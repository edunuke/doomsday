"""Cross-sectional residual reversal with hysteresis (buffer bands).

Reduces turnover by holding positions until they leave a WIDE band
(enter top/bottom 10%; exit only when beyond top/bottom 30%). Tests whether
this preserves IC while cutting the cost drag.
"""
import numpy as np
import pandas as pd

PX = "data/universe_daily_long.csv"
OOS = pd.Timestamp("2021-09-03")
COST_BPS = 3.0

px = pd.read_csv(PX, index_col=0, parse_dates=True)
spy = px["SPY"]; stocks = px.drop(columns=["SPY"])
ret = stocks.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
mkt = spy.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
const = pd.read_csv("data/constituents.csv")
sym = "Symbol" if "Symbol" in const.columns else "Ticker"
smap = dict(zip(const[sym], const["GICS Sector"]))
cols = list(stocks.columns); sarr = np.array([smap.get(c, "Other") for c in cols])
sec_ret = {}
for s in np.unique(sarr):
    idx = np.where(sarr == s)[0]
    sec_ret[s] = ret.iloc[:, idx].mean(axis=1)
sec_for = np.column_stack([sec_ret[s].values for s in sarr])

tr = ret.index < OOS
Y = ret.values; M = mkt.values; res = np.zeros_like(Y)
for j in range(Y.shape[1]):
    X = np.column_stack([np.ones(len(Y)), M, sec_for[:, j]])
    Xtr, ytr = X[tr], Y[tr, j]
    ok = np.isfinite(Xtr).all(axis=1) & np.isfinite(ytr)
    Xtr, ytr = Xtr[ok], ytr[ok]
    if len(ytr) < 50 or Xtr[:, 1:].std(axis=0).min() < 1e-9:
        continue
    b = np.linalg.pinv(Xtr.T @ Xtr) @ (Xtr.T @ ytr)
    res[:, j] = Y[:, j] - X @ b
R = pd.DataFrame(res, index=ret.index, columns=stocks.columns).fillna(0.0)
R = R.loc[R.index >= OOS]
dates = R.index

def run(k, enter, exit_b, gross=2.0, cost_bps=COST_BPS):
    held = pd.Series(0.0, index=R.columns)   # -1/0/+1 target side
    prev_w = pd.Series(0.0, index=R.columns)
    rows = []
    for t in range(0, len(dates) - k, k):
        sig = -R.iloc[max(0, t - k):t + 1].sum()
        r = sig.rank(pct=True)
        new_side = held.copy()          # hysteresis: hold prior side by default
        new_side[r >= 1 - enter] = 1.0
        new_side[r <= enter] = -1.0
        w = new_side / max(new_side.abs().sum(), 1e-9) * gross
        turn = float((w - prev_w).abs().sum())
        fwd = R.iloc[t + 1:t + 1 + k].sum()
        gross_r = float((w * fwd).sum())
        net = gross_r - turn * cost_bps / 10000.0
        rows.append((net, gross_r, turn))
        held = new_side; prev_w = w
    arr = np.array(rows)
    net = pd.Series(arr[:, 0]); g = pd.Series(arr[:, 1]); turn = pd.Series(arr[:, 2])
    bpy = 252.0 / k
    ann_n = net.mean() * bpy * 100
    sh = net.mean() / net.std() * np.sqrt(bpy) if net.std() else 0
    cum = (1 + net).cumprod(); dd = (1 - cum / cum.cummax()).max() * 100
    win = (net > 0).mean() * 100
    return ann_n, sh, turn.mean() / 2 * 100, win, dd

print(f"{'k':>3} {'enter':>6} {'exit':>5} {'ann net%':>9} {'Sharpe':>7} {'turn%':>7} {'win%':>6} {'maxDD%':>7}")
for k in [2, 3, 5]:
    for enter, exit_b in [(0.10, 0.30), (0.10, 0.40), (0.20, 0.50)]:
        a, s, t_, w_, d_ = run(k, enter, exit_b)
        print(f"{k:>3} {enter:>6.2f} {exit_b:>5.2f} {a:>8.2f}% {s:>7.2f} {t_:>6.1f}% {w_:>5.1f}% {d_:>6.1f}%")
