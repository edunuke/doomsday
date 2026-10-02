"""Scrape S&P 500 constituents (Symbol, GICS Sector, GICS Sub-Industry) from
Wikipedia and save in the same format as data/constituents.csv plus a third
column. Output: data/constituents_gics.csv

Usage: python scripts/scrape_constituents.py
"""
from __future__ import annotations
import io, os, sys
import urllib.request
import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def main() -> int:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    t = pd.read_html(io.StringIO(html))[0]
    out = t[["Symbol", "GICS Sector", "GICS Sub-Industry"]].copy()
    out["Symbol"] = out["Symbol"].astype(str).str.strip()
    path = os.path.join(HERE, "data", "constituents_gics.csv")
    out.to_csv(path, index=False)
    print(f"wrote {path}: {len(out)} rows")
    print(f"  sectors={out['GICS Sector'].nunique()}  sub-industries={out['GICS Sub-Industry'].nunique()}")
    print(f"  sub-industry per sector: median "
          f"{out.groupby('GICS Sector')['GICS Sub-Industry'].nunique().median():.0f}, "
          f"max {out.groupby('GICS Sector')['GICS Sub-Industry'].nunique().max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
