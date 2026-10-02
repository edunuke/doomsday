"""PAIR PRESELECTION BY PREDICTIVE QUALITY (rank-IC ensemble).

Scores every candidate pair on multiple ex-ante predictive tests computed
strictly on data BEFORE the OOS window, then keeps the top-N by composite
cross-sectional rank. Decouples the (long) scoring window from the (short)
strategy warmup.

Usage:
    python pair_scoring.py [config.json] [--top 1500] [--h 5] [--out data/preselect.json]
"""
import sys
import json
import argparse
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, "/Users/edunuke/Desktop/statarb-prival/src")
from modules.data.config_loader import Config
from modules.data.data_loader import DataLoaderStage
from modules.processing.residualizer import ResidualizerStage
from modules.processing.alpha_cube import AlphaCubeStage

ap = argparse.ArgumentParser()
ap.add_argument("config", nargs="?", default="config_10y.json")
ap.add_argument("--top", type=int, default=1500)
ap.add_argument("--h", type=int, default=5, help="forward horizon (days)")
ap.add_argument("--out", default="data/preselect.json")
ap.add_argument("--min-obs", type=int, default=400)
args = ap.parse_args()

cfg = Config(config_path=args.config)
ctx = {}
for Stage in (DataLoaderStage, ResidualizerStage, AlphaCubeStage):
    Stage(cfg).run(ctx)

alpha = ctx["alpha_cube"]
oos_start = ctx["start_idx"]
print(f"\n[Scoring] pairs={len(alpha)}  OOS start_idx={oos_start}  "
      f"scoring window = rows [0, {oos_start})")


def pair_metrics(df, h, oos_start, min_obs):
    s = df.iloc[:oos_start]                      # pre-OOS only
    z = s["Z_Spread"].astype(float)
    sr = s["Spread_Return"].astype(float)
    fwd = sum(sr.shift(-k) for k in range(1, h + 1))
    m = z.notna() & fwd.notna()
    if m.sum() < min_obs:
        return None
    zz, ff = z[m].values, fwd[m].values
    trade = -np.sign(zz) * ff                     # what the engine books
    # 1-2 IC
    pear = np.corrcoef(zz, ff)[0, 1]
    spear = spearmanr(zz, ff).correlation
    # 3-4 trade return level + significance
    tmean = trade.mean()
    tstat = tmean / (trade.std(ddof=1) / np.sqrt(len(trade))) if trade.std() > 0 else 0.0
    # 5 quantile monotonicity of trade return vs |Z|
    try:
        q = pd.qcut(np.abs(zz), 5, labels=False, duplicates="drop")
        qt = pd.Series(trade).groupby(q).mean()
        mono = spearmanr(qt.index.values, qt.values).correlation if len(qt) >= 3 else 0.0
    except Exception:
        mono = 0.0
    # 6 signal sharpe
    sharpe = tmean / trade.std(ddof=1) if trade.std() > 0 else 0.0
    # 7-9 from alpha cube at last pre-OOS row
    hl = float(s["Half_Life"].iloc[-1]) if "Half_Life" in s else np.nan
    hu = float(s["Hurst"].iloc[-1]) if "Hurst" in s else np.nan
    return dict(pearson_ic=pear, spearman_ic=spear, trade_mean=tmean,
                trade_tstat=tstat, quantile_mono=mono, signal_sharpe=sharpe,
                half_life=hl, hurst=hu, nobs=len(trade))


rows = {}
for pair, df in alpha.items():
    r = pair_metrics(df, args.h, oos_start, args.min_obs)
    if r is not None:
        rows[pair] = r

met = pd.DataFrame(rows).T
print(f"[Scoring] scored {len(met)} pairs with >= {args.min_obs} pre-OOS obs\n")

# ---- composite rank score: average of directional cross-sectional ranks ----
# +rank for "higher is better", -rank for "lower is better"
comp = pd.DataFrame(index=met.index)
comp["pearson_ic"]    = (-met["pearson_ic"]).rank(pct=True)      # want negative IC
comp["spearman_ic"]   = (-met["spearman_ic"]).rank(pct=True)
comp["trade_mean"]    = met["trade_mean"].rank(pct=True)
comp["trade_tstat"]   = met["trade_tstat"].rank(pct=True)
comp["quantile_mono"] = met["quantile_mono"].rank(pct=True)
comp["signal_sharpe"] = met["signal_sharpe"].rank(pct=True)
comp["hurst"]         = (-met["hurst"]).rank(pct=True)           # want low hurst
met["composite"] = comp.mean(axis=1)

met = met.sort_values("composite", ascending=False)
print("=" * 78)
print("TOP 15 PAIRS BY COMPOSITE RANK SCORE")
print("=" * 78)
cols = ["composite", "pearson_ic", "spearman_ic", "trade_mean",
        "trade_tstat", "quantile_mono", "signal_sharpe", "hurst", "nobs"]
print(met.head(15)[cols].round(4).to_string())

print("\nBOTTOM 10 (worst) PAIRS")
print(met.tail(10)[cols].round(4).to_string())

top = met.head(args.top)
sel = [list(p) for p in top.index]
json.dump({"pairs": sel, "top": args.top, "h": args.h,
           "scored": len(met), "config": args.config},
          open(args.out, "w"), indent=2)
print(f"\n[Scoring] selected top {len(sel)} of {len(met)} -> {args.out}")
print(f"[Scoring] score range: {met['composite'].max():.3f} .. {met['composite'].min():.3f}")
