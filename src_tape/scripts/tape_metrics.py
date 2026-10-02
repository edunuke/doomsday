"""
TAPE METRICS — standalone measurement consumer.

Reads a decision tape + the price data named in the config and reproduces the
portfolio metrics with our own engine. No decision re-run, no engine import of
reporting state.

Usage:
    python scripts/tape_metrics.py results/1/tape.txt --config configs/config_best.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.core.tape import TapeReader, lint, reconstruct_book  # noqa: E402
from modules.data import Config, DataLoaderStage  # noqa: E402
from modules.execution.portfolio_engine import run_target_weights  # noqa: E402
from modules.execution.stats import compute_stats  # noqa: E402


def _cap_gross(mat: pd.DataFrame, cap: float) -> pd.DataFrame:
    abs_sum = mat.abs().sum(axis=1)
    exceed = abs_sum > cap
    if exceed.any():
        mat = mat.copy()
        mat.loc[exceed] = mat.loc[exceed].div(abs_sum[exceed], axis=0) * cap
    return mat


def compute_metrics(tape_path: str, config_path: str) -> dict:
    cfg = Config(config_path=config_path)
    params = cfg.as_dict()
    ctx: dict = {}
    DataLoaderStage(cfg).run(ctx)
    prices = ctx["prices_full"]
    open_full = ctx["open_full"]
    start = min(params["lookback_days"], len(prices))

    book = _cap_gross(
        reconstruct_book(tape_path, prices.index, prices.columns, "BOOK"),
        params["max_gross_exposure"])
    exec_w = book.shift(1).iloc[start:]
    exec_close = prices[book.columns].iloc[start:]
    exec_open = open_full[book.columns].reindex(prices.index).ffill().iloc[start:]

    res = run_target_weights(
        exec_close, exec_open, exec_w,
        init_cash=params["initial_capital"], exec_fee=params["exec_fee"],
        daily_borrow_rate=(params["borrow_bps"] / 10000.0) / 252.0)
    stats = compute_stats(res, float(res.gross_exposure.max()))

    stats["Tape Path"] = tape_path
    stats["Tape Events"] = sum(1 for _ in TapeReader(tape_path).events())
    stats["Tape Lint Violations"] = len(lint(tape_path))
    hdr = TapeReader(tape_path).header()
    stats["Config SHA"] = hdr.get("config_sha", "")
    return stats.to_dict()


def main() -> int:
    ap = argparse.ArgumentParser(description="Metrics from a decision tape")
    ap.add_argument("tape")
    ap.add_argument("--config", default="configs/config_best.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    stats = compute_metrics(a.tape, a.config)
    out = stats if a.out is None else {k: v for k, v in stats.items()}
    print(json.dumps({k: str(v) for k, v in stats.items()}, indent=2))
    if a.out:
        pd.Series(stats).to_csv(a.out)
        print(f"[tape_metrics] wrote {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
