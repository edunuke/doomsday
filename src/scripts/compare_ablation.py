"""Compare warmup-ablation runs: 1y / 2y / 5y (identical OOS window).

Usage:
    python compare_ablation.py <run_dir_1y> <run_dir_2y> <run_dir_5y>
"""
import sys
import csv
import os

RUNS = {
    "1y": sys.argv[1] if len(sys.argv) > 1 else "results/22",
    "2y": sys.argv[2] if len(sys.argv) > 2 else "results/23",
    "5y": sys.argv[3] if len(sys.argv) > 3 else "results/21",
}

FIELDS = [
    "Total Return [%]", "Benchmark Return [%]", "Max Drawdown [%]",
    "Profit Factor", "Win Rate [%]", "Total Trades", "Expectancy",
    "Total Fees Paid", "Sharpe Ratio", "Sortino Ratio", "Omega Ratio",
    "Calmar Ratio", "End Value",
]


def load(run_dir):
    path = os.path.join(run_dir, "csv", "portfolio_stats.csv")
    if not os.path.exists(path):
        return None
    out = {}
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                out[row[0].strip()] = row[1].strip()
    return out


data = {k: load(v) for k, v in RUNS.items()}

print(f"{'Metric':<26} {'1y':>14} {'2y':>14} {'5y':>14}")
print("-" * 72)
for fld in FIELDS:
    vals = []
    for k in ("1y", "2y", "5y"):
        d = data[k]
        vals.append(d.get(fld, "—") if d else "pending")
    print(f"{fld:<26} {vals[0]:>14} {vals[1]:>14} {vals[2]:>14}")

print("\nRuns:")
for k, v in RUNS.items():
    print(f"  {k}: {v}")