"""
Regenerate the MDD factsheet data from a src_tape run and inject it into
docs/deck/static/mdd_report.html.

Independent of src/: reads the run's own CSVs (our trade schema), the run's
config for its data files, and the real SOFR series. Allocations are computed
vectorised (no per-day Python loop) from actual leg notionals.

Usage:
    python scripts/update_mdd.py src_tape/results/1
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_TAPE = os.path.dirname(HERE)
REPO = os.path.dirname(SRC_TAPE)
DATA = os.path.join(SRC_TAPE, "data")
HTML = os.path.join(REPO, "docs", "deck", "static", "mdd_report.html")

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def _stats(run: str) -> dict:
    s = pd.read_csv(os.path.join(run, "csv", "portfolio_stats.csv"))
    s.columns = ["k", "v"]
    return s.set_index("k")["v"].to_dict()


def _daily_returns(run: str) -> pd.Series:
    m = pd.read_csv(os.path.join(run, "csv", "metrics.csv"), index_col=0, parse_dates=True)
    m.columns = [c.strip() for c in m.columns]
    return m["Live_Realized_Return"].astype(float).fillna(0.0), m.index


def _sofr_returns(idx: pd.DatetimeIndex) -> pd.Series:
    s = pd.read_csv(os.path.join(DATA, "sofr.csv"))
    s.columns = ["date", "sofr"]
    s["date"] = pd.to_datetime(s["date"])
    s = s.set_index("date")["sofr"].astype(float).sort_index()
    daily = (s / 100.0 / 252.0).reindex(idx).ffill().bfill().fillna(0.0)
    daily.index = idx
    return daily


def _spy_returns(run: str, idx: pd.DatetimeIndex) -> pd.Series:
    cfg = json.load(open(os.path.join(run, "config.json")))
    frames = []
    for key in ("price_csv", "oos_price_csv"):
        p = cfg.get(key)
        if not p:
            continue
        f = os.path.join(DATA, os.path.basename(p))
        if os.path.exists(f):
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if "SPY" in df.columns:
                frames.append(df[["SPY"]])
    if not frames:
        return pd.Series(0.0, index=idx)
    spy = pd.concat(frames)[~pd.concat(frames).index.duplicated()].sort_index()
    px = spy["SPY"].astype(float).reindex(idx).ffill().bfill()
    return px.pct_change().fillna(0.0)


def _allocations(run: str, idx: pd.DatetimeIndex, nav0: float) -> dict:
    """Vectorised time-weighted sector/asset allocation from leg notionals."""
    const = pd.read_csv(os.path.join(DATA, "constituents.csv"))
    sym = "Symbol" if "Symbol" in const.columns else "Ticker"
    sec_map = dict(zip(const[sym], const["GICS Sector"]))

    t = pd.read_csv(os.path.join(run, "csv", "trade_records.csv"))
    t.columns = [c.strip() for c in t.columns]
    t = t[t["Status"].astype(str).str.strip() == "Closed"].copy()
    t["Entry"] = pd.to_datetime(t["Entry Timestamp"])
    t["Exit"] = pd.to_datetime(t["Exit Timestamp"])
    t["notional"] = t["Entry Notional"].abs()
    t["signed"] = np.where(t["Direction"].astype(str).str.strip() == "Long",
                           t["notional"], -t["notional"])
    t["sector"] = t["Asset"].map(sec_map).fillna("Other")

    r, _ = _daily_returns(run)
    nav = nav0 * (1.0 + r).cumprod()
    avg_nav = float(nav.mean()) or nav0

    days = idx.values.astype("datetime64[ns]")
    E = t["Entry"].values.astype("datetime64[ns]")[None, :]
    X = t["Exit"].values.astype("datetime64[ns]")[None, :]
    open_mask = (E <= days[:, None]) & (X >= days[:, None])          # (D, T)
    notion = t["notional"].values[None, :]
    is_long = (t["signed"].values > 0)[None, :]
    long_day = (open_mask & is_long) * notion
    short_day = (open_mask & ~is_long) * notion

    tot_L = float(long_day.sum(axis=1).mean()) / avg_nav * 100.0
    tot_S = float(short_day.sum(axis=1).mean()) / avg_nav * 100.0

    sec_trades = t.groupby("sector").size().to_dict()
    sec_rows = []
    for s in sorted(t["sector"].unique()):
        m = (t["sector"].values == s)[None, :]
        L = float((long_day * m).sum(axis=1).mean()) / avg_nav * 100.0
        S = float((short_day * m).sum(axis=1).mean()) / avg_nav * 100.0
        if L == 0 and S == 0:
            continue
        sec_rows.append({"sector": s, "net": round(L - S, 1), "long": round(L, 1),
                         "short": round(-S, 1), "trades": int(sec_trades.get(s, 0))})
    sec_rows.sort(key=lambda x: -x["net"])
    net_eq = tot_L - tot_S
    sec_rows.append({"sector": "Total", "net": round(net_eq, 1),
                     "long": round(tot_L, 1), "short": round(-tot_S, 1),
                     "trades": int(sum(x["trades"] for x in sec_rows))})

    asset_rows = [
        {"asset": "Equity — Long", "foreign": 0.0, "domestic": round(tot_L, 1), "total": round(tot_L, 1)},
        {"asset": "Equity — Short", "foreign": 0.0, "domestic": round(-tot_S, 1), "total": round(-tot_S, 1)},
        {"asset": "Equity — Net", "foreign": 0.0, "domestic": round(net_eq, 1), "total": round(net_eq, 1)},
        {"asset": "Cash & Margin", "foreign": 0.0, "domestic": round(100.0 - net_eq, 1),
         "total": round(100.0 - net_eq, 1)},
        {"asset": "Net Total (Equity+Cash, excl. gross)", "foreign": 0.0,
         "domestic": 100.0, "total": 100.0},
    ]
    return {"sector": sec_rows, "asset": asset_rows,
            "exposure": {"long": round(tot_L, 1), "short": round(tot_S, 1)}}


def build(run: str) -> dict:
    S = _stats(run)
    r, idx = _daily_returns(run)
    nav0 = float(S["Start Value"])
    total = float(S["Total Return [%]"]) / 100.0
    n_days = len(r)                       # trading days, not the calendar span
    ann = (1.0 + total) ** (252.0 / n_days) - 1.0

    mo = (1.0 + r).resample("ME").prod() - 1.0
    monthly = [{"year": int(d.year), "month": int(d.month),
                "return_pct": round(float(v) * 100.0, 1)} for d, v in mo.items()]

    # SOFR over the SAME window
    sofr = _sofr_returns(idx)
    sofr_mo = (1.0 + sofr).resample("ME").prod() - 1.0
    sofr_total = float((1.0 + sofr).prod() - 1.0)
    sofr_ann = (1.0 + sofr_total) ** (252.0 / n_days) - 1.0

    def roll12(series_mo):
        cum = (1.0 + series_mo).cumprod()
        if len(cum) > 12:
            return [(cum.iloc[i] / cum.iloc[i - 12] - 1.0) for i in range(12, len(cum))]
        return [float(cum.iloc[-1] - 1.0)]

    fund_roll = roll12(mo)
    sofr_roll = roll12(sofr_mo)
    fund_1y = fund_roll[-1]
    sofr_1y = sofr_roll[-1]

    perf = [
        {"metric": "1 Year", "benchmark": f"{sofr_1y*100:.1f}%", "fund": f"{fund_1y*100:.1f}%"},
        {"metric": "Annualised since inception", "benchmark": f"{sofr_ann*100:.1f}%", "fund": f"{ann*100:.1f}%"},
        {"metric": "Cumulative since inception", "benchmark": f"{sofr_total*100:.1f}%", "fund": f"{total*100:.1f}%"},
        {"metric": "Highest rolling 1-year", "benchmark": f"{max(sofr_roll)*100:.1f}%", "fund": f"{max(fund_roll)*100:.1f}%"},
        {"metric": "Lowest rolling 1-year", "benchmark": f"{min(sofr_roll)*100:.1f}%", "fund": f"{min(fund_roll)*100:.1f}%"},
        {"metric": "Sharpe ratio", "benchmark": "\u2014", "fund": f"{float(S['Sharpe Ratio']):.2f}"},
        {"metric": "Maximum drawdown", "benchmark": "\u2014", "fund": f"-{float(S['Max Drawdown [%]']):.1f}%"},
    ]

    start = pd.Timestamp(S["Start"])
    general = {
        "fund_name": "StatArb v3.2 Market Neutral Quantitative Fund",
        "fund_class": "Class Q",
        "as_of_date": f"{mo.index[-1].strftime('%B %Y')}",
        "document_type": "Minimum Disclosure Document (Algorithmic Quant Strategy)",
        "unit_price": f"{10000.0 + total * 10000.0:.2f} cpu",
        "fund_size": f"${float(S['End Value']):,.0f}",
        "benchmark": "Secured Overnight Financing Rate (SOFR)",
        "strategy": "Multi-Factor Neutral Statistical Arbitrage (market+sector+momentum residual)",
        "risk_profile": "Moderate",
        "inception_date": start.strftime("%d %B %Y"),
        "total_trades": str(int(float(S["Total Trades"]))),
        "win_rate": f"{float(S['Win Rate [%]']):.1f}%",
        "profit_factor": f"{float(S['Profit Factor']):.2f}",
        "max_drawdown": f"{float(S['Max Drawdown [%]']):.1f}%",
        "total_fees": f"${float(S['Total Fees Paid']):,.0f}",
        "management_fee": "N/A (Proprietary)",
        "performance_fee": "N/A (Proprietary)",
        "liquidity": "T+1 (MOO)",
        "contact": "Quantitative Research Desk",
        "model_version": "v3.2 multifactor (src_tape)",
    }

    alloc = _allocations(run, idx, nav0)

    fund_vol = float(r.std() * np.sqrt(252.0) * 100.0)
    spy = _spy_returns(run, idx)
    spy_total = float((1.0 + spy).prod() - 1.0)
    spy_ann = (1.0 + spy_total) ** (252.0 / n_days) - 1.0
    spy_vol = float(spy.std() * np.sqrt(252.0) * 100.0)
    scatter = [
        {"label": "StatArb v3.2", "vol": round(fund_vol, 1), "cagr": round(ann * 100.0, 1),
         "marker": "diamond", "color": "#9E1B32"},
        {"label": "SOFR (cash)", "vol": round(float(sofr.std() * np.sqrt(252.0) * 100.0), 1),
         "cagr": round(sofr_ann * 100.0, 1), "marker": "circle", "color": "#222"},
        {"label": "S&P 500", "vol": round(spy_vol, 1), "cagr": round(spy_ann * 100.0, 1),
         "marker": "triangle", "color": "#777"},
    ]

    return {
        "general_info": general,
        "monthly_returns": monthly,
        "fund_performance": perf,
        "sector_allocation": alloc["sector"],
        "asset_allocation": alloc["asset"],
        "exposure": alloc["exposure"],
        "scatter_data": scatter,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Update MDD factsheet from a run")
    ap.add_argument("run", nargs="?", default=os.path.join(SRC_TAPE, "results", "1"))
    args = ap.parse_args()
    run = args.run if os.path.isabs(args.run) else os.path.join(REPO, args.run)

    D = build(run)
    html = open(HTML).read()
    block = "const D = " + json.dumps(D, indent=2) + ";"
    new_html, n = re.subn(r"const D = \{.*?\n\};", lambda _m: block, html,
                          count=1, flags=re.DOTALL)
    if n != 1:
        raise SystemExit(f"MDD injection failed: {n} substitutions")
    open(HTML, "w").write(new_html)

    print(f"[MDD] updated from {os.path.relpath(run, REPO)}")
    print(f"  period={D['general_info']['as_of_date']}  "
          f"trades={D['general_info']['total_trades']}  "
          f"win={D['general_info']['win_rate']}  "
          f"pf={D['general_info']['profit_factor']}  "
          f"mdd={D['general_info']['max_drawdown']}")
    print(f"  exposure long={D['exposure']['long']}% short={D['exposure']['short']}%")
    print(f"  scatter={[(p['label'], p['vol'], p['cagr']) for p in D['scatter_data']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
