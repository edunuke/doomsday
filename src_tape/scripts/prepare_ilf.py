"""
Prepare ILF data for the stat-arb pipeline.

Converts Bloomberg-format ILF.csv and ILF_constituents.csv into the same format
as universe_daily_5y_real.csv and constituents_gics.csv.
"""
import pandas as pd
import numpy as np
import re
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ILF_DIR = os.path.join(DATA_DIR, "ILF")


def strip_bloomberg_ticker(t) -> str:
    """Strip Bloomberg exchange suffixes like ' US Equity', ' MM Equity', etc."""
    if not isinstance(t, str) or not t.strip():
        return ""
    t = t.strip()
    t = re.sub(r"\s+(US|MM|BZ|CI|CB|LN|TI|JP|HK|SW|GR|FP|SS|SK|NZ|AB|PL|IM|MC|SA|TA)\s+Equity$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+Index$", "", t, flags=re.IGNORECASE)
    t = t.replace("*", "")
    # Some constituent rows fused the exchange token into the Symbol field
    # ("WALMEX* MM" with no separating comma); strip a trailing bare token.
    t = re.sub(r"\s+(US|MM|BZ|CI|CB|Index)$", "", t, flags=re.IGNORECASE)
    return t.strip()


def parse_ilf_prices() -> pd.DataFrame:
    """Parse Bloomberg ILF.csv into wide-format price DataFrame.

    Bloomberg format:
      Row 0: ,TICKER1 US Equity,,TICKER2 US Equity,...
      Row 1: Dates,PX_LAST,PX_VOLUME,PX_LAST,PX_VOLUME,...
      Row 2+: MM/DD/YYYY,price,volume,price,volume,...

    The tickers in row 0 are at odd column indices (1,3,5,...).
    PX_LAST fields in row 1 are at the same odd indices.
    PX_VOLUME fields are at even indices (2,4,6,...).
    """
    raw = pd.read_csv(os.path.join(ILF_DIR, "ILF.csv"), header=None, dtype=str)

    row0 = raw.iloc[0].tolist()
    row1 = raw.iloc[1].tolist()

    # Build ticker->column-index mapping from row 0 and row 1
    # Pattern: col 0 is empty/date label, then pairs (ticker, empty) at (1,2), (3,4), ...
    # In row 1: col 0 is "Dates", then PX_LAST at (1,3,5,...), PX_VOLUME at (2,4,6,...)
    tickers = {}
    for i in range(1, len(row0)):
        val = row0[i]
        if isinstance(val, str) and val.strip():
            clean = strip_bloomberg_ticker(val)
            if clean:
                tickers[clean] = i

    # Parse dates
    date_strings = raw.iloc[2:, 0].values
    dates = pd.to_datetime(date_strings, format="%m/%d/%Y", errors="coerce")

    # Extract PX_LAST columns
    data = {}
    for ticker, col_idx in tickers.items():
        values = raw.iloc[2:, col_idx].values
        data[ticker] = pd.to_numeric(values, errors="coerce")

    df = pd.DataFrame(data, index=dates)
    df = df.dropna(axis=1, how="all")
    df = df.sort_index()
    df.index.name = None
    return df


def parse_ilf_constituents_gics() -> pd.DataFrame:
    """Parse ILF_constituents.csv into constituents_gics.csv format.

    The raw file has inconsistent quoting: rows like
        WALMEX* MM ,"Wal-Mart de México, S.A.B. de C.V.",Consumer Staples,...
    lack the comma between Symbol and Country, so the quoted company name
    (containing commas) shifts the columns left. Robust approach: the LAST
    two non-empty fields are always GICS Sector and GICS Sub-Industry, and
    the FIRST field is always the Bloomberg Symbol.
    """
    out_rows = []
    with open(os.path.join(ILF_DIR, "ILF_constituents.csv")) as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Parse respecting double-quoted fields
            import csv as _csv
            fields = [fld.strip() for fld in next(_csv.reader([line]))]
            if len(fields) < 4:
                continue
            symbol_raw = fields[0]
            sector = fields[-2]
            sub_industry = fields[-1]

            # Skip nothing: index futures are real ILF constituents (kept,
            # labelled Index/Overlay so the screener/allocator treats them
            # as non-equity overlays).
            if "N/A" in sector:
                sector, sub_industry = "Index/Overlay", "Index/Overlay"

            symbol = strip_bloomberg_ticker(symbol_raw)
            if not symbol:
                continue
            out_rows.append({"Symbol": symbol, "GICS Sector": sector,
                             "GICS Sub-Industry": sub_industry})
    return pd.DataFrame(out_rows)


def parse_ilf_volumes() -> pd.DataFrame:
    """Extract PX_VOLUME columns from ILF.csv into a wide panel
    (same format as the price panel: date index, clean tickers)."""
    raw = pd.read_csv(os.path.join(ILF_DIR, "ILF.csv"), header=None, dtype=str)
    row0, row1 = raw.iloc[0].tolist(), raw.iloc[1].tolist()
    tickers = {}
    for i in range(1, len(row0)):
        val = row0[i]
        if isinstance(val, str) and val.strip():
            clean = strip_bloomberg_ticker(val)
            if clean:
                tickers[clean] = i
    dates = pd.to_datetime(raw.iloc[2:, 0].values, format="%m/%d/%Y", errors="coerce")
    data = {}
    for ticker, col_idx in tickers.items():
        data[ticker] = pd.to_numeric(raw.iloc[2:, col_idx].values, errors="coerce")
    df = pd.DataFrame(data, index=dates).dropna(axis=1, how="all").sort_index()
    df.index.name = None
    return df


def main():
    print("=" * 60)
    print("PREPARING ILF DATA FOR STAT-ARB PIPELINE")
    print("=" * 60)

    # 1. Parse price data
    prices = parse_ilf_prices()
    print(f"\n[prices] Shape: {prices.shape}")
    print(f"[prices] Date range: {prices.index.min()} to {prices.index.max()}")
    print(f"[prices] Tickers ({len(prices.columns)}): {list(prices.columns)}")

    # Market benchmark: the reference instrument for this universe. The
    # pipeline is configured with `market_ticker` (here "ILF"); the reference
    # is stored under its REAL name so reports/tapes/blotters show "ILF", not
    # a misleading "SPY". Download it from yfinance if absent.
    MARKET_TICKER = "ILF"
    if MARKET_TICKER in prices.columns:
        print(f"[prices] market reference '{MARKET_TICKER}' found in ILF data")
    else:
        print(f"[prices] Downloading {MARKET_TICKER} ETF from yfinance as market benchmark...")
        import yfinance as yf
        ypx = yf.download("ILF", start=str(prices.index.min().date()),
                          end=str(prices.index.max().date() + pd.Timedelta(days=1)),
                          progress=False, auto_adjust=False)
        if isinstance(ypx.columns, pd.MultiIndex):
            yclose = ypx["Close"].iloc[:, 0]
        else:
            yclose = ypx["Close"]
        yclose.index = pd.to_datetime(yclose.index)
        spy = yclose.reindex(prices.index)
        n_nan = spy.isna().sum()
        print(f"[prices] {MARKET_TICKER} column added. NaN dates: {n_nan} / {len(spy)}")
        # ffill short gaps (e.g. unfinished last session); leave leading NaNs
        spy = spy.bfill().ffill() if n_nan else spy
        prices[MARKET_TICKER] = spy

    out_price = os.path.join(DATA_DIR, "ILF_daily.csv")
    prices.to_csv(out_price)
    print(f"[prices] Saved: {out_price}")

    # 1b. Volume panel (for the relative-volume entry overlay)
    vols = parse_ilf_volumes()
    print(f"[volumes] Shape: {vols.shape} | tickers: {len(vols.columns)}")
    out_vol = os.path.join(DATA_DIR, "ILF_volume.csv")
    vols.to_csv(out_vol)
    print(f"[volumes] Saved: {out_vol}")

    # 2. Parse constituents
    cons = parse_ilf_constituents_gics()
    print(f"\n[constituents] Shape: {cons.shape}")
    print(f"[constituents] Tickers: {list(cons['Symbol'])}")

    sector_counts = cons["GICS Sector"].value_counts()
    print(f"\n[constituents] Sector distribution:")
    for sec, cnt in sector_counts.items():
        print(f"  {sec}: {cnt}")

    # Save as constituents_gics format
    out_cons_gics = os.path.join(DATA_DIR, "ILF_constituents_gics.csv")
    cons.to_csv(out_cons_gics, index=False)
    print(f"\n[constituents] Saved (gics): {out_cons_gics}")

    # Save as sector-only format
    cons_sector = cons[["Symbol", "GICS Sector"]].copy()
    out_cons = os.path.join(DATA_DIR, "ILF_constituents_sector.csv")
    cons_sector.to_csv(out_cons, index=False)
    print(f"[constituents] Saved (sector): {out_cons}")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()
