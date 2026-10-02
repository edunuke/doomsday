"""
TAPE AUDIT — standalone reconciliation consumer.

Verifies the tape's internal invariants, above all the ORPHAN INVARIANT: a
ticker's net weight across concurrently open pairs must equal its BOOK target
at the same timestamp. Divergence here is exactly the class of bug (stranded
legs) this architecture exists to prevent.

Also reports the live reconciliation diff for a supplied positions file
(ticker,shares) against the latest BOOK.

Usage:
    python scripts/tape_audit.py results/1/tape.txt
    python scripts/tape_audit.py results/1/tape.txt --positions live.csv --nav 1000000 --prices last_prices.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.core.tape import TapeReader, lint  # noqa: E402


def orphan_invariant(path: str) -> pd.DataFrame:
    """Per-date max |sum(PAIR_BOOK legs) - BOOK target| per ticker.

    Both views are emitted for the same rebalance date, so grouping by ``ts``
    makes the comparison exact: the net of every pair leg (including explicit
    flatten events for exits/drops) must equal the BOOK target row.
    """
    pair_net = defaultdict(lambda: defaultdict(float))
    book = {}
    overlays = {}
    for ev in TapeReader(path).events({"PAIR_BOOK", "BOOK"}):
        if ev["type"] == "PAIR_BOOK":
            ts = ev["ts"]
            for leg, w in (ev.get("weights") or {}).items():
                pair_net[ts][leg] += float(w)
        else:
            ts = ev["ts"]
            book[ts] = {k: float(v) for k, v in (ev.get("targets") or {}).items()}
            overlays[ts] = float(ev.get("market_overlay",
                                       ev.get("spy_overlay", 0.0)))

    rows = []
    for ts in sorted(book):
        pn = pair_net.get(ts, {})
        bk = book[ts]
        legs = (set(pn) | set(bk)) - {"SPY", "ILF"}
        diff = max((abs(pn.get(k, 0.0) - bk.get(k, 0.0)) for k in legs), default=0.0)
        _mkt = "ILF" if "ILF" in bk else "SPY"
        spy_diff = abs(bk.get(_mkt, 0.0) - overlays.get(ts, 0.0))
        rows.append({"ts": ts, "max_abs_netting_diff": diff,
                     "market_overlay_diff": spy_diff, "n_legs": len(legs)})
    return pd.DataFrame(rows)


def audit(tape: str, positions=None, prices=None, nav=None) -> None:
    print("=" * 78)
    print("TAPE AUDIT")
    print("=" * 78)
    problems = lint(tape)
    print(f"  lint violations : {len(problems)}")
    for p in problems[:20]:
        print(f"      - {p}")

    df = orphan_invariant(tape)
    if len(df):
        worst = df["max_abs_netting_diff"].max()
        spy = df["market_overlay_diff"].max()
        print(f"  orphan invariant: max |net legs - book| = {worst:.3e} over {len(df)} rebalances")
        print(f"  market overlay  : max |book market leg - overlay| = {spy:.3e}")
        if worst > 1e-9:
            bad = df[df["max_abs_netting_diff"] > 1e-9]
            print("    BREACHES:")
            print(bad.to_string(index=False))
    else:
        print("  orphan invariant: no BOOK events")

    if positions and prices and nav:
        pos = pd.read_csv(positions)
        px = pd.read_csv(prices)
        last = None
        for ev in TapeReader(tape).events({"BOOK"}):
            last = ev
        target = pd.Series({k: float(v) for k, v in (last or {}).get("targets", {}).items()})
        cur = pd.Series(dict(zip(pos.iloc[:, 0], pos.iloc[:, 1])))
        prices_s = pd.Series(dict(zip(px.iloc[:, 0], px.iloc[:, 1])))
        tgt_sh = (target.reindex(prices_s.index).fillna(0.0) * nav / prices_s).round()
        diff = (tgt_sh - cur.reindex(tgt_sh.index).fillna(0.0)).abs()
        print(f"  live reconciliation: max |target - current| shares = {diff.max():.0f}, "
              f"total diff notional = {(diff * prices_s).sum():.2f} USD")
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile a decision tape")
    ap.add_argument("tape")
    ap.add_argument("--positions", default=None, help="CSV: ticker,shares")
    ap.add_argument("--prices", default=None, help="CSV: ticker,price")
    ap.add_argument("--nav", type=float, default=None)
    a = ap.parse_args()
    audit(a.tape, a.positions, a.prices, a.nav)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
