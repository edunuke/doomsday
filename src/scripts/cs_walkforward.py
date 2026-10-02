"""CROSS-SECTIONAL RESIDUAL-REVERSAL — WALK-FORWARD HARNESS

Same methodology as the pairwise engine:
  * warmup window -> betas fit, then walk forward
  * periodic retrain (refit residualisation betas) every `retrain_days`
  * T+1 execution via vectorbt target-percent orders
  * SPY overlay to neutralise net market beta
  * same tearsheet + metrics exported to results/{N}/

Usage: python cs_walkforward.py [config.json]
"""
import os
import sys
import json
import numpy as np
import pandas as pd
import statsmodels.api as sm
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, "/Users/edunuke/Desktop/statarb-prival/src")
from modules.execution.backtest_engine import VectorbtBacktestEngine
from modules.core.runs import get_next_run_dir
from modules.risk.benchmark import sofr_daily_returns, benchmark_summary, print_benchmark_block

CFG_PATH = sys.argv[1] if len(sys.argv) > 1 else None
DEFAULTS = {
    "price_csv": "data/universe_daily_long.csv",
    "lookback_days": 1260,
    "reb": 3,
    "retrain_days": 90,
    "ks": [1, 2, 3, 5],
    "ls_frac": 0.20,
    "gross": 2.0,
    "exec_fee": 0.00015,
    "borrow_bps": 50.0,
    "initial_capital": 250000.0,
    "max_spy_overlay": 0.55,
    "oos_start": "2021-09-03",
    "regime_gate": "avg_corr",     # "none" | "avg_corr"
    "regime_gate_q": 0.30,         # ROLLING percentile threshold (adaptive)
    "regime_gate_window": 252,     # rolling window for the threshold
}
cfg = dict(DEFAULTS)
if CFG_PATH and os.path.exists(CFG_PATH):
    cfg.update(json.load(open(CFG_PATH)))

from modules.core.logger import LogCapture

out = get_next_run_dir("results")
os.makedirs(f"{out}/csv", exist_ok=True)
os.makedirs(f"{out}/imgs", exist_ok=True)
json.dump(cfg, open(f"{out}/config.json", "w"), indent=2)
capture = LogCapture(verbose=True, log_path=f"{out}/logs.txt")
sys.stdout = capture

print("=" * 80)
print("CROSS-SECTIONAL RESIDUAL REVERSAL — WALK-FORWARD")
print(f"Results dir: {out}")
for k, v in cfg.items():
    print(f"  {k:<18} {v}")
print("=" * 80)

# ---------------------------------------------------------------- load
px = pd.read_csv(cfg["price_csv"], index_col=0, parse_dates=True).ffill()
spy = px["SPY"]
stocks = px.drop(columns=["SPY"])
ret = stocks.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
mkt = spy.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
open_px = px.copy()  # no separate open file -> use close (same as engine fallback)

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

start_idx = int(px.index.searchsorted(pd.Timestamp(cfg["oos_start"])))

# ---- EX-ANTE regime signal: trailing 63d avg stock correlation to market ----
avg_corr = ret.rolling(63).corr(mkt).mean(axis=1)
_gq = float(cfg.get("regime_gate_q", 0.30))
_gw = int(cfg.get("regime_gate_window", 252))
# Adaptive threshold: rolling percentile of avg_corr (ex-ante, no look-ahead)
gate_thr_series = avg_corr.rolling(_gw, min_periods=60).quantile(_gq)
print(f"[CS] regime gate={cfg.get('regime_gate')}  rolling q={_gq} window={_gw}d "
      f"(adaptive threshold; ex-ante)")
LOOKBACK = int(cfg["lookback_days"])
N = len(cols)
print(f"[CS] loaded {len(px)} days x {N} stocks; OOS start_idx={start_idx} "
      f"({px.index[start_idx].date()}), warmup={LOOKBACK}")


def fit_and_residual(t):
    """Refit betas on trailing LOOKBACK window ending at t; return residual panel."""
    lo = max(0, t - LOOKBACK)
    Ytr = ret.values[lo:t]
    Mtr = mkt.values[lo:t]
    Yall = ret.values
    Mall = mkt.values
    R = np.zeros_like(Yall)
    betas = np.zeros((N, 3))
    for j in range(N):
        X = np.column_stack([np.ones(len(Yall)), Mall, sec_for[:, j]])
        Xtr, ytr = X[lo:t], Ytr[:, j]
        ok = np.isfinite(Xtr).all(axis=1) & np.isfinite(ytr)
        if ok.sum() < 50 or Xtr[ok][:, 1:].std(axis=0).min() < 1e-9:
            betas[j] = [0, 0, 0]
            continue
        b = np.linalg.pinv(Xtr[ok].T @ Xtr[ok]) @ (Xtr[ok].T @ ytr[ok])
        betas[j] = b
        R[:, j] = Yall[:, j] - X @ b
    Rdf = pd.DataFrame(R, index=ret.index, columns=stocks.columns).fillna(0.0)
    return Rdf, betas


def zrow(s):
    s = s.dropna()
    if len(s) < 20:
        return s
    return (s - s.mean()) / (s.std() + 1e-12)


# ---------------------------------------------------------------- walk forward
weights = pd.DataFrame(np.nan, index=px.index, columns=px.columns, dtype=float)
rebal_dates = []
R = None
betas = None
last_fit = -10**9
ks = cfg["ks"]
ls = cfg["ls_frac"]

for t in range(start_idx, len(px)):
    if R is None or (t - last_fit) >= int(cfg["retrain_days"]):
        R, betas = fit_and_residual(t)
        last_fit = t
        print(f"[CS] retrain betas @ {px.index[t].date()} (rows {max(0,t-LOOKBACK)}:{t})")

    if (t - start_idx) % int(cfg["reb"]) != 0:
        continue

    comp = None
    for k in ks:
        sig = -R.iloc[max(0, t - k):t + 1].sum()
        z = zrow(sig)
        comp = z if comp is None else comp.add(z, fill_value=0.0)
    comp = zrow(comp)
    if len(comp) < 50:
        continue
    r = comp.rank(pct=True)
    # ---- regime gate: no edge when correlation regime is high -> go flat ----
    _ac = avg_corr.iloc[t]
    _thr = gate_thr_series.iloc[t]
    gate_off = (str(cfg.get("regime_gate", "none")) == "avg_corr"
                and np.isfinite(_ac) and np.isfinite(_thr) and _ac > _thr)
    if gate_off:
        w = pd.Series(0.0, index=stocks.columns)
        net_beta, overlay = 0.0, 0.0
        rebal_dates.append(px.index[t])
        weights.loc[px.index[t], stocks.columns] = w.values
        weights.loc[px.index[t], "SPY"] = overlay
        print(f"\n[Rebalance @ {px.index[t].date()} | GATE OFF "
              f"(avg_corr={_ac:.3f} > thr {_thr:.3f})] flat")
        continue
    w = pd.Series(0.0, index=stocks.columns)
    w[r >= 1 - ls] = 1.0
    w[r <= ls] = -1.0
    if w.abs().sum() == 0:
        continue
    w = w / w.abs().sum() * cfg["gross"]

    # net SPY beta of the stock book -> hedge with SPY overlay
    b_mkt = betas[:, 1]
    net_beta = float(np.nansum(w.values * b_mkt))
    overlay = float(np.clip(-net_beta, -cfg["max_spy_overlay"], cfg["max_spy_overlay"]))
    # ---- per-rebalance audit log (mirrors pairwise engine granularity) ----
    n_long = int((w > 0).sum()); n_short = int((w < 0).sum())
    top_l = w[w > 0].nlargest(3); top_s = w[w < 0].nsmallest(3)
    prog = (t - start_idx + 1) / (len(px) - start_idx) * 100
    print(f"\n[Rebalance @ {px.index[t].date()} | Progress: {prog:5.1f}% "
          f"({t - start_idx + 1}/{len(px) - start_idx})]")
    print(f"  --> Book: Long={n_long} | Short={n_short} | Gross={w.abs().sum():.2f} | "
          f"NetBeta={net_beta:+.3f} | SPY Overlay={overlay:+.1%}")
    print("  --> Top Longs : " + ", ".join(f"{c}({w[c]:+.1%})" for c in top_l.index))
    print("  --> Top Shorts: " + ", ".join(f"{c}({w[c]:+.1%})" for c in top_s.index))
    print("-" * 60)

    rebal_dates.append(px.index[t])
    weights.loc[px.index[t], stocks.columns] = w.values
    weights.loc[px.index[t], "SPY"] = overlay

weights = weights.ffill().fillna(0.0)
print(f"[CS] walk-forward complete; rebalance days = {(weights.abs().sum(axis=1) > 0).sum()}")

# ---------------------------------------------------------------- backtest
engine = VectorbtBacktestEngine(cfg)
oos = px.index[start_idx:]
close = px.loc[oos, weights.columns]
openp = open_px.loc[oos, weights.columns].ffill()
weights = weights.loc[oos]
pf, net_val, gross_exp = engine.run_backtest(close, openp, weights)
net_ret = net_val.pct_change().fillna(0.0)

# execution lag (T+1) already handled by from_orders on open; shift for safety
stats = pf.stats()
bench = spy.reindex(close.index).pct_change().fillna(0.0)   # SPY: used for BETA only
rf = sofr_daily_returns(close.index)                        # risk-free benchmark
bsum = benchmark_summary(net_ret.reindex(close.index), rf)
bench_cum = float(bsum.get("Benchmark Return [%]", 0.0)) / 100.0

# ---------------------------------------------------------------- metrics
trade_recs = pf.trades.records_readable
n_trades = int(pf.trades.count())
win = float(pf.trades.winning.count() / n_trades * 100) if n_trades else 0.0
pf_ratio = float(pf.trades.winning.pnl.sum() / abs(pf.trades.losing.pnl.sum())) \
    if n_trades and pf.trades.losing.count() else 0.0
expectancy = float(pf.trades.pnl.mean()) if n_trades else 0.0
sortino = 0.0
if net_ret.std() > 0:
    dn = net_ret[net_ret < 0]
    if len(dn) > 1:
        sortino = float(net_ret.mean() * 252 / (np.sqrt((dn ** 2).mean()) * np.sqrt(252)))

# neutrality
al = pd.concat([net_ret, bench], axis=1).dropna()
al.columns = ["s", "m"]
capm = sm.OLS(al.s, sm.add_constant(al.m)).fit()
beta_m = float(capm.params.get("m", 0.0))
p_beta = float(capm.pvalues.get("m", 1.0))
r2 = float(capm.rsquared)
alpha_ann = float(capm.params.get("const", 0.0) * 252)

port_stats = {
    "Start": str(close.index[0]), "End": str(close.index[-1]),
    "Period": len(close), "Start Value": cfg["initial_capital"],
    "End Value": float(net_val.iloc[-1]),
    "Total Return [%]": float((net_val.iloc[-1] / cfg["initial_capital"] - 1) * 100),
    "Benchmark Return [%]": bench_cum * 100,
    "Max Gross Exposure [%]": float(gross_exp.max() * 100),
    "Total Fees Paid": float(pf.orders.fees.sum()),
    "Max Drawdown [%]": float(pf.max_drawdown() * -100 if pf.max_drawdown() < 0 else pf.max_drawdown()),
    "Total Trades": n_trades,
    "Win Rate [%]": win,
    "Profit Factor": pf_ratio,
    "Expectancy": expectancy,
    "Sortino Ratio": sortino,
    "Sharpe Ratio": float(net_ret.mean() / net_ret.std() * np.sqrt(252)) if net_ret.std() else 0.0,
    "Market Beta": beta_m, "Beta p-value": p_beta, "R2": r2,
    "Annualized Alpha [%]": alpha_ann * 100,
    "Excess Return vs SOFR [%]": bsum.get("Excess Return [%]", 0.0),
    "Annualised Excess [%]": bsum.get("Annualised Excess [%]", 0.0),
    "Information Ratio": bsum.get("Information Ratio", 0.0),
}

# ---------------------------------------------------------------- export
pd.DataFrame(port_stats.items(), columns=["", "group"]).to_csv(f"{out}/csv/portfolio_stats.csv", index=False)
pd.DataFrame({"Live_Realized_Return": net_ret}).to_csv(f"{out}/csv/metrics.csv")
trade_recs.to_csv(f"{out}/csv/trade_records.csv", index=False)

print("\n" + "=" * 80)
print("PORTFOLIO STATS")
for k, v in port_stats.items():
    print(f"  {k:<24} {v}")
print("=" * 80)

# ================================================================ NEUTRALITY
print("\n" + "=" * 80)
print("QUANTITATIVE MARKET NEUTRALITY & FACTOR DIAGNOSTIC AUDIT")
print("=" * 80)
al2 = pd.concat([net_ret, bench], axis=1).dropna(); al2.columns = ["s", "m"]
cm = sm.OLS(al2.s, sm.add_constant(al2.m)).fit()
t_beta = float(cm.tvalues.get("m", 0.0))
bull = al2[al2.m > 0]; bear = al2[al2.m < 0]
bb = float(sm.OLS(bull.s, sm.add_constant(bull.m)).fit().params.get("m", 0)) if len(bull) > 10 else 0.0
brb = float(sm.OLS(bear.s, sm.add_constant(bear.m)).fit().params.get("m", 0)) if len(bear) > 10 else 0.0
CAP = 0.05
rows = [
    ("Annualized Alpha", f"{alpha_ann*100:.2f}%", "N/A", "INFO"),
    ("Market Beta (beta_m)", f"{beta_m:.4f}", f"< |{CAP}|", "PASS" if abs(beta_m) <= CAP else "FAIL"),
    ("Beta p-value", f"{p_beta:.4f}", ">= 0.05", "PASS" if p_beta >= 0.05 else "FAIL"),
    ("Beta t-statistic", f"{t_beta:.4f}", "< |1.96|", "PASS" if abs(t_beta) < 1.96 else "FAIL"),
    ("Variance Explained (R2)", f"{r2:.2%}", "< 0.5%", "PASS" if r2 <= 0.005 else "FAIL"),
    ("Bull Market Beta", f"{bb:.4f}", f"< |{CAP}|", "PASS" if abs(bb) <= CAP else "FAIL"),
    ("Bear Market Beta", f"{brb:.4f}", f"< |{CAP}|", "PASS" if abs(brb) <= CAP else "FAIL"),
]
print(f"{'Metric':>28} {'Value':>10} {'Threshold':>12} {'Status':>7}")
for n, v, t, st in rows:
    print(f"{n:>28} {v:>10} {t:>12} {st:>7}")
fails = [r[0] for r in rows if r[3] == "FAIL"]
print("-" * 80)
print(f"  VERDICT: Market Neutral = {'TRUE' if not fails else 'FALSE'} | Failures: "
      f"{', '.join(fails) if fails else 'None'}")
print("=" * 80)

print_benchmark_block(bsum)

# ---- progressive (per-rebalance) diagnostics + early-stop rule ----
from modules.risk.progressive import progressive_metrics, print_progress_table
prog = progressive_metrics(net_ret.reindex(close.index), pd.DatetimeIndex(rebal_dates), rf)
print_progress_table(prog, checkpoint_every=5,
                     title="PROGRESSIVE (PER-REBALANCE) DIAGNOSTICS — CS STRATEGY")
if not prog.empty:
    prog.to_csv(f"{out}/csv/progressive_metrics.csv")

# ================================================================ TEARSHEET
print("\n" + "=" * 80)
print("POSITION TEARSHEET — TOP / BOTTOM CONTRIBUTORS (attributed by ticker)")
print("=" * 80)
if "Column" in trade_recs.columns and len(trade_recs):
    contrib = trade_recs.groupby("Column")["PnL"].agg(["sum", "count"]).sort_values("sum")
    bottom = contrib.head(8); top = contrib.tail(8).iloc[::-1]
    print(f"{'Ticker':>8} {'Net PnL ($)':>14} {'Trades':>8}")
    print("  --- worst ---")
    for tk, rr in bottom.iterrows():
        print(f"{tk:>8} {rr['sum']:>14.2f} {int(rr['count']):>8}")
    print("  --- best ---")
    for tk, rr in top.iterrows():
        print(f"{tk:>8} {rr['sum']:>14.2f} {int(rr['count']):>8}")
else:
    print("  (no trade records)")
print("=" * 80)

# ================================================================ MOO BLOTTER
print("\n" + "=" * 80)
print("ACTIONABLE MARKET-ON-OPEN (MOO) ORDER BLOTTER FOR NEXT SESSION")
print("=" * 80)
fw = weights.iloc[-1]
last_px = px.iloc[-1]
nav = float(net_val.iloc[-1])
blot = []
for tk in [c for c in fw.index if abs(fw.get(c, 0.0)) > 1e-6]:
    w = float(fw[tk]); price = float(last_px.get(tk, np.nan))
    if not np.isfinite(price) or price <= 0:
        continue
    tgt = int(w * nav / price); dlt = tgt
    if abs(dlt * price) < 500:
        continue
    blot.append({"Ticker": tk, "Action": "BUY" if dlt > 0 else "SELL", "Order_Type": "MOO",
                 "Delta_Shares": abs(dlt), "Target_Shares": tgt,
                 "Est_Order_USD": round(abs(dlt * price), 2),
                 "Target_Weight_%": round(w * 100, 2), "Price_Ref": round(price, 2)})
bl = pd.DataFrame(blot).sort_values("Est_Order_USD", ascending=False) if blot else pd.DataFrame()
print(bl.head(15).to_string(index=False) if len(bl) else "  (flat)")
print(f"  ... {len(bl)} orders total")
print("=" * 80)

# ================================================================ WF STATS
print("\n" + "=" * 80)
print("WALK-FORWARD NET PORTFOLIO STATS (POST-FEES & EXECUTION LAG)")
print("=" * 80)
for k, v in port_stats.items():
    print(f"  {k:<24} {v}")
print("=" * 80)

# equity + drawdown plot
fig, ax = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
ax[0].plot(net_val.index, net_val.values, color="#2a9d8f", lw=1.6, label="CS Reversal")
ax[0].axhline(cfg["initial_capital"], color="k", lw=0.6)
ax[0].set_title("Cross-Sectional Residual Reversal — Equity Curve")
ax[0].legend(); ax[0].grid(alpha=0.3)
dd = 1 - net_val / net_val.cummax()
ax[1].fill_between(dd.index, -dd.values * 100, 0, color="#e76f51", alpha=0.6)
ax[1].set_title("Drawdown (%)"); ax[1].grid(alpha=0.3)
plt.tight_layout(); plt.savefig(f"{out}/imgs/cs_tearsheet.png", dpi=130); plt.close()
print(f"\n[CS] results -> {out}/")
capture.close_log()
sys.stdout = capture._stdout
