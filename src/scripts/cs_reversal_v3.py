"""Composite multi-horizon cross-sectional residual reversal.

Signal = average of standardised reversal across k in {1,2,3,5}.
Sharpe/IC should improve via diversification of horizons.
Net of turnover-proportional costs. Also reports win rate.
"""
import numpy as np
import pandas as pd

PX = "data/universe_daily_long.csv"
OOS = pd.Timestamp("2021-09-03")

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
KS = [1, 2, 3, 5]


def zscore_row(s):
    s = s.dropna()
    if len(s) < 20:
        return s
    return (s - s.mean()) / (s.std() + 1e-12)


def run(reb, cost_bps, long_short_frac=0.1, gross=2.0):
    prev_w = pd.Series(0.0, index=R.columns)
    rows = []
    for t in range(0, len(dates) - reb, reb):
        comp = None
        for k in KS:
            sig_k = -R.iloc[max(0, t - k):t + 1].sum()
            z = zscore_row(sig_k)
            comp = z if comp is None else comp.add(z, fill_value=0.0)
        comp = zscore_row(comp)
        r = comp.rank(pct=True)
        w = pd.Series(0.0, index=R.columns)
        w[r >= 1 - long_short_frac] = 1.0
        w[r <= long_short_frac] = -1.0
        if w.abs().sum() == 0:
            continue
        w = w / w.abs().sum() * gross
        turn = float((w - prev_w).abs().sum())
        fwd = R.iloc[t + 1:t + 1 + reb].sum()
        net = float((w * fwd).sum()) - turn * cost_bps / 10000.0
        rows.append((net, turn))
        prev_w = w
    arr = np.array(rows)
    net = pd.Series(arr[:, 0]); turn = pd.Series(arr[:, 1])
    bpy = 252.0 / reb
    ann = net.mean() * bpy * 100
    sh = net.mean() / net.std() * np.sqrt(bpy) if net.std() else 0
    cum = (1 + net).cumprod(); dd = (1 - cum / cum.cummax()).max() * 100
    win = (net > 0).mean() * 100
    return ann, sh, turn.mean() / 2 * 100, win, dd


print(f"{'reb':>4} {'cost':>5} {'ls%':>5} {'ann net%':>9} {'Sharpe':>7} {'turn%':>7} {'win%':>6} {'maxDD%':>7}")
for reb in [2, 3, 5]:
    for cost in [1.5, 3.0]:
        for ls in [0.10, 0.20]:
            a, s, t_, w_, d_ = run(reb, cost, ls)
            print(f"{reb:>4} {cost:>5.1f} {ls*100:>4.0f}% {a:>8.2f}% {s:>7.2f} {t_:>6.1f}% {w_:>5.1f}% {d_:>6.1f}%")
