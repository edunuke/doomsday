"""
SCREENER v4 — ranked + HARD quality floor (turnover-aware admission).

v3 ranked pairs but still admitted the whole top-K. v4 adds hard floors so
only genuinely high-conviction pairs reach the book:

  - cheap stage on the OLS-residual spread: EG p, variance ratio, half-life
  - Johansen on survivors; margin = trace/cv95 - 1
  - linear weighted score (Johansen-dominant)
  - admit iff:  score >= screen_score_floor
                AND joh_margin >= min_joh_margin
                AND within top-K by score

Fewer admitted pairs -> fewer promotions -> far fewer entries/trades (the
turnover driver), at the cost of a thinner book. All knobs config-driven.
"""
from __future__ import annotations
import numpy as np

from modules.stattests import half_life_ar1, variance_ratio, johansen_test
from statsmodels.tsa.stattools import coint as _eg


def _z(v: np.ndarray) -> np.ndarray:
    mu, sd = np.nanmean(v), np.nanstd(v)
    if not np.isfinite(sd) or sd < 1e-12:
        return np.zeros_like(v)
    z = (v - mu) / sd
    z[~np.isfinite(z)] = 0.0
    return z


def ranked_screen_v4(pairs, vals, cmap, params) -> np.ndarray:
    n = len(pairs)
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out

    eg_p_hard = float(params.get("eg_p_hard", 0.10))
    vr_max_hard = float(params.get("vr_max_hard", 1.05))
    hl_min_hard = float(params.get("hl_min_hard", 2.0))
    hl_max_hard = float(params.get("hl_max_hard", 60.0))
    hl_target = 0.5 * (hl_min_hard + hl_max_hard)
    top_k = int(params.get("screen_top_k", 10))
    score_floor = float(params.get("screen_score_floor", 0.0))
    min_margin = float(params.get("min_joh_margin", 0.15))
    w_eg = float(params.get("w_eg", 0.15))
    w_vr = float(params.get("w_vr", 0.15))
    w_hl = float(params.get("w_hl", 0.20))
    w_j = float(params.get("w_johansen", 0.50))

    eg_p = np.full(n, np.nan); vr = np.full(n, np.nan)
    hl = np.full(n, np.nan); joh = np.full(n, np.nan)
    survivors = []
    for k in range(n):
        t1, t2 = pairs[k]
        y = vals[:, cmap[t1]]; x = vals[:, cmap[t2]]
        X = np.column_stack([x, np.ones_like(x)])
        beta = np.linalg.lstsq(X, y, rcond=None)[0][0]
        spread = y - beta * x
        try:
            p = _eg(y, x)[1]
        except Exception:
            continue
        eg_p[k] = p
        if p > eg_p_hard:
            continue
        v = variance_ratio(spread, 5)
        vr[k] = v
        if not np.isfinite(v) or v > vr_max_hard:
            continue
        h = half_life_ar1(spread)
        hl[k] = h
        if not np.isfinite(h) or not (hl_min_hard <= h <= hl_max_hard):
            continue
        j = johansen_test(y, x)
        cv = j.get("trace_cv95", 0.0)
        joh[k] = (j.get("trace_stat", 0.0) / cv - 1.0) if cv else np.nan
        if not np.isfinite(joh[k]) or joh[k] < min_margin:
            continue                      # HARD Johansen margin floor
        survivors.append(k)

    if not survivors:
        return out

    idx = np.array(survivors, dtype=int)
    score = (w_eg * _z(-eg_p[idx]) + w_vr * _z(-vr[idx])
             + w_hl * _z(-np.abs(hl[idx] - hl_target)) + w_j * _z(joh[idx]))
    order = idx[np.argsort(-score)]
    keep = [(int(k), float(s)) for k, s in zip(order, np.sort(score)[::-1])]
    kept = [k for k, s in keep[:top_k] if s >= score_floor]
    for k in kept:
        out[k] = True
    return out
