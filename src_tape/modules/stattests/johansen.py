"""
JOHANSEN COINTEGRATION TEST (trace / max-eigenvalue) — independent module.

Tests whether two (or more) series share a stationary long-run relationship
using the Johansen VECM procedure (statsmodels), reporting the trace and
max-eigenvalue statistics against 90/95/99% critical values.
"""
from __future__ import annotations
import numpy as np


def johansen_test(*series, det_order: int = 0, k_ar_diff: int = 1) -> dict:
    try:
        from statsmodels.tsa.vector_ar.vecm import coint_johansen
    except Exception as exc:  # pragma: no cover
        return {"error": str(exc)}
    data = np.column_stack([np.asarray(s, dtype=float) for s in series])
    n = data.shape[1]
    res = coint_johansen(data, det_order, k_ar_diff)
    trace, maxeig = res.lr1, res.lr2          # own-stat ordering, index 0 = r=0
    cvt, cvm = res.cvt, res.cvm               # (n x 3) cols = [90,95,99]
    coint95 = bool(trace[0] > cvt[0, 1])
    coint99 = bool(trace[0] > cvt[0, 2])
    rank95 = int(sum(trace[i] > cvt[i, 1] for i in range(n)))
    return {
        "trace_stat": float(trace[0]),
        "trace_cv95": float(cvt[0, 1]),
        "trace_cv99": float(cvt[0, 2]),
        "trace_coint_95": coint95,
        "trace_coint_99": coint99,
        "maxeig_stat": float(maxeig[0]),
        "maxeig_cv95": float(cvm[0, 1]),
        "maxeig_coint_95": bool(maxeig[0] > cvm[0, 1]),
        "rank_95": rank95,
    }


def rolling_johansen(y, x, window: int = 252, step: int = 21):
    """Rolling Johansen trace stat (causal): row t uses only data up to t."""
    import pandas as pd
    y = pd.Series(np.asarray(y, dtype=float))
    x = pd.Series(np.asarray(x, dtype=float))
    out = pd.Series(np.nan, index=y.index)
    for i in range(window, len(y) + 1, step):
        r = johansen_test(y.iloc[i - window:i].values, x.iloc[i - window:i].values)
        if "trace_stat" in r:
            out.iloc[i - 1] = r["trace_stat"]
    return out.ffill()
