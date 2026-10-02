"""
ALPHA CUBE STAGE

Processes all candidate pairs in parallel via joblib to produce
the alpha cube — a dict of {pair_key: DataFrame} with Kalman-filtered
state, half-life, Hurst, SPY beta, parametric alpha, and spread returns.

Context produced
----------------
alpha_cube: dict[tuple[str,str], pd.DataFrame]
"""

from joblib import Parallel, delayed

from modules import PipelineStage
from modules.processing.pair_processor import process_pair_history_fast


class AlphaCubeStage(PipelineStage):
    """Stage 3: parallel pair processing → alpha cube."""

    def run(self, ctx: dict) -> None:
        # In multifactor mode the signal is computed on the factor-neutral
        # residual log-price panel; otherwise on raw prices.
        dag = ctx.get("trade_dag_df", ctx["dag_df"])
        m_ret = ctx["market_ret"]
        candidates = ctx["candidate_pairs"]

        dates_idx = dag.index
        m_ret_arr = m_ret.values
        col_map = {c: idx for idx, c in enumerate(dag.columns)}
        dag_vals = dag.values

        # Real prices drive the SPY-beta hedge estimate + realised spread return
        real_dag = ctx["dag_df"]
        real_vals = real_dag.values
        real_col_map = {c: idx for idx, c in enumerate(real_dag.columns)}

        with Parallel(n_jobs=-1, backend="loky") as parallel:
            results = parallel(
                delayed(process_pair_history_fast)(
                    t1, t2,
                    dag_vals[:, col_map[t1]],
                    dag_vals[:, col_map[t2]],
                    m_ret_arr,
                    dates_idx,
                    self.params,
                    real_vals[:, real_col_map[t1]],
                    real_vals[:, real_col_map[t2]],
                )
                for t1, t2 in candidates
            )

        alpha_cube = {pair: df for pair, df in results}
        ctx["alpha_cube"] = alpha_cube

        print(
            f"[AlphaCube] Built alpha cube: {len(alpha_cube)} pairs.",
            flush=True,
        )

class AlphaCubeStageV2(AlphaCubeStage):
    """V2: multi-scale ENSEMBLE signal.

    Blends the Kalman innovation z-score with a rolling-window z of the spread
    (agreement filter). Wider/cleaner dislocations should express earlier and
    more consistently than the raw Kalman z.
    """

    def run(self, ctx: dict) -> None:
        self.params["signal_ensemble_window"] = int(
            self.params.get("ensemble_window", self.params.get("signal_ensemble_window", 21))
        )
        self.params["signal_ensemble_weight"] = float(
            self.params.get("ensemble_weight", self.params.get("signal_ensemble_weight", 0.5))
        )
        print(
            f"[AlphaCubeV2] ensemble z enabled "
            f"(window={self.params['signal_ensemble_window']}, "
            f"weight={self.params['signal_ensemble_weight']})",
            flush=True,
        )
        super().run(ctx)


class AlphaCubeStageV3(AlphaCubeStage):
    """V3: REGIME-SCALED signal.

    The same |Z| means more when cross-sectional dispersion is high (mean
    reversion works) and less when it is low (trending/no-edge). We compute a
    trailing dispersion regime factor (z-scored over a rolling window) and scale
    each pair's Z_Spread by it, so entries concentrate in favourable regimes.
    """

    def run(self, ctx: dict) -> None:
        # V1 signal (keeps V2's ensemble OFF unless enabled)
        self.params.setdefault("signal_ensemble_window", 0)
        super().run(ctx)

        import numpy as _np
        dag = ctx["dag_df"]
        ret = dag.pct_change().replace([_np.inf, -_np.inf], _np.nan)
        disp = ret.std(axis=1).rolling(int(self.params.get("regime_disp_window", 21)),
                                       min_periods=10).mean()
        look = int(self.params.get("regime_zscore_window", 252))
        dz = (disp - disp.rolling(look, min_periods=60).mean()) / \
             disp.rolling(look, min_periods=60).std().replace(0, _np.nan)
        dz = dz.fillna(0.0)
        beta = float(self.params.get("regime_scale_beta", 0.5))
        lo = float(self.params.get("regime_scale_low", 0.5))
        hi = float(self.params.get("regime_scale_high", 2.0))
        scale = (_np.clip(1.0 + beta * dz.values, lo, hi))
        print(f"[AlphaCubeV3] regime scaling enabled (beta={beta}, bounds=[{lo},{hi}], "
              f"mean scale={scale.mean():.3f})", flush=True)
        for p, df in ctx["alpha_cube"].items():
            df["Z_Spread"] = _np.clip(df["Z_Spread"].values * scale, -5.0, 5.0)


class AlphaCubeStageV4(AlphaCubeStage):
    """V4: OU-IMPLIED expected-reversion signal.

    Raw |Z| ignores how much of the reversion can actually be harvested within
    the holding window. V4 scales the dislocation by the OU fraction of the
    deviation that decays over a fixed horizon H:
        f = 1 - exp(-H / half_life)
    and uses z * f as the traded signal, so the same |Z| means more for pairs
    whose half-life lets them revert inside the horizon. F = 1 -> V1 behaviour.
    """

    def run(self, ctx: dict) -> None:
        self.params.setdefault("signal_ensemble_window", 0)
        super().run(ctx)
        import numpy as _np
        H = float(self.params.get("ou_horizon_days", 10.0))
        floor = float(self.params.get("ou_frac_floor", 0.0))
        blend = float(self.params.get("ou_blend", 1.0))   # 1=full OU, 0=V1
        print(f"[AlphaCubeV4] OU-implied signal (horizon={H}d, blend={blend})", flush=True)
        for p, df in ctx["alpha_cube"].items():
            hl = df["Half_Life"].values.astype(float)
            f = 1.0 - _np.exp(-H / _np.maximum(hl, 1e-6))
            f = _np.clip(f, floor, 1.0)
            z = df["Z_Spread"].values * ((1.0 - blend) + blend * f)
            df["Z_Spread"] = _np.clip(z, -5.0, 5.0)
