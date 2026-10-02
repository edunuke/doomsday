"""
CONVEX PORTFOLIO OPTIMIZER (SLSQP)

Allocates sleeve weights across candidate pairs subject to:
  - Gross / net dollar exposure constraints
  - Net beta constraint (institutional market-neutral)
  - Sector constraints
  - Single-stock concentration caps
  - Turnover penalty
  - Endogenous beta throttling via market-reference overlay

Key v3.2 change: relaxed beta neutrality (5 % cap) and dollar exposure (8 % cap)
to allow the optimizer meaningful allocation room.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize as scipy_minimize


class ConvexPortfolioOptimizer:
    """SLSQP-based pair allocation with risk, sector, beta & concentration constraints."""

    def __init__(self, params: dict):
        self.params = params

    @staticmethod
    def calculate_max_weight_drift(
        current_sleeve_weights: Dict[Tuple[str, str], float],
        target_sleeve_weights: Dict[Tuple[str, str], float],
    ) -> float:
        """Linfinity-norm drift between current and target sleeve weights."""
        all_keys = set(current_sleeve_weights.keys()).union(target_sleeve_weights.keys())
        if not all_keys:
            return 0.0
        return float(
            max(
                abs(current_sleeve_weights.get(pk, 0.0) - target_sleeve_weights.get(pk, 0.0))
                for pk in all_keys
            )
        )

    def allocate(
        self,
        live_df: pd.DataFrame,
        sectors: pd.Series,
        current_weights: dict,
        alpha_cube: dict,
        current_idx: int,
        override_legs: Optional[Dict[str, float]] = None,
    ):
        """Run SLSQP over the candidate pool.

        ``override_legs`` is accepted for interface parity with the v2
        factor-neutral allocator (walk_forward passes it uniformly). The v1
        allocator does not model override legs and ignores it.

        Returns
        -------
        allocs : dict — {pair_key: {asset_A: w, asset_B: w, Sleeve_Weight: w}}
        memory : dict — per-pair entry metadata
        metrics : dict — Portfolio_Variance, Turnover_Cost, Effective_Gross
        w_mkt_overlay : float — market-reference overlay weight for beta hedge
        """
        if live_df.empty:
            return {}, {}, {}, 0.0

        N = len(live_df)
        pair_keys = [(r["Asset_A"], r["Asset_B"]) for _, r in live_df.iterrows()]

        # Build covariance matrix from N-day spread returns
        cov_window = int(self.params.get("covariance_lookback_days", 63))
        cov_jitter = float(self.params.get("cov_jitter", 1e-5))
        returns_list = []
        for pk in pair_keys:
            s_ret = (
                alpha_cube[pk]["Spread_Return"]
                .iloc[max(0, current_idx - cov_window): current_idx]
                .values
            )
            if len(s_ret) < cov_window:
                s_ret = np.pad(s_ret, (cov_window - len(s_ret), 0), "constant")
            returns_list.append(s_ret)

        returns_mat = np.column_stack(returns_list)
        cov_matrix = np.cov(returns_mat, rowvar=False) * 252.0
        if N == 1:
            cov_matrix = np.array([[cov_matrix]])
        cov_matrix += np.eye(N) * cov_jitter

        pair_betas = live_df["Beta"].values
        pair_spy_betas = live_df["Market_Beta"].values
        pair_zs = live_df["Z_Spread"].values
        alphas = live_df["Parametric_Alpha"].values
        entry_types = live_df.get("Entry_Type", pd.Series(["BASE"] * N)).values

        w_prev_pairs = np.array([current_weights.get(pk, 0.0) for pk in pair_keys])
        turnover_bps = (self.params["exec_fee"] * 2.0) + float(
            self.params.get("turnover_extra_bps", 0.0005)
        )

        # Directional dollar multipliers
        dollar_mults = np.where(pair_zs > 0, pair_betas - 1.0, 1.0 - pair_betas)
        mkt_beta_mults = np.where(pair_zs > 0, -pair_spy_betas, pair_spy_betas)
        max_pair_beta = float(self.params.get("max_pair_market_beta",
            self.params.get("max_pair_spy_beta", 1.20)))
        mkt_beta_mults_clipped = np.clip(mkt_beta_mults, -max_pair_beta, max_pair_beta)

        pair_secs = np.array(
            [sectors.get(live_df.iloc[i]["Asset_A"], "Unknown") for i in range(N)]
        )
        unique_secs = np.unique(pair_secs)

        # ------------------------------------------------------------------
        # Objective
        # ------------------------------------------------------------------
        def objective(w):
            risk = np.dot(w.T, np.dot(cov_matrix, w))
            turnover = np.sum(np.abs(w - w_prev_pairs)) * turnover_bps
            return risk + turnover

        # ------------------------------------------------------------------
        # Sleeve & stock caps
        # ------------------------------------------------------------------
        low_density_threshold = int(self.params.get("low_density_threshold", 3))
        if N < low_density_threshold:
            max_sleeve_limit = float(self.params.get("max_sleeve_weight_low_density", 0.45))
            max_stock_cap = max_sleeve_limit
        else:
            max_sleeve_limit = float(self.params.get("max_sleeve_weight", 0.30))
            max_stock_cap = float(self.params.get("max_single_stock_gross", 0.45))

        dynamic_sleeve_cap = min(
            max_sleeve_limit, self.params["max_gross_exposure"] / max(1, N)
        )
        target_gross = min(self.params["max_gross_exposure"], N * dynamic_sleeve_cap)
        min_gross = max(
            float(self.params.get("min_gross_floor", 0.30)),
            target_gross * float(self.params.get("min_gross_frac", 0.5)),
        )

        # ------------------------------------------------------------------
        # Constraints
        # ------------------------------------------------------------------
        constraints = [
            {"type": "ineq", "fun": lambda w: target_gross - np.sum(w)},
            {"type": "ineq", "fun": lambda w: np.sum(w) - min_gross},
            {
                "type": "ineq",
                "fun": lambda w: self.params["max_net_dollar_exposure"]
                - abs(np.sum(w * dollar_mults)),
            },
        ]
        for s in unique_secs:
            sec_mask = pair_secs == s
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w, m=sec_mask: self.params["max_sector_exposure"]
                    - abs(np.sum(w[m] * dollar_mults[m])),
                }
            )

        all_assets = list(set(live_df["Asset_A"]).union(live_df["Asset_B"]))
        for asset in all_assets:
            asset_mask = np.zeros(N)
            for i, r in live_df.iterrows():
                idx_pos = live_df.index.get_loc(i)
                if r["Asset_A"] == asset:
                    asset_mask[idx_pos] += 1.0
                if r["Asset_B"] == asset:
                    asset_mask[idx_pos] += abs(r["Beta"])
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w, m=asset_mask: max_stock_cap - np.sum(w * m),
                }
            )

        # ------------------------------------------------------------------
        # Bounds
        # ------------------------------------------------------------------
        alpha_median = max(float(np.median(alphas)), 1e-6)
        bounds = []
        for i in range(N):
            if alphas[i] <= 1e-6:
                bounds.append((0.0, 0.0))
            else:
                limit = dynamic_sleeve_cap
                ub = limit * np.clip(
                    float(alphas[i]) / alpha_median,
                    float(self.params.get("alpha_ub_clip_low", 0.20)),
                    float(self.params.get("alpha_ub_clip_high", 1.00)),
                )
                bounds.append((0.0, ub))

        # Initial guess (vol-weighted)
        init_w = 1.0 / np.maximum(live_df["Spread_Vol"].values, 1e-4)
        init_w = np.minimum(
            (init_w / np.sum(init_w)) * target_gross, [b[1] for b in bounds]
        )

        # ------------------------------------------------------------------
        # Solve
        # ------------------------------------------------------------------
        res = scipy_minimize(
            objective, init_w, method="SLSQP", bounds=bounds, constraints=constraints
        )
        best_w = res.x if res.success else init_w
        best_w[best_w < float(self.params.get("min_weight_cutoff", 0.01))] = 0.0

        # ------------------------------------------------------------------
        # Endogenous beta throttling
        # ------------------------------------------------------------------
        max_overlay_cap = float(self.params.get("max_overlay_weight", 0.55))
        net_pair_mkt_beta = np.sum(best_w * mkt_beta_mults_clipped)
        required_spy_hedge = -net_pair_mkt_beta

        if abs(required_spy_hedge) > max_overlay_cap:
            scale = max_overlay_cap / abs(required_spy_hedge)
            best_w *= scale
            w_spy_overlay = np.sign(required_spy_hedge) * max_overlay_cap
        else:
            w_spy_overlay = required_spy_hedge

        # ------------------------------------------------------------------
        # Build output dictionaries
        # ------------------------------------------------------------------
        allocs = {}
        memory = {}
        for i, pk in enumerate(pair_keys):
            if best_w[i] > 0:
                row = live_df.iloc[i]
                w_a = -best_w[i] if row["Z_Spread"] > 0 else best_w[i]
                w_b = (
                    best_w[i] * row["Beta"]
                    if row["Z_Spread"] > 0
                    else -best_w[i] * row["Beta"]
                )
                allocs[pk] = {
                    row["Asset_A"]: w_a,
                    row["Asset_B"]: w_b,
                    "Sleeve_Weight": best_w[i],
                }
                memory[pk] = {
                    "Beta_Entry": row["Beta"],
                    "Intercept_Entry": (
                        row["Intercept_Entry"] if row["Is_Active"] else row["Intercept"]
                    ),
                    "Spread_Vol_Entry": (
                        row["Spread_Vol_Entry"]
                        if row["Is_Active"]
                        else row["Spread_Vol"]
                    ),
                    "Entry_Z": (
                        row["Entry_Z"] if row["Is_Active"] else row["Z_Spread"]
                    ),
                    "Parametric_Alpha": row["Parametric_Alpha"],
                    "Days_Held": row.get("Days_Held", 0),
                    "Entry_Type": entry_types[i],
                }

        metrics = {
            "Portfolio_Variance": np.dot(best_w.T, np.dot(cov_matrix, best_w)),
            "Turnover_Cost": np.sum(np.abs(best_w - w_prev_pairs)) * turnover_bps,
            "Effective_Gross": np.sum(best_w) + abs(w_spy_overlay),
        }

        return allocs, memory, metrics, w_spy_overlay