"""
START-INVARIANCE RUNNER — fresh-start acceptance gate.

For each start date, slice the price history to [start - warmup, start + horizon],
write a temp dataset + config, run the pipeline from a COLD start (empty book,
state built only from that slice) and record the horizon return. A sound alpha
must pass the gate across these independent starts.

Usage:
    python scripts/start_invariance.py --config configs/config_v3_10y_all.json \
        --horizon 504 --warmup 504 --label baseline
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from modules.validation.gate import window_stats, format_stats  # noqa: E402

PY = sys.executable


def run_one(base_cfg: dict, data: pd.DataFrame, start_pos: int, horizon: int,
            warmup: int, workdir: str, tag: str) -> float:
    lo, hi = max(0, start_pos - warmup), min(len(data), start_pos + horizon)
    win = data.iloc[lo:hi]
    ddir = os.path.join(workdir, "data"); os.makedirs(ddir, exist_ok=True)
    csv = os.path.join(ddir, f"win_{tag}.csv")
    win.to_csv(csv)
    cfg = dict(base_cfg)
    cfg["price_csv"] = csv
    cfg["oos_price_csv"] = csv
    # One persistent dir per start date: results/gate/<label>/<start>/
    rdir = os.path.join(workdir, tag)
    cfg["results_dir"] = rdir
    cpath = os.path.join(workdir, f"cfg_{tag}.json")
    os.makedirs(workdir, exist_ok=True)
    json.dump(cfg, open(cpath, "w"), indent=2)
    # Clean ONLY this start's prior runs (overwrite policy, not temp deletion)
    import shutil
    shutil.rmtree(rdir, ignore_errors=True)
    r = subprocess.run([PY, os.path.join(HERE, "main.py"), "--config", cpath],
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [{tag}] FAILED rc={r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-800:]}")
        return float("nan")
    runs = sorted(int(d) for d in os.listdir(rdir) if d.isdigit()) if os.path.isdir(rdir) else []
    if not runs:
        return float("nan")
    st = pd.read_csv(os.path.join(rdir, str(runs[-1]), "csv", "portfolio_stats.csv"))
    st.columns = ["k", "v"]; S = st.set_index("k")["v"]
    return float(S["Total Return [%]"]) / 100.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--horizon", type=int, default=504)
    ap.add_argument("--warmup", type=int, default=504)
    ap.add_argument("--label", default="run")
    ap.add_argument("--starts", default="2018-09,2020-03,2021-09,2023-03,2024-09")
    a = ap.parse_args()
    base = json.load(open(os.path.join(HERE, a.config)))
    data = pd.read_csv(os.path.join(HERE, "data", os.path.basename(base["price_csv"])),
                       index_col=0, parse_dates=True)
    data = data[~data.index.duplicated()].sort_index().ffill()
    idx = data.index
    # Persist gate runs in the repo (NOT /tmp): each label/start gets
    # results/gate/<label>/<startdate>/ and is overwritten on re-run.
    work = os.path.join(HERE, "results", "gate", a.label)
    os.makedirs(work, exist_ok=True)
    rets = []
    print(f"[gate:{a.label}] horizon={a.horizon}d warmup={a.warmup}d work={work}")
    for s in a.starts.split(","):
        s = s.strip()
        pos = int(idx.searchsorted(pd.Timestamp(s)))
        if pos < a.warmup or pos + a.horizon > len(data):
            print(f"  {s}: skipped (out of range)")
            continue
        ret = run_one(base, data, pos, a.horizon, a.warmup, work, s)
        rets.append(ret)
        print(f"  {s}: {ret*100:+.2f}%")
    st = window_stats(rets)
    print(format_stats(f"{a.label} FRESH-START gate", st))
    out = os.path.join(work, "gate_summary.json")
    json.dump({"label": a.label, "starts": a.starts, "returns": rets, "stats": st},
              open(out, "w"), indent=2)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
