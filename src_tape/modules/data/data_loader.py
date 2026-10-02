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
        # The benchmark/reference instrument is CONFIG-DRIVEN (`market_ticker`):
        # "SPY" for the US universe, "ILF" for the LatAm universe, etc. It is
        # excluded from the tradeable universe and used for residualisation,
        # the beta overlay hedge and all market-relative reporting.
        mkt_ticker = str(self.params.get("market_ticker", "SPY"))
        if mkt_ticker in prices.columns:
            print(f"[DataLoader] market reference '{mkt_ticker}' detected — "
                  f"using it as the market series.", flush=True)
            market_ret = prices[mkt_ticker].pct_change().fillna(0.0)
            tradeable = prices.drop(columns=[mkt_ticker])
            market_col = mkt_ticker
        else:
            print(f"[DataLoader] market reference '{mkt_ticker}' not found in "
                  "prices — using equal-weighted synthetic index.", flush=True)
            tradeable = prices
            market_ret = tradeable.pct_change().mean(axis=1)
            market_col = None

        dag_df = tradeable.dropna(axis=1)
        # ---- Breadth: allow late-listed names (config-gated) ----------------
        # Legacy `dropna(axis=1)` removes ANY name with a single NaN, which
        # silently deletes every post-start listing (IPOs, ADR programs) and
        # starves cross-sectional breadth. With `min_history_fraction` set
        # (e.g. 0.6), a name survives if its price history covers that
        # fraction of the panel; its leading NaNs keep it naturally
        # ineligible until the alpha-cube warmup completes.
        _mhf = self.params.get("min_history_fraction")
        if _mhf is not None:
            _cov = tradeable.notna().mean()
            _keep = _cov[_cov >= float(_mhf)].index
            dag_df = tradeable[_keep]
            _dropped_breadth = int(len(tradeable.columns) - len(_keep))
            print(f"[DataLoader] min_history_fraction={_mhf}: {len(_keep)} "
                  f"names eligible ({_dropped_breadth} dropped for thin "
                  f"history).", flush=True)

        # ---- Synthetic-name tradeability gate --------------------------------
        # Survivorship synthetics (SYNxxxx) exist to compensate delisting bias
        # in the price panel (beta/idio calibration, residualisation) — they
        # must never be tradeable pair legs: their generator-imposed OU
        # dynamics make them trivially cointegratable, so the screen would
        # preferentially admit them and fabricate alpha. Default False keeps
        # historical behaviour; enabled via config for honest real-name runs.
        if self.params.get("exclude_synth_from_pairs"):
            _syn = [c for c in dag_df.columns if str(c).startswith("SYN")]
            if _syn:
                dag_df = dag_df.drop(columns=_syn)
                print(f"[DataLoader] exclude_synth_from_pairs: removed "
                      f"{len(_syn)} synthetic names from the tradeable universe "
                      f"(kept in price panel).", flush=True)

        ctx["prices_full"] = prices
        ctx["open_full"] = o_full
        ctx["market_ret"] = market_ret
        ctx["market_ticker"] = mkt_ticker
        ctx["market_col"] = market_col
        ctx["dag_df"] = dag_df
        ctx["valid_tickers"] = dag_df.columns

        # Optional volume panel (relative-volume entry overlay). Loaded only
        # when configured; the overlay itself is gated downstream so legacy
        # configs are unaffected.
        vcsv = self.params.get("volume_csv")
        if vcsv:
            try:
                vol = pd.read_csv(vcsv, index_col=0, parse_dates=True)
                vol = vol[~vol.index.duplicated()].sort_index()
                ctx["volume_df"] = vol.reindex(prices.index)
                print(f"[DataLoader] Volume panel loaded: {vcsv} "
                      f"({vol.shape[1]} tickers).", flush=True)
            except FileNotFoundError:
                print(f"[DataLoader] WARNING: volume_csv {vcsv} not found — "
                      "volume overlay will be inert.", flush=True)

        print(
            f"[DataLoader] Loaded {len(prices)} days x {len(tradeable.columns)} tickers.",
            flush=True,
        )