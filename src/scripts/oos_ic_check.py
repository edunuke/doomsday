"""Test whether per-pair IC persists: compare IS-scored vs OOS IC.

Splits pairs into PRESELECTED (top-ranked on 2006-2021) vs REJECTED,
then measures each group's IC on the OOS window (2021-09+).
"""
import sys, json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, "/Users/edunuke/Desktop/statarb-prival/src")
from modules.data.config_loader import Config
from modules.data.data_loader import DataLoaderStage
from modules.processing.residualizer import ResidualizerStage
from modules.processing.alpha_cube import AlphaCubeStage

cfg = Config(config_path="config_score.json")
ctx = {}
for Stage in (DataLoaderStage, ResidualizerStage, AlphaCubeStage):
    Stage(cfg).run(ctx)
alpha = ctx["alpha_cube"]
oos = ctx["start_idx"]

sel = {tuple(p) for p in json.load(open("data/preselect.json"))["pairs"]}

H = 5
def ic_on(df, sl):
    s = df.iloc[sl]
    z = s["Z_Spread"].astype(float)
    sr = s["Spread_Return"].astype(float)
    fwd = sum(sr.shift(-k) for k in range(1, H + 1))
    m = z.notna() & fwd.notna()
    if m.sum() < 100:
        return np.nan, np.nan
    zz, ff = z[m].values, fwd[m].values
    return np.corrcoef(zz, ff)[0, 1], spearmanr(zz, ff).correlation

rows_sel, rows_rej = [], []
for p, df in alpha.items():
    is_ic, _ = ic_on(df, slice(0, oos))
    oos_ic, _ = ic_on(df, slice(oos, None))
    if not np.isfinite(oos_ic) or not np.isfinite(is_ic):
        continue
    (rows_sel if (p in sel or (p[1], p[0]) in sel) else rows_rej).append((is_ic, oos_ic))

def summ(name, rows):
    a = np.array(rows)
    if not len(a):
        print(name, "none"); return
    is_ic, oos_ic = a[:, 0], a[:, 1]
    print(f"  {name:<14} n={len(a):>5}  IS IC={is_ic.mean():+.4f}  "
          f"OOS IC={oos_ic.mean():+.4f}  OOS %neg={100*(oos_ic<0).mean():.1f}%  "
          f"corr(IS,OOS)={np.corrcoef(is_ic,oos_ic)[0,1]:+.3f}")

print("=" * 78)
print("IC PERSISTENCE: does historical IC predict OOS IC?")
print("=" * 78)
summ("PRESELECTED", rows_sel)
summ("REJECTED", rows_rej)

allp = np.array(rows_sel + rows_rej)
print(f"\n  ALL pairs      n={len(allp):>5}  corr(IS IC, OOS IC) = "
      f"{np.corrcoef(allp[:,0], allp[:,1])[0,1]:+.4f}")
print("  -> near-zero correlation means historical IC does NOT persist.")
