"""
ORTHOGONAL ALPHA SOURCES (independent, causal).

Three sources that are not the pair Kalman z-score, used to condition/size the
existing pair alpha rather than replace it:

  dispersion_z(prices)      : cross-sectional return dispersion, z-scored.
                              High dispersion => richer pair-reversion payoff.
  sector_resid_z(prices, sectors) : mean-reversion z of each name vs its GICS
                              sector composite (an orthogonal residual).
  ou_scale(half_life, tau)  : OU-implied fraction of a full reversion available
                              within a holding horizon.

All are point-in-time (rolling / cross-sectional at t only).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _cs_z(df: pd.DataFrame) -> pd.DataFrame:
    mu = df.mean(axis=1)
    sd = df.std(axis=1).replace(0.0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0)


def dispersion_series(prices: pd.DataFrame, window: int = 21) -> pd.Series:
    """Dispersion = cross-sectional std of daily returns (causal)."""
    r = prices.pct_change()
    disp = r.std(axis=1)
    return disp.rolling(window, min_periods=max(5, window // 3)).mean()


def dispersion_z(prices: pd.DataFrame, lookback: int = 252, window: int = 21) -> pd.Series:
    d = dispersion_series(prices, window)
    mu = d.rolling(lookback, min_periods=60).mean()
    sd = d.rolling(lookback, min_periods=60).std().replace(0.0, np.nan)
    return ((d - mu) / sd).fillna(0.0)


def sector_resid_z(prices: pd.DataFrame, sectors: pd.Series, window: int = 63) -> pd.DataFrame:
    """Per-name z of cumulative return vs its sector composite (causal)."""
    r = prices.pct_change()
    out = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for s in sectors.dropna().unique():
        cols = [c for c in sectors[sectors == s].index if c in r.columns]
        if len(cols) < 3:
            continue
        comp = r[cols].mean(axis=1)
        resid = r[cols].sub(comp, axis=0)
        cum = resid.rolling(window, min_periods=max(10, window // 3)).sum()
        z = cum.rolling(window, min_periods=max(10, window // 3)).apply(
            lambda v: (v[-1] - v.mean()) / (v.std() or 1.0), raw=True)
        out[cols] = z.fillna(0.0).values
    return out


def ou_scale(half_life: pd.Series, tau: float = 5.0) -> pd.Series:
    """OU-implied fraction of reversion realised within the penalty horizon."""
    hl = half_life.clip(lower=1e-6)
    return 1.0 - np.exp(-hl / float(tau))
