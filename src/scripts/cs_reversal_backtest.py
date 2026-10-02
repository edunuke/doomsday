"""Cross-sectional residual-reversal — NET backtest (non-overlapping).

Rebalance every k days: long top decile of the reversal signal, short bottom,
dollar-neutral, gross 2x. Charges turnover-proportional costs.
"""
import numpy as np
import pandas as pd

PX = "data/universe_daily_long.csv"
OOS = pd.Timestamp("2021-09-03")
COST_BPS = 3.0          # per unit turnover (round trip ~2 bps/leg + slippage)

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

print(f"{'k':>3} {'ann gross%':>11} {'ann net%':>9} {'Sharpe net':>11} {'turnover%':>10} {'win% mo':>8} {'maxDD%':>8}")
for k in [1, 2, 3, 5, 10]:
    prev_w = pd.Series(0.0, index=R.columns)
    rows = []
    for t in range(0, len(dates) - k, k):         # non-overlapping k-day blocks
        d = dates[t]
        sig = -R.iloc[max(0, t - k):t + 1].sum() if t >= 1 else -R.iloc[0]
        m = sig.notna()
        if m.sum() < 50:
            continue
        r = sig[m].rank(pct=True)
        w = pd.Series(0.0, index=R.columns)
        w[r.index] = np.where(r >= 0.9, 1.0, np.where(r <= 0.1, -1.0, 0.0))
        if w.abs().sum() == 0:
            continue
        w = w / w.abs().sum() * 2.0                # gross 2x, dollar-neutral
        turn = float((w - prev_w).abs().sum())
        fwd = R.iloc[t + 1:t + 1 + k].sum()        # held k days
        gross = float((w * fwd).sum())
        net = gross - turn * COST_BPS / 10000.0
        rows.append((net, gross, turn))
        prev_w = w
    if not rows:
        continue
    arr = np.array(rows)
    net = pd.Series(arr[:, 0]); gross = pd.Series(arr[:, 1]); turn = pd.Series(arr[:, 2])
    blocks_per_year = 252.0 / k
    ann_g = gross.mean() * blocks_per_year * 100
    ann_n = net.mean() * blocks_per_year * 100
    sh = net.mean() / net.std() * np.sqrt(blocks_per_year) if net.std() else 0
    cum = (1 + net).cumprod()
    dd = ((1 - cum / cum.cummax()).max()) * 100
    win = (net > 0).mean() * 100
    print(f"{k:>3} {ann_g:>10.2f}% {ann_n:>8.2f}% {sh:>11.2f} {turn.mean()/2*100:>9.1f}% {win:>7.1f}% {dd:>7.1f}%")

print(f"\nCost assumption: {COST_BPS} bps per unit turnover.")
