"""
SCREENER v1 — Engle-Granger p-value (baseline behaviour).

Runs in a joblib worker: receives the windowed residual matrix once per chunk
and returns an order-preserving boolean pass-mask.
"""
from __future__ import annotations
import numpy as np


def coint_screen(pairs, vals, cmap, params) -> np.ndarray:
    from statsmodels.tsa.stattools import coint as _coint
    thr = float(params.get("p_value_threshold", 0.04))
    out = np.empty(len(pairs), dtype=bool)
    for k in range(len(pairs)):
        t1, t2 = pairs[k]
        out[k] = _coint(vals[:, cmap[t1]], vals[:, cmap[t2]])[1] <= thr
    return out
