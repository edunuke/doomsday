"""
VECTORIZED MATH KERNELS & STATISTICAL UTILITIES

Stateless numerical functions used across the StatArb pipeline.
All functions operate on NumPy arrays or Pandas Series and avoid
Python loops in hot paths.
"""

import numpy as np
import pandas as pd


def compute_sortino_ratio(returns: pd.Series, target_return: float = 0.0) -> float:
    """Annualised Sortino ratio (downside deviation)."""
    if len(returns) < 5 or returns.std() < 1e-8:
        return 0.0
    downside = returns[returns < target_return]
    if len(downside) < 2:
        return 0.0
    downside_std = np.sqrt(np.mean(downside ** 2)) * np.sqrt(252.0)
    return float((returns.mean() * 252.0) / max(downside_std, 1e-6))


def rolling_ecdf_fast(arr: np.ndarray, window: int = 63) -> np.ndarray:
    """Rolling empirical CDF rank (sliding window)."""
    n = len(arr)
    out = np.zeros(n)
    for i in range(n):
        start = max(0, i - window + 1)
        out[i] = np.sum(arr[start:i + 1] <= arr[i]) / (i - start + 1)
    return out


def get_rolling_half_life(spread: pd.Series, window: int = 126) -> pd.Series:
    """Half-life of mean reversion from AR(1) coefficient on spread."""
    s_lag = spread.shift(1)
    s_diff = spread.diff()
    var_x = s_lag.rolling(window).var()
    cov_xy = s_lag.rolling(window).cov(s_diff)
    gamma = cov_xy / np.maximum(var_x, 1e-8)
    gamma = np.clip(gamma, -0.9999, -1e-5)
    hl = -np.log(2.0) / np.log(1.0 + gamma)
    return hl


def get_rolling_hurst(spread: pd.Series, window: int = 126, max_lag: int = 20) -> pd.Series:
    """Rolling Hurst exponent via rescaled variance of lagged differences."""
    lags = np.arange(2, max_lag)
    log_lags = np.log(lags)
    tau_dict = {lag: spread.diff(lag).rolling(window).std() for lag in lags}
    tau_df = pd.DataFrame(tau_dict)
    log_tau = np.log(np.maximum(tau_df, 1e-8))
    mean_x, var_x = np.mean(log_lags), np.var(log_lags)
    mean_y = log_tau.mean(axis=1)
    cov_xy = ((log_tau.subtract(mean_y, axis=0)) * (log_lags - mean_x)).mean(axis=1)
    return (cov_xy / var_x) * 2.0


def batch_kalman_filter_numpy(
    y: np.ndarray, x: np.ndarray, delta: float, obs_noise: float
):
    """Online Kalman filter for y = intercept + beta * x.

    Returns (betas, intercepts, innovations, F_arr).
    Uses a random-walk state transition: Q = (delta/(1-delta)) * I.
    """
    N = len(y)
    betas = np.zeros(N)
    intercepts = np.zeros(N)
    innovations = np.zeros(N)
    F_arr = np.zeros(N)

    state_mean = np.zeros(2)
    state_cov = np.eye(2)
    Q = (delta / (1.0 - delta)) * np.eye(2)

    for t in range(N):
        H = np.array([1.0, x[t]])
        pred_cov = state_cov + Q
        v = y[t] - np.dot(H, state_mean)
        F = np.dot(H, np.dot(pred_cov, H)) + obs_noise
        K = np.dot(pred_cov, H) / F
        state_mean += K * v
        state_cov = pred_cov - np.outer(K, np.dot(H, pred_cov))
        intercepts[t], betas[t], innovations[t], F_arr[t] = (
            state_mean[0], state_mean[1], v, F
        )

    return betas, intercepts, innovations, F_arr


def compute_in_sample_residualization(
    dag_df: pd.DataFrame, spy_series: pd.Series, lookback_days: int
) -> pd.DataFrame:
    """OLS-residualise every stock against SPY (in-sample, then full)."""
    train_dag = dag_df.iloc[:lookback_days]
    train_spy = spy_series.iloc[:lookback_days]
    X_train = np.column_stack([np.ones(len(train_spy)), np.log(train_spy.values)])
    X_inv = np.linalg.pinv(X_train.T @ X_train) @ X_train.T
    Y_train_log = np.log(train_dag.values)
    betas_fitted = X_inv @ Y_train_log

    X_full = np.column_stack([np.ones(len(spy_series)), np.log(spy_series.values)])
    Y_full_log = np.log(dag_df.values)
    Y_res_full = Y_full_log - (X_full @ betas_fitted)
    return pd.DataFrame(Y_res_full, index=dag_df.index, columns=dag_df.columns)


def compute_multifactor_residualization(
    dag_df: pd.DataFrame,
    spy_series: pd.Series,
    sectors: pd.Series,
    lookback_days: int,
    mom_window: int = 252,
    mom_skip: int = 21,
) -> pd.DataFrame:
    """Residualise returns against MARKET + SECTOR + MOMENTUM factors.

    Each stock's daily return is regressed on the market (SPY), its own GICS
    sector equal-weight return, and a cross-sectional momentum factor
    (top-quintile minus bottom-quintile 12-1 momentum). Betas are fitted on the
    in-sample lookback window and applied across the full series. The residual
    returns are cumulated into a residual log-price level panel, which replaces
    the raw prices wherever the signal is computed.

    Returns
    -------
    pd.DataFrame — factor-neutral residual log-price levels (same shape).
    """
    logp = np.log(dag_df)
    ret = logp.diff()
    ret.iloc[0] = 0.0
    mkt = np.log(spy_series).diff().fillna(0.0).values

    cols = list(dag_df.columns)
    sec_arr = np.array([sectors.get(c, "Unknown") for c in cols])

    # Sector factor returns (equal-weight within sector)
    sec_ret = {}
    for s in np.unique(sec_arr):
        idx = np.where(sec_arr == s)[0]
        sec_ret[s] = ret.iloc[:, idx].mean(axis=1).fillna(0.0).values
    sec_full = np.column_stack([sec_ret[s] for s in sec_arr])  # T x N

    # Cross-sectional momentum factor (12-1): long top quintile, short bottom
    mom = logp.shift(mom_skip) - logp.shift(mom_window)
    mom_rank = mom.rank(axis=1, pct=True)
    mom_f = (
        ret.where(mom_rank >= 0.8).mean(axis=1)
        - ret.where(mom_rank <= 0.2).mean(axis=1)
    ).fillna(0.0).values

    T = len(dag_df)
    lb = min(lookback_days, T)
    Y = ret.values
    res = np.zeros_like(Y)

    for j in range(Y.shape[1]):
        X = np.column_stack([np.ones(T), mkt, sec_full[:, j], mom_f])
        X_lb, y_lb = X[:lb], Y[:lb, j]
        beta = np.linalg.pinv(X_lb.T @ X_lb) @ (X_lb.T @ y_lb)
        res[:, j] = Y[:, j] - X @ beta

    res[0, :] = 0.0
    # ``res`` are residual LOG-RETURNS. Cumulate to a log-level, then
    # exponentiate so the panel is PRICE-SCALE (strictly positive). The
    # downstream Kalman stage takes np.log() of this panel to recover the
    # factor-neutral log-level spread; returning the raw cumsum would cause a
    # double-log and NaNs for every non-positive value.
    res_price = np.exp(np.cumsum(res, axis=0))
    return pd.DataFrame(res_price, index=dag_df.index, columns=dag_df.columns)