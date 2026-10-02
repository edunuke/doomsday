"""
STOCK-LEVEL RESIDUAL REVERSAL (alphacube v6) — independent strategy.

A different engine from pair cointegration. Each rebalance:

  1. excess_i = r_i - beta_i * r_market                     (market-neutral signal)
  2. demean excess cross-sectionally WITHIN GICS sector  (sector-neutral signal)
  3. signal_i = -sum(excess over `ret_win` days)         (short-horizon reversal)
  4. within each sector, long the most negative residual quantile and short the
     most positive; equal-weight per side, dollar-neutral; market-reference overlay offsets
     net book beta                                            (beta-neutral)

Tradable, causal (all features use data up to t), T+1 executed via the shared
portfolio engine. No pair/cointegration dependence.
"""
from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd

from modules.execution.portfolio_engine import run_target_weights


def build_signal(prices: pd.DataFrame, sectors: pd.Series,
                 ret_win: int = 5, beta_win: int = 126,
                 market_ticker: str = "SPY"):
    ret = prices.pct_change(fill_method=None)
    spy = ret[market_ticker] if market_ticker in ret.columns else ret.mean(axis=1)
    win = max(30, beta_win)
    beta = ret.rolling(win, min_periods=win // 3).cov(spy).div(
        spy.rolling(win, min_periods=win // 3).var(), axis=0)
    excess = ret.sub(beta.mul(spy, axis=0))
    cum = excess.rolling(ret_win, min_periods=ret_win).sum()
    # cross-sectional demean within sector (each day)
    dem = cum.copy()
    sec = sectors.reindex(cum.columns)
    for s in sec.dropna().unique():
        cols = [c for c in sec[sec == s].index if c in cum.columns]
        if cols:
            dem[cols] = cum[cols].sub(cum[cols].mean(axis=1), axis=0)
    return -dem, beta           # signal: buy negative residual, short positive


def target_weights(prices: pd.DataFrame, sectors: pd.Series, params: dict):
    ret_win = int(params.get("stock_ret_window", 5))
    beta_win = int(params.get("stock_beta_window", 126))
    freq = int(params.get("stock_rebal_days", 5))
    gross = float(params.get("stock_gross", 1.5))
    quant = float(params.get("stock_quantile", 0.2))
    min_sector = int(params.get("stock_min_sector_names", 5))
    warm = int(params.get("stock_warmup", 130))

    _mkt = str(params.get("market_ticker", "SPY"))
    signal, beta = build_signal(prices, sectors, ret_win, beta_win, _mkt)
    W = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    n_reb = 0
    for i in range(warm, len(prices), freq):
        d = prices.index[i]
        s = signal.iloc[i].dropna()
        if s.empty:
            continue
        row = pd.Series(0.0, index=prices.columns)
        longs, shorts = [], []
        for sec_name, grp in sectors.groupby(sectors):
            cols = [c for c in grp.index if c in s.index]
            if len(cols) < min_sector:
                continue
            sv = s[cols].sort_values()
            k = max(1, int(len(sv) * quant))
            longs += list(sv.index[:k])        # most negative residual
            shorts += list(sv.index[-k:])      # most positive residual
        if not longs or not shorts:
            continue
        wl, ws = (gross / 2) / len(longs), -(gross / 2) / len(shorts)
        for c in longs:
            row[c] = wl
        for c in shorts:
            row[c] = ws
        # beta hedge on the net book
        b = beta.iloc[i].reindex(prices.columns).fillna(0.0)
        net_beta = float((row * b).sum())
        if _mkt in row.index:
            row[_mkt] = -net_beta
        W.loc[d] = row.values
        n_reb += 1
    return W, n_reb


def backtest(prices: pd.DataFrame, sectors: pd.Series, params: dict,
             start_idx: Optional[int] = None):
    W, n_reb = target_weights(prices, sectors, params)
    exec_w = W.shift(1)
    if start_idx is not None:
        exec_w = exec_w.iloc[start_idx:]
        prices = prices.iloc[start_idx:]
    res = run_target_weights(
        close=prices, open_=prices, weights=exec_w,
        init_cash=float(params.get("initial_capital", 1_000_000.0)),
        exec_fee=float(params.get("exec_fee", 0.00015)),
        daily_borrow_rate=(float(params.get("borrow_bps", 50.0)) / 10000.0) / 252.0)
    return res, n_reb
