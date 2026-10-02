"""
WALK-FORWARD v3 — dynamic book overlay on top of v1.

v1 answers "which pairs, at what sleeves". v3 adds a *book-level* dynamic term
that v1 lacks: exposure is scaled per rebalance by

  (a) how many pairs have ACCUMULATED (crowding / over-trading), and
  (b) the QUALITY of the accumulated realised performance (trailing book return)

    scale_t = clip(1 - k_size * max(0, live_size_t - n_ref)
                     - k_dd   * max(0, -trailing_ret_t), floor, 1.0)

Implementation is additive: v3 runs v1 unchanged, then rewrites the run's tape
BOOK rows (and ctx["master_weights"]) by that scale. Attribution replays the
tape, so the overlay flows through with no edits to v1 or the engine.

Config knobs (defaults safe/off-ish):
  dynamic_book_overlay : True
  dybook_n_ref         : 10     # pair count beyond which we start de-risking
  dybook_k_size        : 0.03   # de-risk per extra pair above n_ref
  dybook_k_dd          : 2.0    # de-risk per unit of trailing loss
  dybook_floor         : 0.40   # minimum scale
  dybook_trail_window  : 63     # trailing-performance window (days)
"""

from __future__ import annotations

import json
import os
import shutil

import numpy as np
import pandas as pd

from modules.execution.walk_forward import WalkForwardStage


class WalkForwardStageV3(WalkForwardStage):
    """v1 pipeline + dynamic book overlay (crowding & realised-quality aware)."""

    def run(self, ctx: dict) -> None:
        # 1) run the standard pipeline unchanged
        super().run(ctx)

        params = self.params
        if not bool(params.get("dynamic_book_overlay", True)):
            print("[WFv3] dynamic_book_overlay=False — no overlay applied.", flush=True)
            return

        mw = ctx.get("master_weights")
        prices = ctx.get("prices_full")
        if mw is None or prices is None:
            return

        n_ref = float(params.get("dybook_n_ref", 10.0))
        k_size = float(params.get("dybook_k_size", 0.03))
        k_dd = float(params.get("dybook_k_dd", 2.0))
        floor = float(params.get("dybook_floor", 0.40))
        win = int(params.get("dybook_trail_window", 63))

        # ---- per-rebalance accumulated pair count --------------------------
        size = pd.Series(np.nan, index=mw.index, dtype=float)
        for rec in (ctx.get("metrics_history") or []):
            d = rec.get("Date")
            if d is not None and d in size.index:
                size.loc[d] = float(rec.get("Live_Size", 0.0))
        size = size.ffill().fillna(0.0)

        # ---- trailing realised book performance ----------------------------
        ret = prices.pct_change(fill_method=None).reindex(mw.index).fillna(0.0)
        book = mw.ffill().fillna(0.0).shift(1).mul(ret).sum(axis=1)
        trail = (1.0 + book).rolling(win, min_periods=max(10, win // 3)).apply(
            lambda v: np.prod(v) - 1.0, raw=True).fillna(0.0)

        # ---- dynamic scale -------------------------------------------------
        scale = (1.0
                 - k_size * (size - n_ref).clip(lower=0.0)
                 - k_dd * (-trail).clip(lower=0.0)).clip(lower=floor, upper=1.0)

        # apply to the weight matrix (rows that actually carry a book)
        mw2 = mw.copy()
        row_has = mw.notna().any(axis=1)
        mw2.loc[row_has] = mw.loc[row_has].mul(scale[row_has], axis=0)

        # ---- rewrite tape BOOK rows so tape replay sees the overlay --------
        tape = ctx.get("tape_path")
        if tape and os.path.exists(tape):
            self._rewrite_tape_books(
                tape, scale,
                keep_original=bool(params.get("tape_keep_original", False)))
            print(f"[WFv3] dynamic overlay applied: mean scale "
                  f"{scale[row_has].mean():.3f} (min {scale[row_has].min():.3f})",
                  flush=True)
        ctx["master_weights"] = mw2
        ctx["dybook_scale"] = scale

    # ------------------------------------------------------------------ tape
    @staticmethod
    def _rewrite_tape_books(tape_path: str, scale: pd.Series,
                            keep_original: bool = False) -> None:
        """Scale BOOK event targets by date.

        ``tape_keep_original`` (default False) writes a ``.orig`` backup first
        — redundant, since the per-event ``dybook_scale`` already records the
        overlay, and it doubles tape storage.
        """
        src = tape_path
        if keep_original:
            bak = tape_path + ".orig"
            if not os.path.exists(bak):
                shutil.copy2(tape_path, bak)
            src = bak
        tmp = tape_path + ".tmp"
        with open(src, "r") as fin, open(tmp, "w") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    fout.write(line + "\n")
                    continue
                if ev.get("type") == "BOOK":
                    s = float(scale.get(pd.Timestamp(ev.get("ts")), 1.0))
                    if abs(s - 1.0) > 1e-12:
                        t = ev.get("targets") or {}
                        ev["targets"] = {k: float(v) * s for k, v in t.items()}
                        ev["market_overlay"] = float(ev.get("market_overlay", 0.0)) * s
                        ev["gross"] = round(float(sum(abs(v) for v in ev["targets"].values())), 10)
                        ev["dybook_scale"] = round(s, 6)
                fout.write(json.dumps(ev, separators=(",", ":"), default=str) + "\n")
        os.replace(tmp, tape_path)
