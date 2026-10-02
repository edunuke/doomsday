"""Plot SPY vs synthetic (doomed) names on one normalized log chart.
Usage: python scripts/plot_survivorship.py [--config configs/config_v3_wf3_5y.json]
Output: data/imgs/survivorship_ts.png
"""
from __future__ import annotations
import argparse, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_v3_wf3_5y.json")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, a.config)))
    base = os.path.basename(cfg["price_csv"])
    panel = pd.read_csv(os.path.join(HERE, "data", base[:-4] + "_survivorship.csv"),
                        index_col=0, parse_dates=True)
    spy = panel["SPY"].dropna()
    spy = spy / spy.iloc[0]
    syn = [c for c in panel.columns if str(c).startswith("SYN")]

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(13, 7))
    for c in syn:
        s = panel[c].dropna()
        if len(s) > 5:
            ax.plot(s.index, s / s.iloc[0], lw=0.7, alpha=0.5, color="#b03030")
    ax.plot(spy.index, spy.values, lw=2.2, color="#111", label="SPY (survivor index)")
    ax.plot([], [], lw=0.8, color="#b03030", label=f"synthetic doomed names (n={len(syn)})")
    ax.set_yscale("log")
    ax.set_title("SPY vs synthetic (doomed) names — normalized, log scale")
    ax.set_ylabel("normalized price (log)")
    ax.legend(loc="lower left")
    ax.grid(alpha=0.25)
    out = os.path.join(HERE, "data", "imgs", "survivorship_ts.png")
    fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)
    print(f"wrote {out}  (SPY + {len(syn)} synthetics)")


if __name__ == "__main__":
    raise SystemExit(main())
