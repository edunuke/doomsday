"""
TAPE REPORT — standalone tearsheet / summary consumer.

Renders a compact report from the tape alone (no decision re-run): header, run
length, rebalance causes, gross-exposure path and entry/exit audit totals.

Usage:
    python scripts/tape_report.py results/1/tape.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.core.tape import TapeReader  # noqa: E402


def load_tape(path: str):
    r = TapeReader(path)
    rebal, books, entries, exits = [], [], [], []
    for ev in r.events():
        t = ev["type"]
        if t == "REBALANCE":
            rebal.append(ev)
        elif t == "BOOK":
            books.append(ev)
        elif t == "ENTRY":
            entries.append(ev)
        elif t == "EXIT":
            exits.append(ev)
    return r.header(), rebal, books, entries, exits


def render(tape_path: str) -> None:
    hdr, rebal, books, entries, exits = load_tape(tape_path)

    print("=" * 78)
    print("DECISION TAPE REPORT")
    print("=" * 78)
    for k in ("run", "universe", "n_tickers", "start_idx", "config_sha", "exec", "fill"):
        print(f"  {k:<14}: {hdr.get(k)}")

    if rebal:
        ts = [pd.Timestamp(r["ts"]) for r in rebal]
        print(f"  rebalances    : {len(rebal)}  ({ts[0].date()} -> {ts[-1].date()})")
    print(f"  entries/exits : {len(entries)} / {len(exits)}")
    print(f"  causes        : {dict(Counter(r['cause'].split(' ')[0] for r in rebal))}")

    if books:
        gross = pd.Series([b.get("gross", 0.0) for b in books],
                          index=[pd.Timestamp(b["ts"]) for b in books])
        print(f"  gross (min/mean/max): {gross.min():.2f} / {gross.mean():.2f} / {gross.max():.2f}")

    if exits:
        reasons = Counter(e.get("reason", "?") for e in exits)
        print(f"  exit reasons  : {dict(reasons)}")
        held = [e.get("days_held", 0) for e in exits]
        print(f"  days held (mean/median/max): "
              f"{np.mean(held):.1f} / {np.median(held):.1f} / {max(held)}")
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description="Report from a decision tape")
    ap.add_argument("tape")
    a = ap.parse_args()
    render(a.tape)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
