"""
START-INVARIANCE GATE

Acceptance test for alpha soundness: a strategy is only "sound" if its outcome
does not depend on the start date. We measure the distribution of horizon
(e.g. 2y) returns across many start dates and require:
  * fraction of positive windows  >= min_pos_frac  (default 0.70)
  * median window return          >= min_median    (default +5%)

Two modes:
  * rolling(returns, horizon): overlapping windows from ONE run. Fast proxy.
  * fresh_starts(...): re-run the pipeline from each start (true test; used by
    scripts/start_invariance.py).
"""

from __future__ import annotations
from typing import Dict, List

import numpy as np
import pandas as pd


def window_stats(windows: List[float], min_pos_frac: float = 0.70,
                 min_median: float = 0.05) -> Dict:
    w = np.asarray(windows, dtype=float)
    if len(w) == 0:
        return {"n": 0, "pass": False}
    stats = {
        "n": int(len(w)),
        "min": float(w.min()),
        "p25": float(np.percentile(w, 25)),
        "median": float(np.median(w)),
        "p75": float(np.percentile(w, 75)),
        "max": float(w.max()),
        "pos_frac": float((w > 0).mean()),
        "ge10_frac": float((w >= 0.10).mean()),
        "min_pos_frac": min_pos_frac,
        "min_median": min_median,
    }
    stats["pass"] = stats["pos_frac"] >= min_pos_frac and stats["median"] >= min_median
    return stats


def rolling(returns: pd.Series, horizon: int = 504) -> List[float]:
    r = returns.astype(float).fillna(0.0)
    cum = (1.0 + r).cumprod()
    roll = (cum / cum.shift(horizon) - 1.0).dropna()
    return roll.tolist()


def format_stats(name: str, s: Dict) -> str:
    if s.get("n", 0) == 0:
        return f"{name}: (no windows)"
    verdict = "PASS" if s["pass"] else "FAIL"
    return (f"{name}: n={s['n']} min={s['min']*100:+.1f}% med={s['median']*100:+.1f}% "
            f"max={s['max']*100:+.1f}% pos={s['pos_frac']*100:.0f}% "
            f">=10%={s['ge10_frac']*100:.0f}%  [{verdict} "
            f"(need pos>={s['min_pos_frac']*100:.0f}% & med>={s['min_median']*100:+.0f}%)]")
