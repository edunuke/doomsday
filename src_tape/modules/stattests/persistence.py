"""
PERSISTENCE — mean-crossing count of a spread over its window.

A cointegrated pair that is tradeable must have reverted repeatedly, not merely
passed a statistical test once. Counting zero-crossings of the de-meaned spread
is the cheapest robust persistence proxy: a trending/spurious "pair" crosses
rarely; a genuinely mean-reverting spread crosses often.

Config knobs (screener v5):
    min_mean_crossings : int — minimum crossings over the screen window.
"""
from __future__ import annotations
import numpy as np


def mean_crossings(s) -> int:
    """Number of sign changes of the de-meaned series (int)."""
    s = np.asarray(s, dtype=float)
    if s.size < 3:
        return 0
    d = s - np.nanmean(s)
    d = d[np.isfinite(d)]
    if d.size < 3:
        return 0
    return int(np.sum(d[1:] * d[:-1] < 0.0))


def rolling_mean_crossings(s, window: int = 252):
    import pandas as pd
    ser = pd.Series(np.asarray(s, dtype=float))

    def _mc(v) -> float:
        return float(mean_crossings(v))

    return ser.rolling(window, min_periods=max(20, window // 3)).apply(_mc, raw=True)
