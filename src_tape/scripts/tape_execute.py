"""
TAPE EXECUTE — standalone rebalancing / MOO order consumer.

Reads the latest BOOK from the tape and produces an actionable market-on-open
blotter from the target book + current broker positions. Pure function of the
tape and broker state.

Usage:
    python scripts/tape_execute.py results/1/tape.txt --config configs/config_best.json
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.core.tape import TapeReader  # noqa: E402
from modules.data import Config, DataLoaderStage  # noqa: E402
from modules.execution.order_generator import LiveMOOOrderGenerator  # noqa: E402


def latest_book(tape_path: str) -> pd.Series:
    last = None
    for ev in TapeReader(tape_path).events({"BOOK"}):
        last = ev
    if last is None:
        return pd.Series(dtype=float)
    return pd.Series({k: float(v) for k, v in (last.get("targets") or {}).items()})


def build_blotter(tape_path: str, live_positions: Dict[str, int],
                  prices: pd.DataFrame, nav: float, params: dict) -> pd.DataFrame:
    targets = latest_book(tape_path)
    targets = targets.reindex(prices.columns).fillna(0.0)
    adv = (
        prices.iloc[-int(params.get("adv_window_days", 20)):].mean()
        * float(params.get("adv_shares_mult", 50_000))
    ).fillna(float(params.get("adv_fill_value", 1_000_000)))
    moo = LiveMOOOrderGenerator(
        min_order_usd=float(params.get("moo_min_order_usd", 500.0)),
        max_adv_part=params.get("max_adv_participation", 0.015),
    )
    return moo.generate_blotter(
        target_weights=targets, current_positions=live_positions,
        latest_prices=prices.iloc[-1], rolling_adv_shares=adv, portfolio_nav=nav)


def main() -> int:
    ap = argparse.ArgumentParser(description="MOO blotter from a decision tape")
    ap.add_argument("tape")
    ap.add_argument("--config", default="configs/config_best.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    cfg = Config(config_path=a.config)
    params = cfg.as_dict()
    ctx: dict = {}
    DataLoaderStage(cfg).run(ctx)
    blotter = build_blotter(a.tape, {}, ctx["prices_full"], params["initial_capital"], params)
    print(blotter.to_string(index=False))
    if a.out:
        blotter.to_csv(a.out, index=False)
        print(f"[tape_execute] wrote {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
