"""SelectionStage — emits a causal per-retrain parameter schedule into ctx."""
from __future__ import annotations

import pandas as pd
from modules import PipelineStage
from modules.selection.selector import WalkForwardSelector


class SelectionStage(PipelineStage):
    def run(self, ctx: dict) -> None:
        params = self.params
        if not bool(params.get("selection_enabled", False)):
            ctx["param_schedule"] = []
            return
        alpha = ctx["alpha_cube"]
        keys = ctx["candidate_pairs"]
        start_idx = ctx["start_idx"]
        dates = ctx["prices_full"].index
        freq = int(params.get("retrain_freq_days", 90))
        sel = WalkForwardSelector(params)
        schedule = []
        # Re-select every `freq` trading days, using only data up to that date.
        for i in range(start_idx + sel.window, len(dates), freq):
            best = sel.select(alpha, keys, i)
            if best:
                schedule.append({"idx": i, "date": dates[i], **best})
        ctx["param_schedule"] = schedule
        print(f"[Selection] walk-forward schedule: {len(schedule)} re-selections "
              f"(window={sel.window}d, grid={len(sel.grid)})", flush=True)
        if schedule:
            last = schedule[-1]
            print(f"[Selection] latest pick: entry_z={last.get('entry_z_base')} "
                  f"hl=[{last.get('min_half_life')},{last.get('max_half_life')}]", flush=True)
