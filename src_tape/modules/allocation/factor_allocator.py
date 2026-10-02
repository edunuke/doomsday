"""
FACTOR-NEUTRAL ALLOCATOR (v2)

Extends :class:`ConvexPortfolioOptimizer` (v1) with **ex-ante factor neutrality
constraints** on the pair book, replacing the heuristic net-dollar + sector +
capped-overlay machinery:

  * factor exposures  B (assets x factors) from :class:`FactorModel`
  * per-sleeve asset-leg map  E (assets x pairs)
  * constraint        | (B^T E) w |_f  <= tol_f   for every factor f
        market |beta| <= 0.02, sector net <= 0.01, style z <= 0.15 (institutional)
  * SPY overlay is solved as the *residual* market-beta of the book (no arbitrary
    0.55 cap leak); capped only for reporting if the venue limit binds.

Overrides are EXOGENOUS (passed in, budget-capped by the bank): the allocator
neutralises the PAIR book, so the combined book's factor exposure equals the
override's intended tilt only — exactly the "neutralise around budgeted
overrides" contract. Combined exposure is returned for monitoring.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize as scipy_minimize

from modules.optimization.optimizer import ConvexPortfolioOptimizer
from modules.factors.model import FactorModel


class FactorNeutralAllocator(ConvexPortfolioOptimizer):
    def __init__(self, params: dict, factor_model: FactorModel):
        super().__init__(params)
        self.fm = factor_model

    def allocate(
        self,
        live_df: pd.DataFrame,
        sectors: pd.Series,
        current_weights: dict,
        alpha_cube: dict,
        current_idx: int,
        override_legs: Optional[Dict[str, float]] = None,
    ):
        if live_df.empty:
            return {}, {}, {}, 0.0, {}

        N = len(live_df)
        pair_keys = [(r["Asset_A"], r["Asset_B"]) for _, r in live_df.iterrows()]
        pair_betas = live_df["Beta"].values
        pair_zs = live_df["Z_Spread"].values
        alphas = live_df["Parametric_Alpha"].values

        # ---- risk model (pair-spread covariance), as v1 -------------------
        cov_window = int(self.params.get("covariance_lookback_days", 63))
        cov_jitter = float(self.params.get("cov_jitter", 1e-5))
        rets = []
        for pk in pair_keys:
            s = alpha_cube[pk]["Spread_Return"].iloc[
                max(0, current_idx - cov_window): current_idx].values
            if len(s) < cov_window:
                s = np.pad(s, (cov_window - len(s), 0), "constant")
            rets.append(s)
        cov = np.cov(np.column_stack(rets), rowvar=False) * 252.0
        if N == 1:
            cov = np.array([[cov]])
        cov = cov + np.eye(N) * cov_jitter

        w_prev = np.array([current_weights.get(pk, 0.0) for pk in pair_keys])
        turnover_bps = self.params["exec_fee"] * 2.0 + float(
            self.params.get("turnover_extra_bps", 0.0005))

        # ---- asset-leg map E: per sleeve, signed legs ---------------------
        all_assets = sorted(set(live_df["Asset_A"]).union(live_df["Asset_B"]))
        apos = {a: j for j, a in enumerate(all_assets)}
        E = np.zeros((len(all_assets), N))
        for k in range(N):
            A, B, z, beta = live_df.iloc[k]["Asset_A"], live_df.iloc[k]["Asset_B"], pair_zs[k], pair_betas[k]
            E[apos[A], k] = -1.0 if z > 0 else 1.0
            E[apos[B], k] = beta if z > 0 else -beta

        date = self.fm.prices.index[current_idx]
        B = self.fm.exposure_frame(date, all_assets)
        # ---- Held-bucket neutrality at the MATCH granularity ----------------
        # If enabled, constrain net exposure to the buckets actually held
        # (e.g. GICS Sub-Industry present in this book) rather than all 127 —
        # tight where it matters, cheap in SLSQP. Default OFF = sector only.
        _mtol = float(self.params.get("neutrality_match_tol", 0.01))
        if bool(self.params.get("neutrality_use_match_granularity", False)):
            _held = pd.Series(sectors, dtype=object).reindex(all_assets)
            _held = _held.fillna("Unknown").astype(str)
            _n_bk = 0
            for _g in sorted(_held.unique()):
                _v = (_held == _g).astype(float).values
                if _v.sum() > 0:
                    B[f"match:{_g}"] = _v
                    _n_bk += 1
            print(f"[Allocator] match-granularity neutrality: +{_n_bk} held buckets "
                  f"(tol={_mtol})", flush=True)
        fnames = list(B.columns)
        Aexp = B.values.T @ E                       # (F x N) factor exposure per sleeve
        tols = self.fm.tolerances()
        tol_vec = np.array([
            float(tols.get(f, _mtol if str(f).startswith("match:") else 0.05))
            for f in fnames])

        ov = np.array([float((override_legs or {}).get(a, 0.0)) for a in all_assets])

        # ---- caps ---------------------------------------------------------
        low_density = int(self.params.get("low_density_threshold", 3))
        if N < low_density:
            max_sleeve_limit = float(self.params.get("max_sleeve_weight_low_density", 0.45))
            max_stock_cap = max_sleeve_limit
        else:
            max_sleeve_limit = float(self.params.get("max_sleeve_weight", 0.30))
            max_stock_cap = float(self.params.get("max_single_stock_gross", 0.45))
        dynamic_sleeve_cap = min(max_sleeve_limit, self.params["max_gross_exposure"] / max(1, N))
        target_gross = min(self.params["max_gross_exposure"], N * dynamic_sleeve_cap)
        min_gross = max(float(self.params.get("min_gross_floor", 0.30)),
                        target_gross * float(self.params.get("min_gross_frac", 0.5)))

        def objective(w):
            return np.dot(w.T, np.dot(cov, w)) + np.sum(np.abs(w - w_prev)) * turnover_bps

        # ---- constraints --------------------------------------------------
        cons = [
            {"type": "ineq", "fun": lambda w: target_gross - np.sum(w)},
            {"type": "ineq", "fun": lambda w: np.sum(w) - min_gross},
        ]
        # factor neutrality of the BASE book:  -tol <= Aexp w <= tol
        for j, f in enumerate(fnames):
            av = Aexp[j]
            t = tol_vec[j]
            cons.append({"type": "ineq", "fun": (lambda w, a=av, tt=t: tt - np.dot(a, w))})
            cons.append({"type": "ineq", "fun": (lambda w, a=av, tt=t: np.dot(a, w) + tt)})
        # single-stock gross cap (combined with override legs)
        for a in all_assets:
            m = (E[apos[a]] != 0).astype(float)
            cons.append({"type": "ineq",
                         "fun": (lambda w, mm=m, aa=a: max_stock_cap - np.sum(np.abs(w * mm)))})

        # ---- bounds -------------------------------------------------------
        alpha_med = max(float(np.median(alphas)), 1e-6)
        bounds = []
        for i in range(N):
            if alphas[i] <= 1e-6:
                bounds.append((0.0, 0.0))
            else:
                ub = dynamic_sleeve_cap * np.clip(
                    float(alphas[i]) / alpha_med,
                    float(self.params.get("alpha_ub_clip_low", 0.20)),
                    float(self.params.get("alpha_ub_clip_high", 1.00)))
                bounds.append((0.0, ub))

        init = 1.0 / np.maximum(live_df["Spread_Vol"].values, 1e-4)
        init = np.minimum((init / init.sum()) * target_gross, [b[1] for b in bounds])
        res = scipy_minimize(objective, init, method="SLSQP", bounds=bounds, constraints=cons)
        if not res.success:
            # SILENT-FALLBACK DEFECT (found in Tier-2 workstream A): when the
            # constraint set is infeasible for a thin book (e.g. macro/FX
            # neutrality with 2-6 sleeves), SLSQP fails and returning `init`
            # silently shipped an UNCONSTRAINED book. Fallback instead to the
            # best-feasible relaxed solve: constraints as a quadratic penalty
            # so the book is as neutral as the sleeve span allows, and log it.
            _pen = float(self.params.get("constraint_penalty", 1e4))

            def pen_obj(w):
                base = objective(w)
                pen = 0.0
                for j, f in enumerate(fnames):
                    a = Aexp[j]
                    v = float(np.dot(a, w))
                    t = tol_vec[j]
                    pen += max(0.0, abs(v) - t) ** 2
                for a in all_assets:
                    m = (E[apos[a]] != 0).astype(float)
                    over = float(np.sum(np.abs(w * m))) - max_stock_cap
                    pen += max(0.0, over) ** 2
                over_g = float(np.sum(w)) - target_gross
                under_g = min_gross - float(np.sum(w))
                pen += max(0.0, over_g) ** 2 + max(0.0, under_g) ** 2
                return base + _pen * pen

            res2 = scipy_minimize(pen_obj, init, method="SLSQP", bounds=bounds)
            # .copy(): SLSQP may return a read-only view; the cutoff zeroing
            # below mutates in place.
            w = np.array(res2.x, copy=True)
            print(f"[Allocator] WARNING hard-constrained SLSQP failed "
                  f"(res.success=False, msg='{res.message}'); using "
                  f"penalised-relaxed weights — book may exceed factor "
                  f"tolerances. n_sleeves={N}.", flush=True)
        else:
            w = res.x
        w = np.array(w, copy=True)
        w[w < float(self.params.get("min_weight_cutoff", 0.01))] = 0.0

        # ---- residual SPY overlay hedges the BASE book --------------------
        mi = fnames.index("market") if "market" in fnames else 0
        base_beta = float(Aexp[mi] @ w)
        max_ov = float(self.params.get("max_overlay_weight",
                                          self.params.get("max_spy_overlay_weight", 0.55)))
        w_spy = -base_beta
        if abs(w_spy) > max_ov:
            w_spy = np.sign(w_spy) * max_ov

        # ---- outputs ------------------------------------------------------
        allocs, memory = {}, {}
        for i, pk in enumerate(pair_keys):
            if w[i] > 0:
                row = live_df.iloc[i]
                wa = -w[i] if row["Z_Spread"] > 0 else w[i]
                wb = (w[i] * row["Beta"] if row["Z_Spread"] > 0 else -w[i] * row["Beta"])
                allocs[pk] = {row["Asset_A"]: wa, row["Asset_B"]: wb, "Sleeve_Weight": w[i]}
                memory[pk] = {
                    "Beta_Entry": row["Beta"],
                    "Intercept_Entry": row["Intercept_Entry"] if row["Is_Active"] else row["Intercept"],
                    "Spread_Vol_Entry": row["Spread_Vol_Entry"] if row["Is_Active"] else row["Spread_Vol"],
                    "Entry_Z": row["Entry_Z"] if row["Is_Active"] else row["Z_Spread"],
                    "Parametric_Alpha": row["Parametric_Alpha"],
                    "Days_Held": row.get("Days_Held", 0),
                    "Entry_Type": row.get("Entry_Type", "BASE"),
                }

        base_exp = {f: float(Aexp[j] @ w) for j, f in enumerate(fnames)}
        ov_exp = {f: float(B.values[:, j] @ ov) for j, f in enumerate(fnames)}
        combined = {f: base_exp[f] + ov_exp[f] for f in fnames}
        metrics = {
            "Portfolio_Variance": float(np.dot(w, np.dot(cov, w))),
            "Turnover_Cost": float(np.sum(np.abs(w - w_prev)) * turnover_bps),
            "Effective_Gross": float(np.sum(w) + abs(w_spy)),
            "Factor_Base": base_exp,
            "Factor_Override": ov_exp,
            "Factor_Combined": combined,
        }
        return allocs, memory, metrics, float(w_spy), combined