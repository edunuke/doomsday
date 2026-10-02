"""
UNIVERSE SCREENER

Maps tickers to GICS sectors from the constituents CSV.
"""

import os
from typing import List

import pandas as pd


class UniverseScreener:
    """Resolves GICS sector strings for a list of tickers."""

    def __init__(self, constituents_path: str, sector_column: str = "GICS Sector"):
        self.constituents_path = constituents_path
        self.sector_column = sector_column

    def get_sectors(self, tickers: List[str], column: str = None) -> pd.Series:
        col = column or self.sector_column
        if os.path.exists(self.constituents_path):
            const = pd.read_csv(self.constituents_path)
            sym_col = "Symbol" if "Symbol" in const.columns else "Ticker"
            if col not in const.columns:
                col = "GICS Sector"
            lookup = dict(zip(const[sym_col], const[col]))
            return pd.Series(tickers, index=tickers).map(lookup).fillna("Unknown")
        return pd.Series("Unknown", index=tickers)