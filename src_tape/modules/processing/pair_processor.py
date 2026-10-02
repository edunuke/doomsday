"""
PAIR HISTORY PROCESSOR

Processes a single candidate pair through Kalman filter,
computes Z-spread, half-life, Hurst, slope t-stat, market betas,
parametric alpha, and pair returns.

Designed for parallel execution via joblib.Parallel.
"""

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from modules.processing.utils import (
    batch_kalman_filter_numpy,
    rolling_ecdf_fast,
    get_rolling_half_life,
    get_rolling_hurst,
)


def process_pair_history_fast(
    t1: str,
    t2: str,
    p1_arr: np.ndarray,
    p2_arr: np.ndarray,
    m_ret_arr: np.ndarray,
    dates_idx: pd.DatetimeIndex,
    params: Dict,
    real_p1_arr: np.ndarray = None,
    real_p2_arr: np.ndarray = None,
) -> Tuple[Tuple[str, str], pd.DataFrame]:
    """Compute full Kalman-filtered history for pair (t1, t2).

    p1_arr/p2_arr drive the SPREAD (signal). In multifactor mode these are
    factor-neutral RESIDUAL log-price levels. real_p1_arr/real_p2_arr, when
    provided, are the ACTUAL traded prices: they drive the market-beta estimate
    used for the market-reference hedge and the realised spread return for the risk
    model — which must reflect real P&L, not the residual.

    Returns ((t1, t2), DataFrame with columns:
        Beta, Intercept, Market_Beta, Slope_TStat, Z_Spread, ECDF_Rank,
        Spread_Vol, Half_Life, Hurst, Parametric_Alpha, Spread_Return)
    indexed by dates_idx.
    """
    y = np.log(p1_arr)
    x = np.log(p2_arr)

    _dates_idx_full = dates_idx
    # ---- Leading-NaN guard (late-listed names) ----------------------------
    # A name listed after the panel start (IPOs, new ADR programs) leaves
    # leading NaNs; the rolling lstsq/Kalman below would raise SVD errors on
    # them. Compute on the joint-valid slice, then pad every output column
    # with leading NaN so the returned frame stays aligned with dates_idx.
    _pad = 0
    _both_finite = np.isfinite(y) & np.isfinite(x)
    if not _both_finite.all():
        if not _both_finite.any():
            empty = pd.DataFrame(index=dates_idx, columns=[
                "Beta", "Intercept", "Market_Beta", "Slope_TStat", "Z_Spread",
                "ECDF_Rank", "Spread_Vol", "Half_Life", "Hurst",
                "Parametric_Alpha", "Spread_Return"], dtype=float)
            return (t1, t2), empty
        _pad = int(np.argmax(_both_finite))
        y, x = y[_pad:], x[_pad:]
        p1_arr, p2_arr = p1_arr[_pad:], p2_arr[_pad:]
        if real_p1_arr is not None:
            real_p1_arr = real_p1_arr[_pad:]
        if real_p2_arr is not None:
            real_p2_arr = real_p2_arr[_pad:]
        if m_ret_arr is not None and len(m_ret_arr) == len(_both_finite):
            m_ret_arr = m_ret_arr[_pad:]
        dates_idx_full = dates_idx
        dates_idx = dates_idx[_pad:]

    betas, intercepts, innovations, F_arr = batch_kalman_filter_numpy(
        y, x, delta=params["kalman_delta"], obs_noise=params["kalman_obs_noise"]
    )

    # Smoothed forecast variance
    F_series = (
        pd.Series(F_arr, index=dates_idx)
        .rolling(
            int(params["kalman_f_smooth_window"]),
            min_periods=int(params["kalman_f_smooth_min_periods"]),
        )
        .mean()
        .bfill()
        .values
    )
    spread_vol = np.sqrt(np.maximum(F_series, 1e-4))
    z_spread = np.clip(innovations / spread_vol, -5.0, 5.0)

    ecdf_ranks = rolling_ecdf_fast(np.abs(innovations), window=int(params["ecdf_window"]))

    spread = pd.Series(y - betas * x, index=dates_idx)

    # ---- OPTIONAL MULTI-SCALE ENSEMBLE SIGNAL (V2) ----------------------
    # Blend the Kalman innovation z with a rolling-window z of the spread.
    # The Kalman z is fast/adaptive but noisy; the rolling z is a stable
    # deviation measure. Averaging the two agreement-filters the signal:
    # a dislocation must be confirmed by BOTH estimators.
    _ew = int(params.get("signal_ensemble_window", 0))
    if _ew > 0:
        _minp = max(5, _ew // 2)
        _rm = spread.rolling(_ew, min_periods=_minp).mean()
        _rs = spread.rolling(_ew, min_periods=_minp).std()
        _zr = ((spread - _rm) / np.maximum(_rs, 1e-8)).clip(-5.0, 5.0).fillna(0.0).values
        _w = float(params.get("signal_ensemble_weight", 0.5))
        z_spread = (1.0 - _w) * np.asarray(z_spread) + _w * _zr
        z_spread = np.clip(z_spread, -5.0, 5.0)
    hl = (
        get_rolling_half_life(spread, window=int(params["half_life_window"]))
        .fillna(float(params["half_life_fillna"]))
        .clip(params["min_half_life"], params["max_half_life"])
    )
    hurst = get_rolling_hurst(
        spread,
        window=int(params["hurst_window"]),
        max_lag=int(params["hurst_max_lag"]),
    ).fillna(float(params["hurst_fillna"]))

    # Trend slope t-stat (configurable window)
    W_slope = int(params["slope_window"])
    t_idx = pd.Series(np.arange(len(spread)), index=dates_idx)
    var_t = float((W_slope ** 2 - 1.0) / 12.0)
    slope_s = spread.rolling(W_slope).cov(t_idx) / var_t
    var_s = spread.rolling(W_slope).var()
    se_slope = np.sqrt(
        np.maximum(1e-8, var_s - (slope_s ** 2 * var_t)) / ((W_slope - 2.0) * var_t)
    )
    slope_tstats = (slope_s / np.maximum(se_slope, 1e-6)).fillna(0.0).values

    # Rolling SPY betas per leg — from REAL prices (hedge must match real book)
    rp1 = real_p1_arr if real_p1_arr is not None else p1_arr
    rp2 = real_p2_arr if real_p2_arr is not None else p2_arr
    ret_A = pd.Series(rp1, index=dates_idx).pct_change().fillna(0.0)
    ret_B = pd.Series(rp2, index=dates_idx).pct_change().fillna(0.0)
    m_ret = pd.Series(m_ret_arr, index=dates_idx).fillna(0.0)
    spy_beta_span = int(params["spy_beta_ewm_span"])
    var_m = m_ret.ewm(span=spy_beta_span).var().fillna(1e-4)
    cov_a_m = ret_A.ewm(span=spy_beta_span).cov(m_ret).fillna(0.0)
    cov_b_m = ret_B.ewm(span=spy_beta_span).cov(m_ret).fillna(0.0)
    beta_a_spy = (cov_a_m / np.maximum(var_m, 1e-6)).values
    beta_b_spy = (cov_b_m / np.maximum(var_m, 1e-6)).values
    pair_spy_betas = beta_a_spy - (betas * beta_b_spy)

    # Parametric alpha
    exp_yield = np.maximum(0.0, np.abs(z_spread) - params["exit_z_spread"]) * spread_vol
    alpha = (
        exp_yield
        * (252.0 / hl.values)
        * (1.0 - np.exp(-hl.values / params["tau_hl_penalty"]))
    )
    pair_returns = (ret_A.values - betas * ret_B.values) / (1.0 + np.abs(betas))

    result_df = pd.DataFrame(
        {
            "Beta": betas,
            "Intercept": intercepts,
            "Market_Beta": pair_spy_betas,
            "Slope_TStat": slope_tstats,
            "Z_Spread": z_spread,
            "ECDF_Rank": ecdf_ranks,
            "Spread_Vol": spread_vol,
            "Half_Life": hl.values,
            "Hurst": hurst.values,
            "Parametric_Alpha": alpha,
            "Spread_Return": pair_returns,
        },
        index=dates_idx,
    )
    # Re-pad leading NaN rows so the frame stays aligned with the FULL
    # panel index (walk_forward indexes alpha[p].iloc[i] positionally).
    if _pad:
        result_df = result_df.reindex(_dates_idx_full)
    return (t1, t2), result_df