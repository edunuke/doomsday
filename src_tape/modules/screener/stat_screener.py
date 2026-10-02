"""
SCREENER v2 — Johansen rank (+ Hurst + half-life under the full funnel).

Under ``funnel_mode="simple"`` the screener tests COINTEGRATION ONLY
(Johansen trace at 95%); mean-reversion quality is enforced ONCE downstream by
the alpha-cube filters (single source of truth). This avoids the
double/inconsistent Hurst that collapsed the funnel (207 -> 1).

Under ``funnel_mode="full"`` (default) it also requires Hurst < max and
half-life in bounds on the OLS spread — the legacy 7-step behaviour.
"""
from __future__ import annotations
import numpy as np

from modules.stattests import hurst_exponent, half_life_ar1, johansen_test


def stat_screen(pairs, vals, cmap, params) -> np.ndarray:
    simple = str(params.get("funnel_mode", "full")).lower() == "simple"
    h_max = float(params.get("max_hurst_exponent", 0.43))
    hl_min = float(params.get("min_half_life", 4.0))
    hl_max = float(params.get("max_half_life", 25.0))
    out = np.empty(len(pairs), dtype=bool)
    for k in range(len(pairs)):
        t1, t2 = pairs[k]
        y = vals[:, cmap[t1]]
        x = vals[:, cmap[t2]]
        X = np.column_stack([x, np.ones_like(x)])
        beta = np.linalg.lstsq(X, y, rcond=None)[0][0]
        spread = y - beta * x
        ok = True
        if not simple:
            h = hurst_exponent(spread)
            if not np.isfinite(h) or h >= h_max:
                ok = False
            if ok:
                hl = half_life_ar1(spread)
                if not (np.isfinite(hl) and hl_min <= hl <= hl_max):
                    ok = False
        if ok:
            j = johansen_test(y, x)
            ok = bool(j.get("trace_coint_95", False))
        out[k] = ok
    return out
