"""
FACTOR MODEL — ex-ante, causal factor exposures for the allocator.

Factors (all price-derived, causal — computed from data up to date t):
  market      : rolling market-reference beta (shrunk)
  sector      : GICS one-hot (from constituents)
  momentum    : 12-1 month return, cross-sectionally z-scored
  volatility  : trailing realised vol, cross-sectionally z-scored
  growth      : PLUGGABLE. A true growth factor needs fundamentals, which this
                dataset does not contain. If ``fundamentals_path`` provides a
                per-ticker growth series it is used; otherwise the factor is
                DROPPED (never faked with momentum, which would alias it and
                double-count exposure).

Exposures feed the factor-neutral allocator as a matrix B (assets x factors) and
the risk model's factor block. Everything is point-in-time: no future data.
"""

from __future__ import annotations

import os
from typing import List, Optional

import numpy as np
import pandas as pd


def _cs_z(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score per row (date)."""
    mu = df.mean(axis=1)
    sd = df.std(axis=1).replace(0.0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0).fillna(0.0)


class FactorModel:
    def __init__(self, params: dict, prices: pd.DataFrame, sectors: pd.Series,
                 warnings: Optional[List[str]] = None):
        self.params = params
        self.prices = prices
        self.sectors = sectors
        self.ret = prices.pct_change().fillna(0.0)
        self._warn = warnings

        bw = int(params.get("factor_beta_window", 126))
        vw = int(params.get("factor_vol_window", 63))
        mo = int(params.get("factor_mom_window", 252))
        sk = int(params.get("factor_mom_skip", 21))

        self.market_ticker = str(params.get("market_ticker", "SPY"))
        trade = [c for c in prices.columns if c != self.market_ticker]
        self.assets = trade

        # market beta (rolling), shrunk toward cross-sectional mean
        if self.market_ticker in self.ret.columns:
            spy = self.ret[self.market_ticker]
            cov = self.ret.rolling(bw, min_periods=max(30, bw // 3)).cov(spy)
            var = spy.rolling(bw, min_periods=max(30, bw // 3)).var()
            beta = cov.div(var, axis=0)
            beta = beta.sub(beta.mean(axis=1), axis=0).add(1.0)  # centre on 1
            shr = float(params.get("factor_beta_shrink", 0.0))
            self._beta = beta * (1.0 - shr) + 1.0 * shr
        else:
            self._beta = pd.DataFrame(1.0, index=prices.index, columns=prices.columns)

        # volatility (z)
        vol = self.ret.rolling(vw, min_periods=max(20, vw // 3)).std()
        self._vol = _cs_z(vol[trade])

        # momentum 12-1 (z)
        mom = prices.shift(sk) / prices.shift(sk + mo) - 1.0
        self._mom = _cs_z(mom[trade])

        # growth (pluggable)
        self._growth: Optional[pd.DataFrame] = None
        self._growth_static: Optional[pd.Series] = None
        fpath = params.get("fundamentals_path")
        if fpath:
            p = fpath if os.path.isabs(fpath) else fpath
            if os.path.exists(p):
                f = pd.read_csv(p)
                gcol = params.get("fundamentals_growth_col", "growth")
                sym = "Symbol" if "Symbol" in f.columns else f.columns[0]
                if gcol in f.columns:
                    self._growth_static = f.set_index(sym)[gcol].astype(float)
        if self._growth_static is None and self._warn is not None:
            self._warn.append(
                "factor 'growth': no fundamentals file — factor DROPPED "
                "(a true growth factor needs fundamentals; not faked with momentum)")
        self.growth_enabled = self._growth_static is not None

        # ------------------------------------------------------------------
        # MACRO FACTORS (FX per country + commodities) — hedge-in-the-book.
        # The traded spread stays RAW (v4c lesson: residualising the spread
        # against FX/commodities destroys the signal); instead the allocator
        # constrains the BOOK's macro exposure to ~zero via these factor rows.
        # Each asset loads ONLY its home-country FX (from country_map_csv) plus
        # the global commodity factors; multi-country names get globals only.
        # Betas are rolling, causal (window ends at t), shrunk toward 0.
        # ------------------------------------------------------------------
        self.macro_enabled = bool(params.get("macro_hedge_enabled", False))
        self._macro_cols: dict = {}          # factor name -> rolling beta df
        if self.macro_enabled:
            _fcsv = params.get("factors_csv")
            _ccsv = params.get("country_map_csv")
            if not _fcsv or not os.path.exists(_fcsv):
                self.macro_enabled = False
                if self._warn is not None:
                    self._warn.append(
                        "macro_hedge_enabled but factors_csv missing — macro factors DROPPED")
            else:
                fpx = pd.read_csv(_fcsv, index_col=0, parse_dates=True).sort_index()
                fpx = fpx.reindex(prices.index).fillna(0.0)
                cmap = {}
                if _ccsv and os.path.exists(_ccsv):
                    _c = pd.read_csv(_ccsv)
                    _c.columns = [str(x).strip().title() if x.lower() == "country" else str(x).strip()
                                  for x in _c.columns]
                    sc = "Symbol" if "Symbol" in _c.columns else _c.columns[0]
                    cc = "Country" if "Country" in _c.columns else _c.columns[1]
                    cmap = _c.set_index(sc)[cc].astype(str).str.strip().to_dict()

                _mw = int(params.get("factor_macro_beta_window", bw))
                for col in fpx.columns:
                    f = fpx[col]
                    if col in ("copper", "oil"):
                        fname = f"cmd:{col}"
                        load_all = True
                    else:
                        fname = f"fx:{col}"
                        load_all = False
                    var = f.rolling(_mw, min_periods=max(20, _mw // 3)).var()
                    if load_all:
                        beta = self.ret[trade].rolling(
                            _mw, min_periods=max(20, _mw // 3)).cov(f).div(var, axis=0)
                    else:
                        # own-country FX only: assets whose home country == col
                        own = pd.Series([cmap.get(a, "") == col for a in trade],
                                        index=trade)
                        if not own.any():
                            continue
                        b = self.ret[trade].rolling(
                            _mw, min_periods=max(20, _mw // 3)).cov(f).div(var, axis=0)
                        # indicator multiply (NOT .where — a Series condition
                        # aligns on the date index, silently zeroing everything)
                        beta = b.mul(own.astype(float), axis=1)
                    shr = float(params.get("factor_macro_shrink", 0.0))
                    self._macro_cols[fname] = beta * (1.0 - shr)
                if self._warn is not None and not self._macro_cols:
                    self._warn.append(
                        "macro_hedge_enabled but no usable macro factors — DROPPED")

    # ------------------------------------------------------------------ factors
    def factor_names(self) -> List[str]:
        secs = [f"sec:{s}" for s in sorted(self.sectors.dropna().unique())]
        names = ["market", "momentum", "volatility"] + secs
        if self.growth_enabled:
            names.insert(3, "growth")
        names += list(self._macro_cols.keys())
        return names

    def beta(self, assets: List[str]) -> pd.Series:
        """Latest causal market-reference beta per asset (row = last date)."""
        last = self._beta.index[-1]
        return self._beta.loc[last].reindex(assets).fillna(1.0)

    def exposure_frame(self, date, assets: List[str]) -> pd.DataFrame:
        """B: assets x factors, point-in-time at ``date``."""
        idx = self._beta.index
        pos = idx.get_indexer([pd.Timestamp(date)])[0]
        if pos < 0:
            pos = len(idx) - 1
        ts = idx[pos]
        cols = ["market", "momentum", "volatility"]
        B = pd.DataFrame(index=assets)
        B["market"] = self._beta.loc[ts].reindex(assets).fillna(1.0).values
        B["momentum"] = self._mom.loc[ts].reindex(assets).fillna(0.0).values
        B["volatility"] = self._vol.loc[ts].reindex(assets).fillna(0.0).values
        if self.growth_enabled:
            g = self._growth_static.reindex(assets)
            g = (g - g.mean()) / (g.std() or 1.0)
            B["growth"] = g.fillna(0.0).values
        sec = self.sectors.reindex(assets).fillna("Unknown")
        for s in sorted(sec.unique()):
            B[f"sec:{s}"] = (sec == s).astype(float).values
        # macro factor rows (FX per country + commodities), point-in-time
        for fname, bdf in self._macro_cols.items():
            B[fname] = bdf.loc[ts].reindex(assets).fillna(0.0).values
        return B

    def tolerances(self) -> dict:
        """Institutional ex-ante factor bounds (defaults; override in config)."""
        t = {"market": 0.02, "momentum": 0.15, "volatility": 0.15, "growth": 0.15}
        for s in self.sectors.dropna().unique():
            t[f"sec:{s}"] = 0.01
        for fname in self._macro_cols:
            # macro neutrality: tight default; per-factor override via config
            t[fname] = 0.05
        t.update(self.params.get("factor_tolerances", {}) or {})
        return t