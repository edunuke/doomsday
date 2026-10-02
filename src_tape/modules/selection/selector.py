"""
WALK-FORWARD PARAMETER SELECTOR

Re-selects a small parameter set on a TRAILING window at each retrain, causally,
so the strategy is not run with a single regime-tuned config over a decade.

Proxy score (fast, no pipeline re-run): for a candidate param set, build an
equal-weight pair-reversion book from the alpha cube's own signals over the
trailing window and score its Sharpe net of a turnover penalty. The winning set
is applied to the NEXT window only (no look-ahead: the window ends at the
selection date).

Selections happen on a subsample of pairs to bound cost.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


_DEFAULT_GRID = [
    {"entry_z_base": 0.80, "min_half_life": 2.0, "max_half_life": 21.0},
    {"entry_z_base": 0.95, "min_half_life": 2.0, "max_half_life": 21.0},
    {"entry_z_base": 1.10, "min_half_life": 2.0, "max_half_life": 21.0},
    {"entry_z_base": 0.95, "min_half_life": 4.0, "max_half_life": 15.0},
    {"entry_z_base": 0.95, "min_half_life": 4.0, "max_half_life": 25.0},
    {"entry_z_base": 1.10, "min_half_life": 4.0, "max_half_life": 25.0},
]


class WalkForwardSelector:
    def __init__(self, params: dict):
        self.params = params
        self.window = int(params.get("selection_window_days", 504))
        self.exit_z = float(params.get("exit_z_spread", 0.2))
        self.max_hold = int(params.get("selection_max_hold", 20))
        self.max_pairs = int(params.get("selection_max_pairs", 400))
        self.grid = params.get("selection_grid", _DEFAULT_GRID)

    def _score(self, alpha: dict, keys: List[Tuple[str, str]], t0: int, t1: int,
               cfg: dict) -> float:
        ent = float(cfg["entry_z_base"])
        hlmin, hlmax = float(cfg["min_half_life"]), float(cfg["max_half_life"])
        port = np.zeros(t1 - t0)
        n = 0
        for p in keys:
            df = alpha.get(p)
            if df is None:
                continue
            z = df["Z_Spread"].iloc[t0:t1].values
            hl = df["Half_Life"].iloc[t0:t1].values
            sr = df["Spread_Return"].iloc[t0:t1].values
            if len(z) < 30 or not np.isfinite(sr).any():
                continue
            cond = (np.abs(z) >= ent) & (hl >= hlmin) & (hl <= hlmax)
            if not cond.any():
                continue
            raw = np.where(cond & (np.abs(z) > self.exit_z), -np.sign(z), np.nan)
            pos = pd.Series(raw).ffill(limit=self.max_hold).fillna(0.0).values
            pnl = pos * np.nan_to_num(sr)
            port += np.nan_to_num(pnl)
            n += 1
        if n == 0:
            return -1e9
        port /= n
        sd = port.std()
        if sd <= 1e-12:
            return -1e9
        turn = np.abs(np.diff(port)).mean() * 2.0
        return float(port.mean() / sd * np.sqrt(252.0) - 5.0 * turn)

    def select(self, alpha: dict, keys: List[Tuple[str, str]], current_idx: int) -> dict:
        t0 = max(0, current_idx - self.window)
        t1 = current_idx
        sample = keys[:: max(1, len(keys) // self.max_pairs)] if keys else []
        best, best_cfg = -np.inf, None
        for cfg in self.grid:
            s = self._score(alpha, sample, t0, t1, cfg)
            if s > best:
                best, best_cfg = s, cfg
        return dict(best_cfg) if best_cfg else {}
