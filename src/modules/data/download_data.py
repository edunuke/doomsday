"""
DOWNLOAD DATA STAGE

Downloads S&P 500 daily close prices and GICS constituents from Yahoo Finance.

Two modes:

1. DEFAULT (train/val split) — triggered by ``--download`` alone.
   Fetches ~4y of history, splits into ``universe_daily_train.csv`` /
   ``universe_daily_val.csv``, writes ``constituents.csv``.

2. CUSTOM WINDOW — triggered by ``--download`` plus ``--start --end --output``.
   Downloads exactly [start, end] and saves a single file at the given output
   path (e.g. ``data/distress/tariff_shock.csv``). Constituents are reused
   from ``data/constituents.csv`` if present, otherwise fetched.

Standalone (without the pipeline)::

    python -m modules.data.download_data --start 2025-02-19 --end 2025-08-08 --output data/distress/tariff_shock.csv

Through the pipeline::

    ./entrypoint.sh --download --start 2025-02-19 --end 2025-08-08 --output data/distress/tariff_shock.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

from modules import PipelineStage


# ---------------------------------------------------------------------------
# S&P 500 constituents from Wikipedia
# ---------------------------------------------------------------------------

def _fetch_sp500_constituents() -> pd.DataFrame:
    """Return DataFrame with columns ['Symbol', 'GICS Sector']."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    import io, urllib.request
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8")
    tables = pd.read_html(io.StringIO(html))
    df = tables[0]
    sym_col = "Symbol" if "Symbol" in df.columns else df.columns[1]
    sec_col = "GICS Sector" if "GICS Sector" in df.columns else df.columns[2]
    out = df[[sym_col, sec_col]].copy()
    out.columns = ["Symbol", "GICS Sector"]
    out["Symbol"] = out["Symbol"].str.strip()
    return out.reset_index(drop=True)


def _get_constituents(existing_path: Optional[str] = None) -> pd.DataFrame:
    """Reuse an existing constituents.csv if present, otherwise fetch fresh."""
    if existing_path and Path(existing_path).exists():
        print(f"  Reusing constituents -> {existing_path}", flush=True)
        return pd.read_csv(existing_path)
    print("  Fetching S&P 500 constituents from Wikipedia ...", flush=True)
    const = _fetch_sp500_constituents()
    return const


# ---------------------------------------------------------------------------
# Price download (batched)
# ---------------------------------------------------------------------------

def _download_close_prices(
    tickers: List[str],
    start: str,
    end: str,
    batch_size: int = 80,
    pause: float = 1.5,
) -> pd.DataFrame:
    """Download adjusted closes from Yahoo Finance in batches."""
    all_frames: List[pd.DataFrame] = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        tag = f"[batch {i // batch_size + 1}/{(len(tickers) + batch_size - 1) // batch_size}]"
        print(f"  {tag} Downloading {len(batch)} tickers ({batch[0]}..{batch[-1]}) ...", end=" ", flush=True)
        try:
            data = yf.download(batch, start=start, end=end, auto_adjust=True, progress=False, threads=True, group_by="ticker")
        except Exception as exc:
            print(f"FAILED ({exc})")
            time.sleep(pause)
            continue

        if isinstance(data.columns, pd.MultiIndex):
            if "Close" in data.columns.get_level_values(-1):
                close = data.xs("Close", axis=1, level=-1)
            elif "Close" in data.columns.get_level_values(0):
                close = data["Close"]
            else:
                close = data
        else:
            close = data

        if not isinstance(close.index, pd.DatetimeIndex):
            close.index = pd.to_datetime(close.index)

        print(f"got {close.shape[1]} tickers.")
        all_frames.append(close)
        time.sleep(pause)

    if not all_frames:
        print("[ERROR] No data downloaded.", file=sys.stderr)
        sys.exit(1)

    prices = pd.concat(all_frames, axis=1)
    before = prices.shape[1]
    prices = prices.dropna(axis=1, how="all")
    after = prices.shape[1]
    if before != after:
        print(f"  Dropped {before - after} tickers with no data ({after} kept).")
    prices = prices.ffill().sort_index()
    prices.index.name = None
    return prices


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def download_window(
    start: str,
    end: str,
    output_path: str,
    constituents_path: Optional[str] = "data/constituents.csv",
    batch_size: int = 80,
) -> Path:
    """Download [start, end] to a single CSV at output_path.

    Returns the output Path. Tickers are the current S&P 500 + SPY.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    constituents = _get_constituents(constituents_path)
    tickers = constituents["Symbol"].tolist() + ["SPY"]
    print(f"  Universe: {len(tickers)} tickers (S&P 500 + SPY).", flush=True)

    # Save constituents alongside the window file if none exists yet.
    if not (Path(constituents_path).exists() if constituents_path else False):
        const_dest = output.parent / "constituents.csv"
        constituents.to_csv(const_dest, index=False)
        print(f"  Saved constituents -> {const_dest}", flush=True)

    print(f"  Downloading daily closes ({start} -> {end}) ...", flush=True)
    prices = _download_close_prices(tickers, start=start, end=end, batch_size=batch_size)
    print(f"  Downloaded {prices.shape[1]} tickers x {prices.shape[0]} days.", flush=True)

    # Clamp to exactly [start, end]
    prices = prices.loc[start:end]

    prices.to_csv(output)
    print(f"  Saved -> {output}  ({output.stat().st_size / 1024:.0f} KB)", flush=True)
    return output


def download_train_val(
    val_end: Optional[str] = None,
    val_days: int = 252,
    min_history: int = 504,
    output_dir: str = "data",
) -> None:
    """Default mode: fetch ~4y and split into train/val CSVs."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    constituents = _get_constituents(str(output / "constituents.csv"))
    tickers = constituents["Symbol"].tolist() + ["SPY"]
    print(f"  Universe: {len(tickers)} tickers (S&P 500 + SPY).", flush=True)

    val_end_approx = pd.Timestamp(val_end) if val_end else pd.Timestamp.today().normalize()
    download_start = (val_end_approx - pd.DateOffset(years=4)).strftime("%Y-%m-%d")
    download_end = (val_end_approx + pd.offsets.BDay(2)).strftime("%Y-%m-%d")

    print(f"  Downloading daily closes ({download_start} -> {download_end}) ...", flush=True)
    prices = _download_close_prices(tickers, start=download_start, end=download_end)
    print(f"  Downloaded {prices.shape[1]} tickers x {prices.shape[0]} days.", flush=True)

    # ---- Split train / val ----
    val_start_idx = len(prices) - val_days
    if val_start_idx <= 0:
        print(f"[ERROR] Not enough data: {len(prices)} days, need > {val_days}", file=sys.stderr)
        sys.exit(1)

    prices_val = prices.iloc[val_start_idx:].copy()
    prices_train = prices.iloc[:val_start_idx].copy()

    assert prices_train.index[-1] < prices_val.index[0], \
        f"Overlap! Train ends {prices_train.index[-1]}, Val starts {prices_val.index[0]}"
    assert len(prices_train) >= min_history, \
        f"Training has {len(prices_train)} days, need >= {min_history}"

    print(f"  Train shape: {prices_train.shape}  ({prices_train.index[0].date()} -> {prices_train.index[-1].date()})", flush=True)
    print(f"  Val   shape: {prices_val.shape}  ({prices_val.index[0].date()} -> {prices_val.index[-1].date()})", flush=True)
    assert len(prices_val) >= val_days

    # ---- Save ----
    train_path = output / "universe_daily_train.csv"
    val_path = output / "universe_daily_val.csv"
    prices_train.to_csv(train_path)
    prices_val.to_csv(val_path)
    print(f"  Saved -> {train_path}  ({train_path.stat().st_size / 1024:.0f} KB)", flush=True)
    print(f"  Saved -> {val_path}    ({val_path.stat().st_size / 1024:.0f} KB)", flush=True)


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------

class DownloadDataStage(PipelineStage):
    """Stage 0: download S&P 500 data from Yahoo Finance (conditional).

    Only executes when ``self.params.get("download", False)`` is truthy.

    Modes:
      * ``--download`` alone                     → train/val split into data/
      * ``--download --start --end --output``    → single window → output path
    """

    def run(self, ctx: Dict[str, Any]) -> None:
        if not self.params.get("download", False):
            print("[DownloadDataStage] Skipped (--download not set).", flush=True)
            return

        print("[DownloadDataStage] Downloading S&P 500 data from Yahoo Finance ...", flush=True)

        start = self.params.get("start")
        end = self.params.get("end")
        output = self.params.get("output")

        if start and end:
            # CUSTOM WINDOW MODE
            output_path = output or f"data/distress/{start}_to_{end}.csv"
            print(f"[DownloadDataStage] Custom window mode: {start} -> {end}", flush=True)
            download_window(start=start, end=end, output_path=output_path)
            ctx["downloaded_window"] = str(Path(output_path).resolve())
        else:
            # DEFAULT TRAIN/VAL MODE
            print("[DownloadDataStage] Default mode: full train/val split.", flush=True)
            download_train_val(
                val_end=self.params.get("val_end"),
                val_days=int(self.params.get("val_days", 252)),
                min_history=int(self.params.get("min_history", 504)),
                output_dir=self.params.get("output_dir", "data"),
            )

        print("[DownloadDataStage] Complete.", flush=True)


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download S&P 500 daily close data for a custom window.",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_window = sub.add_parser("window", help="Download a custom [start, end] window")
    p_window.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_window.add_argument("--end", required=True, help="End date YYYY-MM-DD (exclusive)")
    p_window.add_argument("--output", required=True, help="Output CSV path (e.g. data/distress/name.csv)")

    p_full = sub.add_parser("full", help="Default train/val split")
    p_full.add_argument("--val-end", default=None)
    p_full.add_argument("--val-days", type=int, default=252)
    p_full.add_argument("--min-history", type=int, default=504)
    p_full.add_argument("--output-dir", default="data")

    args = parser.parse_args()

    if args.mode == "window":
        download_window(args.start, args.end, args.output)
    else:
        download_train_val(
            val_end=args.val_end,
            val_days=args.val_days,
            min_history=args.min_history,
            output_dir=args.output_dir,
        )