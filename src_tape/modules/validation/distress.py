"""
MARKET-DISTRESS BATCH — run the SAME config across historical stress windows.

Two-file mode (as the original src/run_distress_tests.sh):
    price_csv     = data/distress_in/<name>.csv   (warmup, in-sample)
    oos_price_csv = data/distress/<name>.csv      (the distress window, OOS)
    lookback_days = len(warmup)  -> start_idx falls exactly at the first OOS row
So the walk-forward loop trades ONLY the distress window with valid pre-history.

Triggered by config ``run_distress: true`` (default false). Output goes to
``distress_out_dir`` (default results/19/distress), one numbered run per window,
plus a manifest.json mapping run -> window + headline metrics.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Callable, Optional

import pandas as pd

from modules.data import Config
from modules.core import PipelineOrchestrator


def _next_run_dir(base: str) -> int:
    dirs = [int(d) for d in os.listdir(base) if d.isdigit()] if os.path.isdir(base) else []
    return max(dirs) + 1 if dirs else 1


def run_distress_batch(config_path: Optional[str], base_overrides: dict, out_dir: str,
                       build_flow: Callable, only: Optional[str] = None,
                       verbose: bool = False) -> int:
    files = sorted(glob.glob("data/distress/*.csv"))
    if only:
        files = [f for f in files if os.path.basename(f)[:-4] == only]
    if not files:
        print(f"[distress] no windows found (only={only})", flush=True)
        return 1
    os.makedirs(out_dir, exist_ok=True)
    print(f"[distress] {len(files)} windows -> {out_dir}/", flush=True)

    manifest = []
    for k, oos in enumerate(files, 1):
        name = os.path.basename(oos)[:-4]
        warm = os.path.join("data", "distress_in", f"{name}.csv")
        if not os.path.exists(warm):
            print(f"[distress] WARN missing warmup {warm} — skip {name}", flush=True)
            continue
        wlen = len(pd.read_csv(warm, index_col=0))
        ov = dict(base_overrides)
        ov.update({"price_csv": warm, "oos_price_csv": oos, "lookback_days": wlen,
                   "results_dir": out_dir, "run_distress": False, "distress_only": None})
        print(f"[distress] [{k}/{len(files)}] {name}  warmup={wlen}d", flush=True)
        cfg = Config(config_path=config_path, overrides=ov)
        flow = build_flow(cfg.get("components", {}) or {})
        PipelineOrchestrator(cfg, flow, verbose=verbose, results_dir=out_dir).run()
        rd = os.path.join(out_dir, str(_next_run_dir(out_dir) - 1))
        rec = {"run": os.path.basename(rd), "window": name}
        try:
            s = pd.read_csv(os.path.join(rd, "csv", "portfolio_stats.csv"))
            s.columns = ["k", "v"]; S = s.set_index("k")["v"]
            rec.update({
                "total_return_%": float(S["Total Return [%]"]),
                "max_dd_%": float(S["Max Drawdown [%]"]),
                "sharpe": float(S["Sharpe Ratio"]),
                "trades": int(float(S["Total Trades"])),
            })
        except Exception as e:
            rec["error"] = str(e)
        manifest.append(rec)
        print(f"[distress]   -> {rd}  {rec.get('total_return_%', 'n/a')}", flush=True)

    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w"), indent=2)
    ok = [m for m in manifest if "sharpe" in m]
    if ok:
        print(f"[distress] DONE {len(ok)} windows | mean total "
              f"{sum(m['total_return_%'] for m in ok)/len(ok):+.2f}% | "
              f"worst {min(m['total_return_%'] for m in ok):+.2f}% "
              f"({min(ok, key=lambda m: m['total_return_%'])['window']})", flush=True)
    return 0
