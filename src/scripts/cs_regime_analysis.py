"""REGIME ANALYSIS FOR THE CS REVERSAL STRATEGY (fast iteration)

Joins the per-rebalance period returns (from a run's progressive_metrics.csv)
with candidate EX-ANTE regime indicators computed from prices up to each
rebalance date. Reports which indicator separates good vs bad periods, and the
performance of a regime gate that only trades in favourable regimes.

No full backtest needed -> fast iteration for regime-signal design.
"""
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PROG = sys.argv[1] if len(sys.argv) > 1 else "results/42/csv/progressive_metrics.csv"
PXF = "data/universe_daily_long.csv"

prog = pd.read_csv(PROG)
prog["date"] = pd.to_datetime(prog["date"])
prog = prog.set_index("date").sort_index()
pr = prog["period_return_%"] / 100.0     # forward period return

px = pd.read_csv(PXF, index_col=0, parse_dates=True)
spy = px["SPY"]
stocks = px.drop(columns=["SPY"])
ret = stocks.pct_change().replace([np.inf, -np.inf], np.nan)
mkt = spy.pct_change()

# ---- EX-ANTE regime indicators (trailing only) ----
ind = pd.DataFrame(index=px.index)
ind["xs_disp_21"] = ret.std(axis=1).rolling(21).mean()            # cross-sectional dispersion
ind["spy_vol_21"] = mkt.rolling(21).std()                          # market realised vol
ind["spy_vol_63"] = mkt.rolling(63).std()
ind["trend_21"] = spy.pct_change(21)                               # market trend (momentum)
ind["trend_63"] = spy.pct_change(63)
ind["avg_corr_63"] = (ret.rolling(63).corr(mkt).mean(axis=1))      # avg corr to market
ind["breadth_50"] = (stocks > stocks.rolling(50).mean()).mean(axis=1)  # % above 50d MA
ind["resid_vol_21"] = (ret.sub(mkt, axis=0)).std(axis=1).rolling(21).mean()  # idio vol

# align to rebalance dates (use last available value up to each date)
common = pr.index.intersection(ind.index)
pr = pr.loc[common]
X = ind.loc[common].ffill()

print(f"rebalance periods: {len(pr)}   {common[0].date()} -> {common[-1].date()}\n")
print(f"{'indicator':<16} {'IC':>8} {'t-stat':>8} {'low-regime%':>12} {'high-regime%':>13} {'sep(pp)':>8}")
print("-" * 78)
results = {}
for c in X.columns:
    x = X[c]
    m = x.notna() & pr.notna()
    if m.sum() < 20:
        continue
    ic = spearmanr(x[m], pr[m]).correlation
    t = ic * np.sqrt(m.sum() - 2) / np.sqrt(1 - ic ** 2) if abs(ic) < 1 else 0
    med = x[m].median()
    lo = pr[m][x[m] <= med].mean() * 100
    hi = pr[m][x[m] > med].mean() * 100
    results[c] = (ic, abs(hi - lo))
    print(f"{c:<16} {ic:>8.3f} {t:>8.2f} {lo:>11.3f}% {hi:>12.3f}% {hi-lo:>7.3f}")

print("\n" + "=" * 78)
print("REGIME GATE — trade only when indicator is in the favourable half")
print("=" * 78)
# pick the two strongest separators
best = sorted(results.items(), key=lambda kv: -kv[1][1])[:3]
print(f"{'gate (long when)':<26} {'periods in':>10} {'cum ret%':>10} {'win%':>6} {'Sharpe':>7}")
for c, _ in best:
    x = X[c]; med = x.median()
    sign = np.sign(results[c][0])
    mask = (x >= med) if sign > 0 else (x <= med)      # favourable half
    sub = pr[mask.fillna(False)]
    if len(sub) < 10:
        continue
    cum = float((1 + sub).prod() - 1) * 100
    sh = float(sub.mean() / sub.std() * np.sqrt(252 / 3)) if sub.std() else 0
    win = float((sub > 0).mean() * 100)
    print(f"{c + (' HIGH' if sign>0 else ' LOW'):<26} {len(sub):>10} {cum:>9.2f}% {win:>5.1f}% {sh:>7.2f}")
print("\n(baseline ungated: cum %.2f%%, win %.1f%%)" % (
    float((1 + pr).prod() - 1) * 100, float((pr > 0).mean() * 100)))
