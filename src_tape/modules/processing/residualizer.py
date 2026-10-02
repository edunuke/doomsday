"""
RESIDUALIZER STAGE

Market-reference-residualises the DAG, computes the in-sample correlation matrix,
and screens sector-matched candidate pairs for cointegration testing.

Context produced
----------------
start_idx      : int            — lookback window boundary
sectors        : pd.Series      — GICS sector per ticker
res_dag_df     : pd.DataFrame   — market-reference-residualised log-prices
candidate_pairs: list[tuple]    — (ticker_A, ticker_B) pairs passing sector/corr gate
"""

import numpy as np
import os
import pandas as pd

from modules import PipelineStage
from modules.processing.utils import compute_in_sample_residualization
from modules.data.screener import UniverseScreener


class ResidualizerStage(PipelineStage):
    """Stage 2: residualise against the market reference, build sector-matched candidate pool."""

    def run(self, ctx: dict) -> None:
        dag = ctx["dag_df"]
        prices = ctx["prices_full"]
        valid = ctx["valid_tickers"]

        start_idx = min(self.params["lookback_days"], len(prices))
        ctx["start_idx"] = start_idx

        # Sector mapping.
        # `sector_column` selects the granularity used for PAIR MATCHING
        # (GICS Sector or GICS Sub-Industry). The allocator's factor model
        # always uses the true "GICS Sector" to keep neutrality at 11 sectors.
        _col = self.params.get("sector_column", "GICS Sector")
        screener = UniverseScreener(self.params["constituents_path"], _col)
        sectors = screener.get_sectors(valid.tolist())
        ctx["sectors"] = sectors
        ctx["sectors_gics"] = screener.get_sectors(valid.tolist(), column="GICS Sector")
        if _col != "GICS Sector":
            print(f"[Residualizer] pair matching granularity: {_col} "
                  f"({sectors.nunique()} groups)", flush=True)

        # Benchmark series (config-driven reference instrument, e.g. SPY/ILF)
        _mkt_col = ctx.get("market_col") or str(self.params.get("market_ticker", "SPY"))
        spy = (
            prices[_mkt_col]
            if _mkt_col in prices.columns
            else dag.mean(axis=1)
        )

        # SPY-residualised DAG
        res_dag = compute_in_sample_residualization(dag, spy, start_idx)
        ctx["res_dag_df"] = res_dag

        # ---- RESIDUALIZATION MODE ---------------------------------------
        # "spy"         -> trade raw prices (baseline); res_dag used for screening only
        # "multifactor" -> market+sector+momentum residual fed into the SIGNAL
        #                  (+ optional FX/commodity factors via factors_csv)
        mode = self.params.get("residualization_mode", "spy")
        if mode == "multifactor":
            from modules.processing.utils import compute_multifactor_residualization
            extra_factors, factor_map = None, None
            fcsv = self.params.get("factors_csv")
            if fcsv and os.path.exists(fcsv):
                import pandas as _pd
                extra_factors = _pd.read_csv(fcsv, index_col=0, parse_dates=True)
                cmcsv = self.params.get("country_map_csv")
                if cmcsv and os.path.exists(cmcsv):
                    cm = _pd.read_csv(cmcsv).set_index("Symbol")["Country"].to_dict()
                    fx_cols = [c for c in extra_factors.columns
                               if c not in ("copper", "oil")]
                    # Each name loads its own-country FX + the global commodity
                    # factors. Country "" (multi-country names) → globals only.
                    factor_map = {
                        t: ([cm[t]] if cm.get(t) and cm[t] in fx_cols else [])
                           + ["copper", "oil"]
                        for t in dag.columns
                    }
            trade_dag = compute_multifactor_residualization(
                dag, spy, sectors, start_idx,
                mom_window=int(self.params.get("mom_window", 252)),
                mom_skip=int(self.params.get("mom_skip", 21)),
                extra_factors=extra_factors,
                factor_map=factor_map,
            )
            res_dag = trade_dag           # screen on the same residual
            ctx["res_dag_df"] = res_dag
            _fx = " + FX/commodities" if extra_factors is not None else ""
            print(f"[Residualizer] multifactor mode: market+sector+momentum{_fx} "
                  "residual routed into signal.", flush=True)
        else:
            trade_dag = dag
        ctx["trade_dag_df"] = trade_dag

        # === SECTOR-MATCHED CORRELATION SCREEN ==============================
        in_sample = res_dag.iloc[:start_idx]
        corr = np.corrcoef(np.diff(in_sample.values, axis=0), rowvar=False)

        # .to_numpy(dtype=object): pyarrow-backed string columns reject 2-D
        # fancy indexing; a plain object ndarray supports the outer-product
        # equality comparison below.
        sec_arr = sectors.reindex(valid).to_numpy(dtype=object)
        sec_match = sec_arr[:, None] == sec_arr[None, :]
        mask = np.triu(sec_match & (corr >= self.params["min_correlation"]), k=1)

        tickers = valid
        candidates = [(tickers[i], tickers[j]) for i, j in zip(*np.where(mask))]

        # ---- PREDICTIVE-QUALITY PRESELECTION (optional) ----------------
        # If a preselect file exists (produced by pair_scoring.py on pre-OOS
        # history), keep only candidate pairs ranked in the top set. This
        # replaces a purely structural (correlation) screen with one that
        # also requires demonstrated out-of-sample predictive quality.
        import json as _json
        import os as _os
        presel_file = self.params.get("preselect_pairs_file")
        if presel_file and _os.path.exists(presel_file):
            try:
                sel = {tuple(p) for p in _json.load(open(presel_file))["pairs"]}
                kept = [c for c in candidates if c in sel or (c[1], c[0]) in sel]
                print(
                    f"[Residualizer] preselection filter: {len(candidates)} -> "
                    f"{len(kept)} pairs ({presel_file})",
                    flush=True,
                )
                candidates = kept
            except Exception as e:
                print(f"[Residualizer] preselection skipped ({e})", flush=True)

        ctx["candidate_pairs"] = candidates

        print(
            f"[Residualizer] {len(candidates)} sector-matched residual pairs.",
            flush=True,
        )

class ResidualizerStageV2(ResidualizerStage):
    """V2: adds an in-sample VARIANCE-RATIO mean-reversion screen.

    V1 screens candidates on correlation only — a structural gate that says
    nothing about whether the spread actually reverts. V2 additionally computes
    the variance ratio VR(k) = Var(k-step change) / (k * Var(1-step change)) on
    the in-sample residual spread and keeps only pairs with VR <= vr_max
    (VR < 1 => mean-reverting; VR ~ 1 => random walk; VR > 1 => trending).
    """

    def run(self, ctx: dict) -> None:
        super().run(ctx)
        res_dag = ctx["res_dag_df"]
        start_idx = ctx["start_idx"]
        cands = ctx["candidate_pairs"]
        vr_max = float(self.params.get("vr_max", 0.95))
        k = int(self.params.get("vr_horizon", 5))
        ins = res_dag.iloc[:start_idx]
        kept = []
        for a, b in cands:
            x = ins[a].values.astype(float)
            y = ins[b].values.astype(float)
            X = np.column_stack([np.ones(len(x)), x])
            try:
                beta = np.linalg.pinv(X.T @ X) @ (X.T @ y)
            except Exception:
                continue
            spread = y - (beta[0] + beta[1] * x)
            d1 = np.diff(spread)
            if len(d1) < k + 20 or np.var(d1) <= 0:
                continue
            dk = spread[k:] - spread[:-k]
            vr = float(np.var(dk) / (k * np.var(d1)))
            if vr <= vr_max:
                kept.append((a, b))
        ctx["candidate_pairs"] = kept
        print(
            f"[ResidualizerV2] variance-ratio screen ({k}d, VR<={vr_max}): "
            f"{len(cands)} -> {len(kept)} pairs",
            flush=True,
        )


class ResidualizerStageV3(ResidualizerStage):
    """V3: REGIME-CONDITIONAL reversion screen.

    V2's VR screen measured reversion over the *whole* in-sample window — which
    averages across regimes and (like all in-sample quality measures) failed to
    persist. V3 measures reversion ONLY over the in-sample HIGH-DISPERSION days
    (the favourable regime) and keeps pairs that actually revert when the regime
    is easy.
    """

    def run(self, ctx: dict) -> None:
        super().run(ctx)
        dag = ctx["dag_df"]
        start_idx = ctx["start_idx"]
        cands = ctx["candidate_pairs"]
        k = int(self.params.get("vr_horizon", 5))
        vr_max = float(self.params.get("vr_max", 0.95))
        ret = dag.pct_change().replace([np.inf, -np.inf], np.nan)
        disp = ret.std(axis=1).rolling(int(self.params.get("regime_disp_window", 21)),
                                       min_periods=10).mean()
        disp_ins = disp.iloc[:start_idx]
        thr = disp_ins.quantile(float(self.params.get("regime_hi_pctile", 0.5)))
        hi_idx = disp_ins[disp_ins >= thr].index
        res_dag = ctx["res_dag_df"].loc[hi_idx]
        kept = []
        for a, b in cands:
            x = res_dag[a].values.astype(float)
            y = res_dag[b].values.astype(float)
            if len(x) < k + 20:
                continue
            X = np.column_stack([np.ones(len(x)), x])
            try:
                beta = np.linalg.pinv(X.T @ X) @ (X.T @ y)
            except Exception:
                continue
            spread = y - (beta[0] + beta[1] * x)
            d1 = np.diff(spread)
            if len(d1) < k + 10 or np.var(d1) <= 0:
                continue
            dk = spread[k:] - spread[:-k]
            vr = float(np.var(dk) / (k * np.var(d1)))
            if vr <= vr_max:
                kept.append((a, b))
        ctx["candidate_pairs"] = kept
        print(f"[ResidualizerV3] regime-conditional VR screen "
              f"(high-dispersion days={len(hi_idx)}, {k}d, VR<={vr_max}): "
              f"{len(cands)} -> {len(kept)} pairs", flush=True)


class ResidualizerStageV4(ResidualizerStage):
    """V4: BOTH-REGIME reversion screen (more robust than V3).

    V3 required reversion only in HIGH-dispersion days. That is a single-regime
    condition and may select pairs whose apparent reversion is regime-specific
    noise. V4 requires the spread to mean-revert (VR <= vr_max) in BOTH the
    high- and low-dispersion halves of the in-sample window — a structural
    property, not a regime accident.
    """

    def run(self, ctx: dict) -> None:
        super().run(ctx)
        dag = ctx["dag_df"]
        start_idx = ctx["start_idx"]
        cands = ctx["candidate_pairs"]
        k = int(self.params.get("vr_horizon", 5))
        vr_hi = float(self.params.get("vr_max", 0.95))
        vr_lo = float(self.params.get("vr_max_low", 1.00))
        ret = dag.pct_change().replace([np.inf, -np.inf], np.nan)
        disp = ret.std(axis=1).rolling(int(self.params.get("regime_disp_window", 21)),
                                       min_periods=10).mean().iloc[:start_idx]
        thr = disp.quantile(0.5)
        hi_idx = disp[disp >= thr].index
        lo_idx = disp[disp < thr].index
        res = ctx["res_dag_df"]

        def vr_on(idx, a, b):
            s = res.loc[idx]
            x = s[a].values.astype(float); y = s[b].values.astype(float)
            if len(x) < k + 15:
                return None
            X = np.column_stack([np.ones(len(x)), x])
            try:
                beta = np.linalg.pinv(X.T @ X) @ (X.T @ y)
            except Exception:
                return None
            sp = y - (beta[0] + beta[1] * x)
            d1 = np.diff(sp)
            if len(d1) < k + 10 or np.var(d1) <= 0:
                return None
            dk = sp[k:] - sp[:-k]
            return float(np.var(dk) / (k * np.var(d1)))

        kept = []
        for a, b in cands:
            vh = vr_on(hi_idx, a, b)
            vl = vr_on(lo_idx, a, b)
            if vh is None or vl is None:
                continue
            if vh <= vr_hi and vl <= vr_lo:
                kept.append((a, b))
        ctx["candidate_pairs"] = kept
        print(f"[ResidualizerV4] both-regime VR screen "
              f"(hi<={vr_hi} on {len(hi_idx)}d, lo<={vr_lo} on {len(lo_idx)}d): "
              f"{len(cands)} -> {len(kept)} pairs", flush=True)
