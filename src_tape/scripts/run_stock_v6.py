"""
Run alphacube v6 (stock-level residual reversal) over 10y and through the
fresh-start gate. Independent of the pair pipeline.

Usage: python scripts/run_stock_v6.py
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.stockalpha import backtest  # noqa: E402
from modules.execution.stats import compute_stats  # noqa: E402
from modules.validation.gate import window_stats, format_stats  # noqa: E402

PARAMS = dict(initial_capital=1_000_000.0, exec_fee=0.00015, borrow_bps=50.0,
              stock_ret_window=5, stock_beta_window=126, stock_rebal_days=5,
              stock_gross=1.5, stock_quantile=0.2, stock_min_sector_names=5,
              stock_warmup=130)

px = pd.read_csv("data/universe_daily_10y.csv", index_col=0, parse_dates=True)
px = px[~px.index.duplicated()].sort_index().ffill()
con = pd.read_csv("data/constituents.csv")
sym = "Symbol" if "Symbol" in con.columns else con.columns[0]
sectors = con.set_index(sym)["GICS Sector"]

def rets_of(slice_px, w=130):
    res, n = backtest(slice_px, sectors, PARAMS, start_idx=w)
    r = res.value.pct_change().fillna(0.0)
    return r, res, n

# ---- full 10y ----
print("=== v6 full 10y ===")
r, res, n = rets_of(px, 130)
st = compute_stats(res, float(res.gross_exposure.max()))
ann = ((1 + r).prod() ** (252 / len(r)) - 1) * 100
print(f"  rebalances={n}  total {(res.value.iloc[-1]/1e6-1)*100:+.1f}%  ann {ann:+.2f}%  "
      f"DD {float(st['Max Drawdown [%]']):.1f}%  vol {r.std()*np.sqrt(252)*100:.1f}%  "
      f"Sharpe {float(st['Sharpe Ratio']):+.2f}")
yr = (1 + r).groupby(r.index.year).prod() - 1
print("  yearly:", {y: round(v*100,1) for y,v in yr.items()})

# ---- fresh-start gate ----
print("=== v6 fresh-start gate (2y windows) ===")
starts = ["2018-10","2020-03","2021-09","2023-03","2024-06"]
idx = px.index
rets = []
for s in starts:
    pos = int(idx.searchsorted(pd.Timestamp(s)))
    lo, hi = max(0, pos-130), min(len(px), pos+504)
    sub = px.iloc[lo:hi]
    rr, _, _ = rets_of(sub, w=pos-lo)   # live starts exactly at `s`
    live = rr.iloc[pos-lo:]
    c = float((1+live).prod()-1)
    rets.append(c)
    print(f"  {s}: {c*100:+.2f}%")
gate = window_stats(rets)
print(format_stats("v6 FRESH-START gate", gate))
