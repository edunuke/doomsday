"""
SCREENER v5 — ranked admission + FDR control + persistence filter.

Motivation (this project's diagnostics): the pipeline screens ~100+ candidate
pairs per retrain and trades survivors. Without multiple-testing control,
borderline p-values pass by chance and fail out-of-sample. Additionally, a
"pair" that passed EG/Johansen once but whose spread rarely crossed its mean is
not a tradeable reversion relationship.

Admission = ranked_screen(v3) mask  AND
            BH-FDR-adjusted EG p <= fdr_q  AND
            mean-crossings of the OLS spread >= min_mean_crossings

All knobs config-driven; the mask is order-preserving.
"""
from __future__ import annotations
import numpy as np
from statsmodels.tsa.stattools import coint as _eg

from modules.screener.ranked_screen import ranked_screen
from modules.stattests import mean_crossings


def _bh_fdr_mask(pvals: np.ndarray, q: float) -> np.ndarray:
    """Benjamini-Hochberg: keep p_(i) <= (i/m) * q (order-preserving mask)."""
    p = np.asarray(pvals, dtype=float)
    m = int(np.sum(np.isfinite(p)))
    keep = np.zeros(len(p), dtype=bool)
    if m == 0:
        return keep
    idx = np.where(np.isfinite(p))[0]
    order = idx[np.argsort(p[idx])]
    thresh = (np.arange(1, m + 1) / m) * q
    passed = p[order] <= thresh
    if not passed.any():
        return keep
    # BH step-up: all ranks up to the largest passing rank are rejected.
    k = np.max(np.where(passed)[0])
    keep[order[: k + 1]] = True
    return keep


def fdr_screen(pairs, vals, cmap, params) -> np.ndarray:
    n = len(pairs)
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out

    fdr_q = float(params.get("fdr_q", 0.10))
    min_x = int(params.get("min_mean_crossings", 4))

    eg_p = np.full(n, np.nan)
    crossings = np.zeros(n, dtype=int)
    for k in range(n):
        t1, t2 = pairs[k]
        y = vals[:, cmap[t1]]
        x = vals[:, cmap[t2]]
        try:
            eg_p[k] = _eg(y, x)[1]
        except Exception:
            continue
        X = np.column_stack([x, np.ones_like(x)])
        beta = np.linalg.lstsq(X, y, rcond=None)[0][0]
        crossings[k] = mean_crossings(y - beta * x)

    # Stage 1: multiple-testing control + persistence.
    ok = _bh_fdr_mask(eg_p, fdr_q) & (crossings >= min_x) & np.isfinite(eg_p)
    if not ok.any():
        return out

    # Stage 2: delegate ranking/admission to the v3 screener, restricted to
    # stage-1 survivors (compute cost duplicated per survivor set, acceptable
    # at screen frequency; keeps v3 semantics untouched).
    sub_pairs = [pairs[k] for k in np.where(ok)[0]]
    sub_mask = ranked_screen(sub_pairs, vals, cmap, params)
    out[np.where(ok)[0]] = sub_mask
    return out
