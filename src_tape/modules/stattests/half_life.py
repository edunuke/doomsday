"""
HALF-LIFE of mean reversion (AR(1) / Ornstein-Uhlenbeck) and variance ratio —
independent module.

AR(1):  ds_t = a + b * s_{t-1} + e,  phi = 1 + b
        half-life = ln(0.5) / ln(phi)      (exact)  ~ -ln2 / b (small-b approx)

Variance ratio (Lo-MacKinlay): VR<1 mean-reverting, =1 random walk, >1 trending.
"""
from __future__ import annotations
import numpy as np


def half_life_ar1(s) -> float:
    s = np.asarray(s, dtype=float)
    if len(s) < 5:
        return np.inf
    dy = np.diff(s)
    y_lag = s[:-1]
    X = np.column_stack([y_lag, np.ones(len(y_lag))])
    b = np.linalg.lstsq(X, dy, rcond=None)[0][0]
    if b >= 0:
        return np.inf
    phi = 1.0 + b
    if 0.0 < phi < 1.0:
        return float(-np.log(2.0) / np.log(phi))
    return float(-np.log(2.0) / b)


def variance_ratio(s, k: int = 5) -> float:
    s = np.asarray(s, dtype=float)
    if len(s) <= k + 1:
        return 1.0
    var1 = np.var(np.diff(s), ddof=1)
    vark = np.var(s[k:] - s[:-k], ddof=1)
    if var1 == 0:
        return 1.0
    return float(vark / (k * var1))


def rolling_half_life(s, window: int = 126):
    import pandas as pd
    s = pd.Series(np.asarray(s, dtype=float))
    return s.rolling(window, min_periods=max(20, window // 3)).apply(
        lambda v: half_life_ar1(v), raw=True)
