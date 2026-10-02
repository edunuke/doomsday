"""
SCREENER v3 — linear-weighted ranked multi-stage admission.

  Stage 1 (cheap): per candidate, on the OLS-residual spread:
      - Engle-Granger p-value
      - Lo-MacKinlay variance ratio
      - AR(1) half-life
  Stage 2 (rank): linear weighted score over z-scored components
      score = w_eg*z(-EGp) + w_vr*z(-VR) + w_hl*z(-|HL-hl_target|)
            + w_j*z(joh_margin)
      joh_margin = trace_stat / trace_cv95 - 1   (how far above the 95% cv)
      Johansen is computed for all stage-1 survivors; its weight dominates the
      rank (default 0.50 vs 0.15/0.15/0.20).
  Stage 3 (admission): top-K by score AND Johansen trace >= 95%.

All weights/limits are config knobs; the mask is order-preserving.
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


def ranked_screen(pairs, vals, cmap, params) -> np.ndarray:
    n = len(pairs)
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out

    eg_p_hard = float(params.get("eg_p_hard", 0.10))
    vr_max_hard = float(params.get("vr_max_hard", 1.05))
    hl_min_hard = float(params.get("hl_min_hard", 2.0))
    hl_max_hard = float(params.get("hl_max_hard", 60.0))
    hl_target = 0.5 * (hl_min_hard + hl_max_hard)
    top_k = int(params.get("screen_top_k", 60))
    w_eg = float(params.get("w_eg", 0.15))
    w_vr = float(params.get("w_vr", 0.15))
    w_hl = float(params.get("w_hl", 0.20))
    w_j = float(params.get("w_johansen", 0.50))

    eg_p = np.full(n, np.nan)
    vr = np.full(n, np.nan)
    hl = np.full(n, np.nan)
    joh = np.full(n, np.nan)
    survivors = []

    for k in range(n):
        t1, t2 = pairs[k]
        y = vals[:, cmap[t1]]
        x = vals[:, cmap[t2]]
        X = np.column_stack([x, np.ones_like(x)])
        beta = np.linalg.lstsq(X, y, rcond=None)[0][0]
        spread = y - beta * x                      # OLS-residual spread
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
        survivors.append(k)

    if not survivors:
        return out

    idx = np.array(survivors, dtype=int)
    score = (
        w_eg * _z(-eg_p[idx])
        + w_vr * _z(-vr[idx])
        + w_hl * _z(-np.abs(hl[idx] - hl_target))
        + w_j * _z(joh[idx])
    )
    order = idx[np.argsort(-score)]
    keep = set(order[:top_k].tolist())

    for k in range(n):
        if k in keep and joh[k] > 0.0:          # Johansen trace >= 95% cv
            out[k] = True
    return out
