"""
HURST EXPONENT of a spread — independent module.

H < 0.5 : mean-reverting (favourable for pairs)
H ~ 0.5 : random walk
H > 0.5 : persistent / trending

Estimator: variance-of-differences.  tau(lag) = std(s[lag:] - s[:-lag]);
log tau = H log lag + c  ->  slope = H.

Note it measures STOCHASTIC persistence: a deterministic linear drift has
constant lag-differences (zero variance), so H is ~0 there and Hurst is not a
trend detector.
"""
from __future__ import annotations
import numpy as np


def hurst_exponent(s, max_lag: int = 20) -> float:
    s = np.asarray(s, dtype=float)
    n = len(s)
    hi = min(max_lag, max(3, n // 2))
    lags = np.arange(2, hi)
    if len(lags) < 3:
        return np.nan
    tau = np.array([np.std(s[lag:] - s[:-lag]) for lag in lags])
    ok = tau > 0
    if ok.sum() < 3:
        return np.nan
    return float(np.polyfit(np.log(lags[ok]), np.log(tau[ok]), 1)[0])


def rolling_hurst(s, window: int = 126, max_lag: int = 20):
    import pandas as pd
    s = pd.Series(np.asarray(s, dtype=float))
    return s.rolling(window, min_periods=max(30, window // 3)).apply(
        lambda v: hurst_exponent(v, max_lag), raw=True)
