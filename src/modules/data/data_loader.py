"""
DATA LOADER STAGE

Loads train/val price CSVs and open-price CSVs, determines the
market return series (SPY or synthetic equal-weight), and builds
the DAG (daily arithmetic growth) DataFrame.

Context produced
----------------
prices_full  : pd.DataFrame  — all close prices (train + val), NaN-dropped
open_full    : pd.DataFrame  — open prices (or close if unavailable)
market_ret   : pd.Series     — benchmark daily return
dag_df       : pd.DataFrame  — tradeable universe (SPY excluded)
valid_tickers: pd.Index      — column names from dag_df
"""

import pandas as pd

from modules import PipelineStage


class DataLoaderStage(PipelineStage):
    """Stage 1: load price CSVs and prepare market data."""

    def run(self, ctx: dict) -> None:
        # ---- Load close prices -----------------------------------------------
        try:
            train = pd.read_csv(self.params["price_csv"], index_col=0, parse_dates=True)
            val = pd.read_csv(self.params["oos_price_csv"], index_col=0, parse_dates=True)
            prices = pd.concat([train, val]).dropna(axis=1, how="all").ffill()
        except Exception:
            train = pd.read_csv(self.params["price_csv"], index_col=0, parse_dates=True)
            prices = train.ffill()

        prices = prices[~prices.index.duplicated()].sort_index().ffill()

        # ---- Load open prices ------------------------------------------------
        try:
            o_train = pd.read_csv(
                self.params["price_csv"].replace("close", "open"),
                index_col=0, parse_dates=True,
            )
            o_val = pd.read_csv(
                self.params["oos_price_csv"].replace("close", "open"),
                index_col=0, parse_dates=True,
            )
            o_full = pd.concat([o_train, o_val]).dropna(axis=1, how="all").ffill()
            o_full = o_full[~o_full.index.duplicated(keep="first")].sort_index()
        except FileNotFoundError:
            o_full = prices.copy()

        # ---- Market returns & tradeable universe ----------------------------
        if "SPY" in prices.columns:
            print("[DataLoader] 'SPY' detected. Using real SPY series.", flush=True)
            market_ret = prices["SPY"].pct_change().fillna(0.0)
            tradeable = prices.drop(columns=["SPY"])
        else:
            print("[DataLoader] No SPY — using equal-weighted synthetic index.", flush=True)
            tradeable = prices
            market_ret = tradeable.pct_change().mean(axis=1)

        dag_df = tradeable.dropna(axis=1)

        ctx["prices_full"] = prices
        ctx["open_full"] = o_full
        ctx["market_ret"] = market_ret
        ctx["dag_df"] = dag_df
        ctx["valid_tickers"] = dag_df.columns

        print(
            f"[DataLoader] Loaded {len(prices)} days x {len(tradeable.columns)} tickers.",
            flush=True,
        )