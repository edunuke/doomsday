"""RISK-FREE BENCHMARK (SOFR)

For a market-neutral book the only meaningful benchmark is the risk-free
rate. We use SOFR (compounded daily) and report:

  * Benchmark Return [%]   — compounded SOFR over the period
  * Excess Return [%]      — strategy total - SOFR total
  * Information Ratio      — annualised excess return / tracking error
  * Excess Sharpe          — strategy excess return vs its volatility

SOFR data: data/sofr.csv (observation_date, SOFR in %). Daily accrual is
rate/100/252 (business-day convention).
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np
import pandas as pd

_SOFR_CACHE: Optional[pd.Series] = None
_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "sofr.csv")


def load_sofr(path: str = None) -> pd.Series:
    """Return the SOFR rate series (annualised %), indexed by date."""
    global _SOFR_CACHE
    p = path or _DEFAULT
    if _SOFR_CACHE is not None and path is None:
        return _SOFR_CACHE
    df = pd.read_csv(p)
    df.columns = ["date", "sofr"]
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["sofr"].astype(float).sort_index()
    if path is None:
        _SOFR_CACHE = s
    return s


def sofr_daily_returns(dates: pd.DatetimeIndex, path: str = None) -> pd.Series:
    """Daily SOFR accrual returns aligned (and reindexed/filled) to `dates`."""
    s = load_sofr(path)
    r = (s / 100.0 / 252.0).reindex(dates).ffill().bfill().fillna(0.0)
    r.index = dates
    return r


def benchmark_summary(
    strat_returns: pd.Series,
    rf_returns: pd.Series,
) -> dict:
    """Compute risk-free-relative interpretation metrics.

    strat_returns : daily strategy returns (net)
    rf_returns    : daily risk-free (SOFR) accrual returns, same index
    """
    df = pd.concat([strat_returns.rename("s"), rf_returns.rename("rf")],
                   axis=1).dropna()
    if df.empty:
        return {}
    n = len(df)
    s_total = float((1 + df.s).prod() - 1)
    rf_total = float((1 + df.rf).prod() - 1)
    excess = df.s - df.rf
    ann_excess = float(excess.mean() * 252)
    te = float(excess.std() * np.sqrt(252))
    ir = float(ann_excess / te) if te > 0 else 0.0
    ann_strat = float((1 + s_total) ** (252.0 / n) - 1)
    ann_rf = float((1 + rf_total) ** (252.0 / n) - 1)
    return {
        "Benchmark Return [%]": rf_total * 100,
        "Excess Return [%]": (s_total - rf_total) * 100,
        "Annualised Return [%]": ann_strat * 100,
        "Annualised SOFR [%]": ann_rf * 100,
        "Annualised Excess [%]": ann_excess * 100,
        "Tracking Error [%]": te * 100,
        "Information Ratio": ir,
        "Excess Sharpe": float(ann_excess / (df.s.std() * np.sqrt(252))) if df.s.std() > 0 else 0.0,
        "Beta vs SOFR": float(np.polyfit(df.rf, df.s, 1)[0]) if df.rf.std() > 0 else 0.0,
    }


def print_benchmark_block(summary: dict, label: str = "RISK-FREE (SOFR) BENCHMARK") -> None:
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)
    print(f"{'Metric':<28}{'Value':>16}")
    print("-" * 80)
    for k, v in summary.items():
        if "Ratio" in k:
            print(f"{k:<28}{v:>16.3f}")
        else:
            print(f"{k:<28}{v:>15.2f}%")
    print("=" * 80)
