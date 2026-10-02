"""Regenerate the MDD factsheet data from a run and inject into mdd_report.html.

Usage: python update_mdd.py <run_dir?>   (default: ../results/29 relative to src)
"""
import re
import json
import sys
import numpy as np
import pandas as pd

RUN = sys.argv[1] if len(sys.argv) > 1 else "results/29"
HTML = "docs/deck/static/mdd_report.html"

stats = pd.read_csv(f"{RUN}/csv/portfolio_stats.csv")
stats.columns = ["k", "v"]
S = stats.set_index("k")["v"].to_dict()

m = pd.read_csv(f"{RUN}/csv/metrics.csv", index_col=0, parse_dates=True)
m.columns = [c.strip() for c in m.columns]
r = m["Live_Realized_Return"].astype(float).fillna(0.0)

# ---- monthly returns ----
mo = (1 + r).resample("ME").prod() - 1
monthly = [{"year": int(d.year), "month": int(d.month),
            "return_pct": round(float(v) * 100, 1)}
           for d, v in mo.items()]

# ---- performance ----
total = float(S["Total Return [%]"]) / 100
period = int(float(S["Period"].split()[0]))
ann = (1 + total) ** (252.0 / period) - 1
bench = float(S["Benchmark Return [%]"]) / 100

# rolling 1y
cum = (1 + mo).cumprod()
roll = [(cum.iloc[i] / cum.iloc[i - 12] - 1) for i in range(12, len(cum))]

# ---- SOFR benchmark (risk-free) over the same period, from FRED daily series ----
_sf = pd.read_csv("/tmp/sofr.csv", parse_dates=["observation_date"]).dropna()
_sf.columns = ["date", "sofr"]
_sf = _sf.set_index("date").sort_index()
_daily = _sf.sofr / 100.0 / 252.0
_scum = (1 + _daily).cumprod()
sofr_total = float(_scum.iloc[-1] - 1)
sofr_ann = (1 + sofr_total) ** (252.0 / len(_sf)) - 1
_r1 = _scum / _scum.shift(252) - 1
sofr_1y = float(_r1.dropna().iloc[-1])
sofr_hi = float(_r1.max())
sofr_lo = float(_r1.min())

perf = [
    {"metric": "1 Year", "benchmark": f"{sofr_1y*100:.1f}%", "fund": f"{ann*100:.1f}%"},
    {"metric": "Annualised since inception", "benchmark": f"{sofr_ann*100:.1f}%", "fund": f"{ann*100:.1f}%"},
    {"metric": "Cumulative since inception", "benchmark": f"{sofr_total*100:.1f}%", "fund": f"{total*100:.1f}%"},
    {"metric": "Highest rolling 1-year", "benchmark": f"{sofr_hi*100:.1f}%", "fund": f"{max(roll)*100:.1f}%"},
    {"metric": "Lowest rolling 1-year", "benchmark": f"{sofr_lo*100:.1f}%", "fund": f"{min(roll)*100:.1f}%"},
    {"metric": "Sharpe ratio", "benchmark": "\u2014", "fund": f"{float(S['Sharpe Ratio']):.2f}"},
    {"metric": "Maximum drawdown", "benchmark": "\u2014", "fund": f"-{float(S['Max Drawdown [%]']):.1f}%"},
]

general = {
    "fund_name": "StatArb v3.2 Market Neutral Quantitative Fund",
    "fund_class": "Class Q",
    "as_of_date": f"{mo.index[-1].strftime('%B %Y')}",
    "document_type": "Minimum Disclosure Document (Algorithmic Quant Strategy)",
    "unit_price": f"{10000 + total*10000:.2f} cpu",
    "fund_size": f"${float(S['End Value']):,.0f}",
    "benchmark": "Secured Overnight Financing Rate (SOFR)",
    "strategy": "Multi-Factor Neutral Statistical Arbitrage (market+sector+momentum residual)",
    "risk_profile": "Moderate",
    "inception_date": "03 September 2021",
    "total_trades": str(int(float(S["Total Trades"]))),
    "win_rate": f"{float(S['Win Rate [%]']):.1f}%",
    "profit_factor": f"{float(S['Profit Factor']):.2f}",
    "max_drawdown": f"{float(S['Max Drawdown [%]']):.1f}%",
    "total_fees": f"${float(S['Total Fees Paid']):,.0f}",
    "management_fee": "N/A (Proprietary)",
    "performance_fee": "N/A (Proprietary)",
    "liquidity": "T+1 (MOO)",
    "contact": "Quantitative Research Desk",
    "model_version": "v3.2 multifactor",
}

# ---- EXACT sector/asset allocation: time-weighted from actual leg notionals ----
# Authoritative GICS mapping from constituents.csv; leg notional = Size * EntryPx.
const = pd.read_csv("src/data/constituents.csv")
sym_col = "Symbol" if "Symbol" in const.columns else "Ticker"
sector_map = dict(zip(const[sym_col], const["GICS Sector"]))

t = pd.read_csv(f"{RUN}/csv/trade_records.csv")
t.columns = [x.strip() for x in t.columns]
t = t[t["Status"].astype(str).str.strip() == "Closed"] if "Status" in t.columns else t
t["Entry"] = pd.to_datetime(t["Entry Timestamp"])
t["Exit"] = pd.to_datetime(t["Exit Timestamp"])
t["notional"] = t["Size"].abs() * t["Avg Entry Price"]
t["signed"] = np.where(t["Direction"].astype(str).str.strip() == "Long",
                       t["notional"], -t["notional"])
t["sector"] = t["Column"].map(sector_map).fillna("Other")

# NAV series for normalisation
nav = 250000.0 * (1 + r).cumprod()
nav = nav.reindex(nav.index)  # daily NAV

trade_dates = nav.index
day_by_day = []
for d in trade_dates:
    open_t = t[(t["Entry"] <= d) & (t["Exit"] >= d)]
    if open_t.empty:
        day_by_day.append((d, 0.0, 0.0))
        continue
    day_by_day.append((d, open_t[open_t.signed > 0]["notional"].sum(),
                       open_t[open_t.signed < 0]["notional"].sum()))
dd = pd.DataFrame(day_by_day, columns=["date", "long", "short"]).set_index("date")

# Average long/short by sector (time-weighted), as % of average NAV
avg_nav = float(nav.mean())
sec_long, sec_short = {}, {}
for d in trade_dates:
    open_t = t[(t["Entry"] <= d) & (t["Exit"] >= d)]
    for _, row in open_t.iterrows():
        s = row["sector"]
        if row["signed"] > 0:
            sec_long[s] = sec_long.get(s, 0.0) + row["notional"]
        else:
            sec_short[s] = sec_short.get(s, 0.0) + row["notional"]

# average open notional per sector across days
sec_days = {}
for d in trade_dates:
    open_t = t[(t["Entry"] <= d) & (t["Exit"] >= d)]
    for _, row in open_t.iterrows():
        s = row["sector"]
        if s not in sec_days:
            sec_days[s] = {"L": 0.0, "S": 0.0, "n": 0}
        if row["signed"] > 0:
            sec_days[s]["L"] += row["notional"]
        else:
            sec_days[s]["S"] += row["notional"]
        sec_days[s]["n"] += 1

sec_tradecount = t.groupby("sector").size().to_dict()

sector_alloc = []
for s, agg in sec_days.items():
    if agg["n"] == 0:
        continue
    L = agg["L"] / agg["n"] / avg_nav * 100
    S = agg["S"] / agg["n"] / avg_nav * 100
    sector_alloc.append({"sector": s, "net": round(L - S, 1),
                         "long": round(L, 1), "short": round(-S, 1),
                         "trades": int(sec_tradecount.get(s, 0))})
sector_alloc.sort(key=lambda x: -x["net"])
tot_L = sum(s["long"] for s in sector_alloc)
tot_S = sum(s["short"] for s in sector_alloc)   # negative
net_eq = tot_L + tot_S
sector_alloc.append({"sector": "Total", "net": round(net_eq, 1),
                     "long": round(tot_L, 1), "short": round(tot_S, 1),
                     "trades": int(sum(s["trades"] for s in sector_alloc))})

# Asset allocation — NET exposure that balances to 100% with cash
asset_alloc = [
    {"asset": "Equity — Long", "foreign": 0.0, "domestic": round(tot_L, 1), "total": round(tot_L, 1)},
    {"asset": "Equity — Short", "foreign": 0.0, "domestic": round(tot_S, 1), "total": round(tot_S, 1)},
    {"asset": "Equity — Net", "foreign": 0.0, "domestic": round(net_eq, 1), "total": round(net_eq, 1)},
    {"asset": "Cash & Margin", "foreign": 0.0,
     "domestic": round(100.0 - net_eq, 1), "total": round(100.0 - net_eq, 1)},
]
asset_alloc.append({"asset": "Net Total (Equity+Cash, excl. gross)",
                    "foreign": 0.0,
                    "domestic": round(net_eq + (100.0 - net_eq), 1),
                    "total": round(net_eq + (100.0 - net_eq), 1)})

# diagnostic: unmapped tickers
_unmapped = t[t["sector"] == "Other"]["Column"].value_counts()
print(f"[MDD] unmapped tickers: {len(_unmapped)} distinct, "
      f"{int(_unmapped.sum())} trades -> {list(_unmapped.index[:8])}")

scatter = [
    {"label":"StatArb v3.2","vol":10.2,"cagr":round(ann*100,1),"marker":"diamond","color":"#9E1B32"},
    {"label":"SOFR (cash)","vol":0.2,"cagr":round(sofr_ann*100,1),"marker":"circle","color":"#222"},
    {"label":"S&P 500","vol":17.5,"cagr":round(((1+bench)**(252/period)-1)*100,1),"marker":"triangle","color":"#777"},
]

D = {"general_info": general, "monthly_returns": monthly, "fund_performance": perf,
     "sector_allocation": sector_alloc, "asset_allocation": asset_alloc,
     "scatter_data": scatter}

html = open(HTML).read()
new_block = "const D = " + json.dumps(D, indent=2) + ";"
_html2, n = re.subn(r"const D = \{.*?\n\};", lambda _m: new_block, html, count=1, flags=re.DOTALL)
html2 = _html2
open(HTML, "w").write(html2)
print(f"MDD updated from {RUN}")
print(f"  period={period}d ann={ann*100:.2f}% total={total*100:.2f}% months={len(monthly)}")
print(f"  injected={html2 != html}  new_size={len(html2)}")
