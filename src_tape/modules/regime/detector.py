"""
REGIME DETECTOR — independent, causal regime/change-point detection.

Three interchangeable detectors (select via ``components.regime``):

v1 QuantileRegimeDetector
    "double quantile" instability signal. WARNING: structurally broken for
    equity returns (fat left tail makes |q_low|/q_high > 1 permanently).

v2 MarkovRegimeDetector
    statsmodels Markov-switching (2-state) on market returns. Heavier; optional.

v3 VolatilityRegimeDetector
    Rolling realised-vol percentile. Stressed when trailing vol exceeds
    ``regime_vol_percentile``-th percentile of its own history. This is the
    standard stat-arb regime filter: reduce exposure when vol spikes.

All expose ``states(index) -> pd.Series`` (0 = calm, 1 = stressed) and
``changes(index) -> pd.Series`` (bool, state transition at t).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


class QuantileRegimeDetector:
    def __init__(self, params: dict):
        self.params = params
        self.window = int(params.get("regime_window", 63))
        self.q_low = float(params.get("regime_q_low", 0.05))
        self.q_high = float(params.get("regime_q_high", 0.25))
        self.thr = float(params.get("regime_ratio_threshold", 1.6))

    def signal(self, market_ret: pd.Series) -> pd.Series:
        r = market_ret.astype(float).fillna(0.0)
        w = self.window
        # rolling lower/upper quantiles via strided windows (causal: window ends at t)
        from numpy.lib.stride_tricks import sliding_window_view
        vals = r.values
        n = len(vals)
        lo = np.full(n, np.nan)
        hi = np.full(n, np.nan)
        if n >= w:
            win = sliding_window_view(vals, w)              # (n-w+1, w)
            lo[w - 1:] = np.quantile(win, self.q_low, axis=1)
            hi[w - 1:] = np.quantile(win, self.q_high, axis=1)
        ratio = np.abs(lo) / np.where(np.abs(hi) > 1e-9, np.abs(hi), np.nan)
        stressed = pd.Series((ratio > self.thr).astype(float), index=r.index)
        stressed[~np.isfinite(ratio)] = 0.0
        return stressed.fillna(0.0)

    def states(self, market_ret: pd.Series) -> pd.Series:
        return self.signal(market_ret).astype(int)


class MarkovRegimeDetector:
    def __init__(self, params: dict):
        self.params = params
        self.refit = int(params.get("regime_refit_days", 63))
        self.window = int(params.get("regime_sw_window", 504))
        self._fallback = QuantileRegimeDetector(params)

    def states(self, market_ret: pd.Series) -> pd.Series:
        try:
            from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression
        except Exception:
            return self._fallback.states(market_ret)
        r = market_ret.astype(float).fillna(0.0)
        out = pd.Series(0, index=r.index)
        try:
            for end in range(self.window, len(r) + 1, self.refit):
                y = r.iloc[max(0, end - self.window):end]
                res = MarkovRegression(y.values, k_regimes=2, trend="c", switching_variance=True).fit(disp=False)
                # FILTERED probabilities are causal (use only past+current);
                # smoothed probabilities use the whole sample and would leak.
                fp = res.filtered_marginal_probabilities
                means = res.params[:2]
                stressed = int(np.argmin(means))
                seg = pd.Series((fp[:, stressed] > 0.5).astype(int),
                                index=y.index[-min(self.refit, len(y)):])
                out.loc[seg.index] = seg.values
        except Exception:
            return self._fallback.states(market_ret)
        return out


class VolatilityRegimeDetector:
    """Trailing realised-vol percentile. Standard stat-arb regime filter.

    Stressed when realised vol exceeds its own historical percentile.
    Fully causal: each day's percentile is computed over the lookback window
    up to and including that day.
    """

    def __init__(self, params: dict):
        self.params = params
        self.vol_window = int(params.get("regime_vol_window", 63))
        self.pct_window = int(params.get("regime_vol_lookback", 252))
        self.percentile = float(params.get("regime_vol_percentile", 75.0))

    def signal(self, market_ret: pd.Series) -> pd.Series:
        r = market_ret.astype(float).fillna(0.0)
        # trailing realised vol
        vol = r.rolling(self.vol_window, min_periods=max(20, self.vol_window // 3)).std() * np.sqrt(252.0)
        # expanding percentile of that vol
        stressed = pd.Series(0.0, index=r.index)
        for i in range(self.pct_window, len(vol)):
            hist = vol.iloc[max(0, i - self.pct_window):i].dropna()
            if len(hist) < 20:
                continue
            current = vol.iloc[i]
            if pd.notna(current):
                stressed.iloc[i] = 1.0 if float(current) > float(np.percentile(hist.values, self.percentile)) else 0.0
        return stressed

    def states(self, market_ret: pd.Series) -> pd.Series:
        return self.signal(market_ret).astype(int)


def changes(states: pd.Series) -> pd.Series:
    """True where the regime state changes vs the previous observation."""
    return states.astype(int).diff().fillna(0).ne(0)