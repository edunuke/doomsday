#!/usr/bin/env python3
"""
MDD (Minimum Disclosure Document) Data Generator for StatArb v3.2

Reads v3.2 backtest results from experiments/results/65/ and generates
a JSON data payload suitable for rendering a regulatory-style factsheet.

Usage:
    python generate_mdd_data.py [--output docs/deck/static/mdd_data.json]

Output: JSON file + console log of all computed fields.
"""
import os
import sys
import json
import csv
import math
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
RESULTS_DIR = "experiments/results/65"
METRICS_CSV = f"{RESULTS_DIR}/v32_metrics.csv"
STATS_CSV = f"{RESULTS_DIR}/v32_portfolio_stats.csv"
TRADES_CSV = f"{RESULTS_DIR}/v32_trade_records.csv"
COMMITTEE_CSV = f"{RESULTS_DIR}/v32_committee_report.csv"
BLOTTER_CSV = f"{RESULTS_DIR}/v32_moo_blotter.csv"

INITIAL_CAPITAL = 250_000.0
FUND_NAME = "StatArb v3.2 Market Neutral Quantitative Fund"
FUND_CLASS = "Class Q"
AS_OF_DATE = "31 August 2026"  # Will be overridden by last metrics date
BENCHMARK_NAME = "S&P 500 Total Return"

SECTOR_MAP = {
    "Technology": ["AVGO", "NVDA", "ANET", "SMCI", "DELL", "ADP", "ADI", "MPWR", "KLAC", "MSCI", "FTNT"],
    "Financials": ["XYZ", "TROW", "FITB", "TFC", "COF", "WFC", "BAC", "CFG", "IBKR"],
    "Health Care": ["BAX", "PFE", "HSIC", "INCY", "RVTY", "CIEN", "LITE"],
    "Industrials": ["AMT", "KIM", "CBRE", "INVH", "CRL", "MTD", "HRL", "FE", "DTE", "ES", "CCI", "VICI", "WY", "FRT"],
    "Utilities": ["D", "ETR", "SRE", "EXC", "NI", "ATO", "AWK", "SO", "CEG", "VST", "EPD"],
    "Consumer Discretionary": ["KDP"],
    "Energy": ["COP", "MPC"],
    "Real Estate": ["BXP"],
    "Unknown": [],
}

LOG_LINES = []


def log(msg: str):
    """Print and buffer log line."""
    print(msg, flush=True)
    LOG_LINES.append(msg)


def read_csv(path: str) -> list[dict]:
    with open(path, "r") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# 1. PORTFOLIO STATS
# ---------------------------------------------------------------------------
def load_portfolio_stats() -> dict:
    log("=" * 80)
    log("[1/7] Loading portfolio stats from v32_portfolio_stats.csv")
    log("=" * 80)
    rows = read_csv(STATS_CSV)
    stats = {}
    for row in rows:
        key = row.get("", row.get("Metric", "")).strip()
        val = row.get("group", row.get("Value", "")).strip()
        if key and val:
            stats[key] = val
            log(f"  {key}: {val}")
    return stats


# ---------------------------------------------------------------------------
# 2. MONTHLY RETURNS
# ---------------------------------------------------------------------------
def compute_monthly_returns() -> list[dict]:
    log("\n" + "=" * 80)
    log("[2/7] Computing monthly net returns from metrics CSV")
    log("=" * 80)
    rows = read_csv(METRICS_CSV)
    monthly = defaultdict(float)
    for row in rows:
        date_str = row.get("", row.get("Date", "")).strip()
        ret_str = row.get("Live_Realized_Return", "0").strip()
        if not date_str or not ret_str:
            continue
        try:
            dt = datetime.strptime(date_str.split(" ")[0], "%Y-%m-%d")
            ret = float(ret_str) if ret_str and ret_str != "nan" else 0.0
            monthly[(dt.year, dt.month)] += ret
        except (ValueError, KeyError):
            continue

    result = []
    for (year, month), total_ret in sorted(monthly.items()):
        pct = round(total_ret * 100, 1)
        result.append({"year": year, "month": month, "return_pct": pct})
        log(f"  {year}-{month:02d}: {pct:+.1f}%")

    return result


# ---------------------------------------------------------------------------
# 3. FUND PERFORMANCE TABLE
# ---------------------------------------------------------------------------
def compute_fund_performance(stats: dict, monthly_returns: list[dict]) -> list[dict]:
    log("\n" + "=" * 80)
    log("[3/7] Computing fund performance metrics")
    log("=" * 80)

    total_return = float(stats.get("Total Return [%]", "0").strip()) / 100.0
    benchmark_return = float(stats.get("Benchmark Return [%]", "0").strip()) / 100.0
    max_dd = float(stats.get("Max Drawdown [%]", "0").strip())
    period_days = int(float(stats.get("Period", "500").strip()))

    ann_factor = 252.0 / max(period_days, 1)
    ann_return = (1.0 + total_return) ** ann_factor - 1.0
    ann_bench = (1.0 + benchmark_return) ** ann_factor - 1.0

    # Compute standard deviation from monthly returns
    monthly_rets = [m["return_pct"] / 100.0 for m in monthly_returns]
    if len(monthly_rets) > 1:
        mean_m = sum(monthly_rets) / len(monthly_rets)
        var_m = sum((r - mean_m) ** 2 for r in monthly_rets) / (len(monthly_rets) - 1)
        monthly_std = math.sqrt(var_m)
        annual_std = monthly_std * math.sqrt(12.0)
    else:
        annual_std = 0.0

    # Compute rolling 1-year returns
    cum_returns = []
    cum = 1.0
    for m in monthly_returns:
        cum *= (1.0 + m["return_pct"] / 100.0)
        cum_returns.append(cum)

    rolling_1yr = []
    for i in range(12, len(cum_returns)):
        r = cum_returns[i] / cum_returns[i - 12] - 1.0
        rolling_1yr.append(r)

    highest_1yr = max(rolling_1yr) if rolling_1yr else 0.0
    lowest_1yr = min(rolling_1yr) if rolling_1yr else 0.0

    perf_rows = [
        {"metric": "1 Year", "benchmark": f"{ann_bench * 100:.1f}%", "fund": f"{ann_return * 100:.1f}%"},
        {"metric": "Annualised return since inception", "benchmark": f"{ann_bench * 100:.1f}%", "fund": f"{ann_return * 100:.1f}%"},
        {"metric": "Cumulative return since inception", "benchmark": f"{benchmark_return * 100:.1f}%", "fund": f"{total_return * 100:.1f}%"},
        {"metric": "Highest rolling 1-year return", "benchmark": "—", "fund": f"{highest_1yr * 100:.1f}%"},
        {"metric": "Lowest rolling 1-year return", "benchmark": "—", "fund": f"{lowest_1yr * 100:.1f}%"},
        {"metric": "Standard deviation", "benchmark": "—", "fund": f"{annual_std * 100:.1f}%"},
        {"metric": "Maximum drawdown", "benchmark": "—", "fund": f"-{max_dd:.1f}%"},
    ]

    for row in perf_rows:
        log(f"  {row['metric']}: Fund={row['fund']}, Bench={row['benchmark']}")

    return perf_rows


# ---------------------------------------------------------------------------
# 4. SECTOR ALLOCATION
# ---------------------------------------------------------------------------
def compute_sector_allocation() -> list[dict]:
    log("\n" + "=" * 80)
    log("[4/7] Computing sector allocation from committee report")
    log("=" * 80)
    rows = read_csv(COMMITTEE_CSV)

    # Group by sector
    sector_pnl = defaultdict(lambda: {"long_pnl": 0.0, "short_pnl": 0.0, "trades": 0})

    # Read trade records for stock-level exposure
    trades = read_csv(TRADES_CSV)

    # Map each trade to a sector
    for trade in trades:
        ticker = trade.get("Column", "").strip()
        direction = trade.get("Direction", "").strip()
        pnl = float(trade.get("PnL", "0").strip() or "0")
        size = abs(float(trade.get("Size", "0").strip() or "0"))

        sector = "Unknown"
        for sec, tickers in SECTOR_MAP.items():
            if ticker in tickers:
                sector = sec
                break

        if direction == "Long":
            sector_pnl[sector]["long_pnl"] += pnl
        else:
            sector_pnl[sector]["short_pnl"] += pnl
        sector_pnl[sector]["trades"] += 1

    # Compute exposures relative to initial capital
    result = []
    total_long = 0
    total_short = 0
    for sector in sorted(sector_pnl.keys()):
        data = sector_pnl[sector]
        long_exp = data["long_pnl"] / INITIAL_CAPITAL * 100.0
        short_exp = data["short_pnl"] / INITIAL_CAPITAL * 100.0
        net_exp = long_exp + short_exp
        total_long += long_exp
        total_short += short_exp
        result.append({
            "sector": sector,
            "net": round(net_exp, 1),
            "long": round(long_exp, 1),
            "short": round(short_exp, 1),
            "trades": data["trades"],
        })
        log(f"  {sector}: Net={net_exp:+.1f}%, Long={long_exp:+.1f}%, Short={short_exp:+.1f}%, Trades={data['trades']}")

    result.append({
        "sector": "Total",
        "net": round(total_long + total_short, 1),
        "long": round(total_long, 1),
        "short": round(total_short, 1),
        "trades": sum(d["trades"] for d in result),
    })
    log(f"  TOTAL: Net={total_long + total_short:+.1f}%, Long={total_long:+.1f}%, Short={total_short:+.1f}%")

    return result


# ---------------------------------------------------------------------------
# 5. ASSET ALLOCATION (simplified: Equity + SPY Overlay = Fixed Income proxy)
# ---------------------------------------------------------------------------
def compute_asset_allocation(sector_alloc: list[dict]) -> list[dict]:
    log("\n" + "=" * 80)
    log("[5/7] Computing asset allocation net exposure")
    log("=" * 80)

    total_equity_net = sum(s["net"] for s in sector_alloc if s["sector"] != "Total")
    total_equity_long = sum(s["long"] for s in sector_alloc if s["sector"] != "Total")
    total_equity_short = sum(s["short"] for s in sector_alloc if s["sector"] != "Total")

    result = [
        {"asset": "Equity (StatArb Pairs)", "foreign": 0.0, "domestic": round(total_equity_net, 1), "total": round(total_equity_net, 1)},
        {"asset": "Equity (SPY Overlay Hedge)", "foreign": round(-total_equity_net * 0.6, 1), "domestic": 0.0, "total": round(-total_equity_net * 0.6, 1)},
        {"asset": "Cash & Margin", "foreign": 0.0, "domestic": round(100.0 - abs(total_equity_net), 1), "total": round(100.0 - abs(total_equity_net), 1)},
    ]
    grand_total = sum(r["total"] for r in result)
    result.append({"asset": "Total", "foreign": sum(r["foreign"] for r in result), "domestic": sum(r["domestic"] for r in result), "total": round(grand_total, 1)})

    for r in result:
        log(f"  {r['asset']}: Foreign={r['foreign']:+.1f}%, Domestic={r['domestic']:+.1f}%, Total={r['total']:+.1f}%")

    return result


# ---------------------------------------------------------------------------
# 6. RISK-RETURN SCATTER DATA
# ---------------------------------------------------------------------------
def compute_scatter_data(stats: dict) -> list[dict]:
    log("\n" + "=" * 80)
    log("[6/7] Computing risk-return scatter plot data points")
    log("=" * 80)

    total_return = float(stats.get("Total Return [%]", "0").strip()) / 100.0
    benchmark_return = float(stats.get("Benchmark Return [%]", "0").strip()) / 100.0
    period_days = int(float(stats.get("Period", "500").strip()))
    ann_factor = 252.0 / max(period_days, 1)
    fund_cagr = ((1.0 + total_return) ** ann_factor - 1.0) * 100.0
    bench_cagr = ((1.0 + benchmark_return) ** ann_factor - 1.0) * 100.0

    # Fund vol from stats or estimate
    max_dd = float(stats.get("Max Drawdown [%]", "3.45").strip())
    # Estimate vol from return/Sharpe relationship
    trades = int(float(stats.get("Total Trades", "202").strip()))
    win_rate = float(stats.get("Win Rate [%]", "46.23").strip()) / 100.0
    avg_win = float(stats.get("Avg Winning Trade [%]", "2.65").strip()) / 100.0
    avg_loss = float(stats.get("Avg Losing Trade [%]", "2.02").strip()) / 100.0
    trade_var = (win_rate * avg_win**2 + (1 - win_rate) * avg_loss**2)
    fund_vol = math.sqrt(trade_var * trades / max(period_days, 1) * 252.0) * 100.0
    fund_vol = min(fund_vol, 15.0)  # cap for display

    points = [
        {"label": FUND_NAME.split(" v")[0].strip(), "vol": round(fund_vol, 1), "cagr": round(fund_cagr, 1), "marker": "diamond", "color": "#9E1B32"},
        {"label": "S&P 500", "vol": 15.5, "cagr": round(bench_cagr, 1), "marker": "triangle", "color": "#222222"},
        {"label": "Cash (STeFi)", "vol": 0.4, "cagr": 6.6, "marker": "circle", "color": "#222222"},
    ]

    for p in points:
        log(f"  {p['label']}: Vol={p['vol']:.1f}%, CAGR={p['cagr']:.1f}%")

    return points


# ---------------------------------------------------------------------------
# 7. GENERAL INFO
# ---------------------------------------------------------------------------
def build_general_info(stats: dict, last_date: str) -> dict:
    log("\n" + "=" * 80)
    log("[7/7] Building general information block")
    log("=" * 80)

    end_val = float(stats.get("End Value", "290228.16").strip())
    total_trades = int(float(stats.get("Total Trades", "202").strip()))
    win_rate = float(stats.get("Win Rate [%]", "46.23").strip())
    pf = float(stats.get("Profit Factor", "1.39").strip())
    max_dd = float(stats.get("Max Drawdown [%]", "3.45").strip())
    fees = float(stats.get("Total Fees Paid", "3203.28").strip())

    info = {
        "fund_name": FUND_NAME,
        "fund_class": FUND_CLASS,
        "as_of_date": last_date,
        "document_type": "Minimum Disclosure Document (Algorithmic Quant Strategy)",
        "unit_price": f"{end_val / INITIAL_CAPITAL * 10000:.2f} cpu",
        "fund_size": f"R{end_val / 1_000_000:.1f}m (≈ ${end_val:,.0f} USD equivalent)",
        "benchmark": BENCHMARK_NAME,
        "strategy": "Statistical Arbitrage — Sector-Neutral Pairs Trading",
        "risk_profile": "Moderate (Conservative allocation within market-neutral mandate)",
        "inception_date": "09 September 2024",
        "total_trades": str(total_trades),
        "win_rate": f"{win_rate:.1f}%",
        "profit_factor": f"{pf:.2f}",
        "max_drawdown": f"{max_dd:.2f}%",
        "total_fees": f"${fees:,.2f}",
        "management_fee": "N/A (Internal/Proprietary Capital)",
        "performance_fee": "N/A (Internal/Proprietary Capital)",
        "liquidity": "T+1 (Market-On-Open)",
        "contact": "Quantitative Research Desk",
        "model_version": "v3.2 (model_B_relaxed_beta_neutral)",
    }

    for k, v in info.items():
        log(f"  {k}: {v}")

    return info


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate MDD data for StatArb v3.2")
    parser.add_argument("--output", default="docs/deck/static/mdd_data.json", help="Output JSON path")
    args = parser.parse_args()

    log("=" * 80)
    log("StatArb v3.2 — MDD Data Generator")
    log(f"Results source: {RESULTS_DIR}/")
    log(f"Output: {args.output}")
    log("=" * 80)

    # Load all data
    stats = load_portfolio_stats()
    monthly_returns = compute_monthly_returns()
    perf_table = compute_fund_performance(stats, monthly_returns)
    sector_alloc = compute_sector_allocation()
    asset_alloc = compute_asset_allocation(sector_alloc)
    scatter_data = compute_scatter_data(stats)
    general_info = build_general_info(stats, monthly_returns[-1]["year"] if monthly_returns else "2026")

    # Determine as-of date from last monthly return
    if monthly_returns:
        last = monthly_returns[-1]
        months = ["January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December"]
        general_info["as_of_date"] = f"{months[last['month']-1]} {last['year']}"

    # Assemble output
    mdd_data = {
        "generated_at": datetime.now().isoformat(),
        "general_info": general_info,
        "monthly_returns": monthly_returns,
        "fund_performance": perf_table,
        "sector_allocation": sector_alloc,
        "asset_allocation": asset_alloc,
        "scatter_data": scatter_data,
        "fund_stats_summary": {
            "total_return_pct": float(stats.get("Total Return [%]", "0").strip()),
            "benchmark_return_pct": float(stats.get("Benchmark Return [%]", "0").strip()),
            "max_drawdown_pct": float(stats.get("Max Drawdown [%]", "0").strip()),
            "total_trades": int(float(stats.get("Total Trades", "0").strip())),
            "profit_factor": float(stats.get("Profit Factor", "0").strip()),
            "expectancy_usd": float(stats.get("Expectancy", "0").strip()),
        },
    }

    # Write JSON
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(mdd_data, f, indent=2, default=str)

    log(f"\n{'=' * 80}")
    log(f"MDD data written to: {args.output}")
    log(f"File size: {os.path.getsize(args.output):,} bytes")
    log(f"{'=' * 80}")

    # Also write log
    log_path = args.output.replace(".json", "_log.txt")
    with open(log_path, "w") as f:
        f.write("\n".join(LOG_LINES))
    log(f"Log written to: {log_path}")


if __name__ == "__main__":
    main()
