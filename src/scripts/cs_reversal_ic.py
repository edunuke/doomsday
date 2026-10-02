"""Cross-sectional residual-reversal IC study.

Tests whether factor-neutral RESIDUAL reversal has cross-sectional predictive
power — the high-breadth formulation that could reach the target Sharpe.

Signal : -cumulative factor-neutral residual return over past k days
Forward: factor-neutral residual return over next k days
IC     : cross-sectional Spearman rank correlation, averaged over days
Portfolio: decile long-short of the signal, dollar-neutral, gross 2x
"""
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PXF = "data/universe_daily_long.csv"
OOS_START = pd.Timestamp("2021-09-03")

px = pd.read_csv(PXF, index_col=0, parse_dates=True)
spy = px["SPY"]
stocks = px.drop(columns=["SPY"])
ret = stocks.pct_change().replace([np.inf,-np.inf],np.nan).fillna(0.0)
mkt = spy.pct_change().replace([np.inf,-np.inf],np.nan).fillna(0.0)

# sector mapping
const = pd.read_csv("data/constituents.csv")
sym = "Symbol" if "Symbol" in const.columns else "Ticker"
smap = dict(zip(const[sym], const["GICS Sector"]))
cols = list(stocks.columns)
sarr = np.array([smap.get(c, "Other") for c in cols])
sec_ret = {}
for s in np.unique(sarr):
    idx = np.where(sarr == s)[0]
    sec_ret[s] = ret.iloc[:, idx].mean(axis=1)
sec_for = np.column_stack([sec_ret[s].values for s in sarr])  # T x N

# factor-neutral residual returns: r - beta_mkt*mkt - beta_sec*sec
# betas fixed from pre-OOS window (no look-ahead)
tr = ret.index < OOS_START
Y = ret.values
M = mkt.values
res = np.zeros_like(Y)
for j in range(Y.shape[1]):
    X = np.column_stack([np.ones(len(Y)), M, sec_for[:, j]])
    Xtr, ytr = X[tr], Y[tr, j]
    ok = np.isfinite(Xtr).all(axis=1) & np.isfinite(ytr)
    Xtr, ytr = Xtr[ok], ytr[ok]
    if len(ytr) < 50 or Xtr[:,1:].std(axis=0).min() < 1e-9:
        continue
    b = np.linalg.pinv(Xtr.T @ Xtr) @ (Xtr.T @ ytr)
    res[:, j] = Y[:, j] - X @ b
R = pd.DataFrame(res, index=ret.index, columns=stocks.columns).fillna(0.0)
R_oos = R.loc[R.index >= OOS_START]

print(f"OOS residual panel: {R_oos.shape[0]} days x {R_oos.shape[1]} stocks")
print(f"sample ann residual vol: {R_oos.std().mean()*np.sqrt(252)*100:.1f}%\n")

print(f"{'k':>3} {'IC mean':>9} {'IC t-stat':>10} {'ICIR':>7} {'decile LS ann%':>15} {'LS Sharpe':>10}")
for k in [1, 2, 3, 5, 10]:
    sig = -R_oos.rolling(k).sum()                 # reversal signal
    fwd = R_oos.shift(-1).rolling(k).sum().shift(-(k-1))  # next-k forward
    ics = []
    for d in R_oos.index:
        s = sig.loc[d]; f = fwd.loc[d]
        m = s.notna() & f.notna()
        if m.sum() < 50:
            continue
        ics.append(spearmanr(s[m], f[m]).correlation)
    ics = np.array([x for x in ics if np.isfinite(x)])
    ic_mean, ic_std = ics.mean(), ics.std()
    icir = ic_mean / ic_std if ic_std else 0

    # decile long-short (gross 2x, equal weight)
    ls = []
    for d in R_oos.index:
        s = sig.loc[d]; f = fwd.loc[d]
        m = s.notna() & f.notna()
        if m.sum() < 50:
            ls.append(0.0); continue
        ss, ff = s[m], f[m]
        q = pd.qcut(ss.rank(method="first"), 10, labels=False)
        ls.append(ff[q == 9].mean() - ff[q == 0].mean())   # long high signal, short low
    ls = pd.Series(ls, index=R_oos.index).dropna()
    ann = ls.mean() * 252 * 100
    sharpe = ls.mean() / ls.std() * np.sqrt(252) if ls.std() else 0
    print(f"{k:>3} {ic_mean:>9.4f} {ic_mean/ (ic_std/np.sqrt(len(ics))):>10.2f} {icir:>7.3f} {ann:>14.2f}% {sharpe:>10.2f}")

print("\nNote: decile LS is GROSS (no costs). Costs ~2-4bps/day/leg apply.")
