"""
SIZING — independent leverage / exposure-scaling components.

v1 NoSizing        : scale = 1 (no leverage; baseline).
v2 VolTargetSizing : scale gross so trailing realised portfolio vol targets
                     ``sizing_target_vol`` (annualised), capped at
                     ``sizing_max_leverage`` and floored at ``sizing_min_scale``.

Causal: realised vol uses only the trailing equity history up to ``pos``.
Leverage is only ever applied when the base strategy's trailing vol is below
target (i.e. it de-risks high-vol regimes and can modestly lever calm ones).
"""

from __future__ import annotations

import numpy as np


class NoSizing:
    def __init__(self, params: dict):
        self.params = params

    def scale(self, equity_hist, pos: int, params: dict) -> float:
        return 1.0


class VolTargetSizing:
    def __init__(self, params: dict):
        self.params = params
        self.target = float(params.get("sizing_target_vol", 0.10))
        self.window = int(params.get("sizing_vol_window", 126))
        self.max_lev = float(params.get("sizing_max_leverage", 1.5))
        self.min_scale = float(params.get("sizing_min_scale", 0.2))
        self.mode = params.get("sizing_mode", "exposure")

    def scale(self, equity_hist, pos: int, params: dict) -> float:
        if pos < self.window or not equity_hist:
            return 1.0
        seg = np.asarray(equity_hist[max(0, pos - self.window): pos], dtype=float)
        if len(seg) < 20 or seg[0] <= 0:
            return 1.0
        rets = np.diff(seg) / seg[:-1]
        vol = float(np.std(rets) * np.sqrt(252.0))
        if vol <= 1e-9:
            return self.max_lev
        s = self.target / vol
        # exposure mode: only de-risk (<=1) unless leverage explicitly allowed
        if self.mode == "derisk":
            return float(np.clip(s, self.min_scale, 1.0))
        return float(np.clip(s, self.min_scale, self.max_lev))
