"""Information-Coefficient (IC) study for the Kalman z-score signal.

Question: does Z_Spread(t) predict the forward spread return over the next
1/2/3/5 days? A working mean-reversion signal requires NEGATIVE IC (high Z ->
spread falls). We also compute the trade-conditional P&L
    trade_ret_h = -sign(Z) * fwd_spread_return_h
which is exactly what the engine books if it shorts high-Z and longs low-Z.

Usage: python ic_study.py [config.json]
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/edunuke/Desktop/statarb-prival/src")

from modules.data.config_loader import Config
from modules.data.data_loader import DataLoaderStage
from modules.processing.residualizer import ResidualizerStage
from modules.processing.alpha_cube import AlphaCubeStage

CFG = sys.argv[1] if len(sys.argv) > 1 else "config_1y.json"

cfg = Config(config_path=CFG)
ctx = {}
for Stage in (DataLoaderStage, ResidualizerStage, AlphaCubeStage):
    Stage(cfg).run(ctx)

alpha = ctx["alpha_cube"]
start = ctx["start_idx"]
print(f"\n[IC] alpha cube pairs={len(alpha)}  OOS start_idx={start}")

HORIZONS = [1, 2, 3, 5]
# pooled (z, fwd_h, trade_ret_h) accumulators
pooled = {h: {"z": [], "fwd": []} for h in HORIZONS}
per_pair_ic = {h: [] for h in HORIZONS}

for pair, df in alpha.items():
    s = df.iloc[start:]
    z = s["Z_Spread"].astype(float)
    sr = s["Spread_Return"].astype(float)
    for h in HORIZONS:
        fwd = sum(sr.shift(-k) for k in range(1, h + 1))
        mask = z.notna() & fwd.notna()
        if mask.sum() < 100:
            continue
        zz, ff = z[mask], fwd[mask]
        ic = np.corrcoef(zz, ff)[0, 1]
        if np.isfinite(ic):
            per_pair_ic[h].append(ic)
        pooled[h]["z"].append(zz.values)
        pooled[h]["fwd"].append(ff.values)

print("\n" + "=" * 78)
print("PER-PAIR IC  (corr(Z_t, forward spread return) — want NEGATIVE)")
print("=" * 78)
print(f"{'horizon':>8} {'mean IC':>10} {'median IC':>11} {'std':>9} {'%neg':>7} {'pairs':>7}")
for h in HORIZONS:
    a = np.array(per_pair_ic[h])
    if len(a):
        print(f"{h:>7}d {a.mean():>10.4f} {np.median(a):>11.4f} {a.std():>9.4f} "
              f"{100*(a<0).mean():>6.1f}% {len(a):>7}")

print("\n" + "=" * 78)
print("POOLED DECILE ANALYSIS  (trade_ret = -sign(Z) * fwd return; want >0)")
print("=" * 78)
for h in HORIZONS:
    z = np.concatenate(pooled[h]["z"])
    f = np.concatenate(pooled[h]["fwd"])
    trade = -np.sign(z) * f
    # deciles by |Z| magnitude
    try:
        dec = pd.qcut(np.abs(z), 10, labels=False, duplicates="drop")
    except Exception:
        continue
    print(f"\n  Horizon {h}d   (N={len(z):,})")
    print(f"  {'|Z| decile':>11} {'mean|Z|':>9} {'mean fwd%':>11} {'trade ret%':>12} {'hit%':>7}")
    for d in range(10):
        m = dec == d
        if m.sum() == 0:
            continue
        print(f"  {d+1:>11} {np.abs(z[m]).mean():>9.2f} {f[m].mean()*100:>10.4f}% "
              f"{trade[m].mean()*100:>11.4f}% {100*(trade[m]>0).mean():>6.1f}%")
    # overall
    print(f"  {'ALL':>11} {np.abs(z).mean():>9.2f} {f.mean()*100:>10.4f}% "
          f"{trade.mean()*100:>11.4f}% {100*(trade>0).mean():>6.1f}%")

# ---------------------------------------------------------------------------
# BREAK-EVEN THRESHOLD: net trade return after round-trip cost
# ---------------------------------------------------------------------------
# Round trip = entry (2 legs) + exit (2 legs) at exec_fee 1.5bp/leg = 6bp,
# plus ~10d borrow at borrow_bps/365. Cost expressed per unit notional.
EXEC = cfg.get("exec_fee", 0.00015)
BORROW = cfg.get("borrow_bps", 50.0) / 10000.0
COST = EXEC * 4.0 + BORROW * (5.0 / 365.0)   # holds ~5d
print("\n" + "=" * 78)
print(f"BREAK-EVEN: net = trade_ret - cost, cost={COST*100:.3f}% (5d hold)")
print("=" * 78)
print(f"{'|Z| >= T':>9} {'N':>9} {'gross%':>10} {'net%':>10} {'hit%':>8} {'$ per 30% sleeve':>18}")
h = 5
z = np.concatenate(pooled[h]["z"]); f = np.concatenate(pooled[h]["fwd"])
trade = -np.sign(z) * f
for T in [0.4, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]:
    m = np.abs(z) >= T
    if m.sum() < 50:
        continue
    g = trade[m].mean()
    net = g - COST
    print(f"{T:>9.2f} {m.sum():>9,} {g*100:>9.4f}% {net*100:>9.4f}% "
          f"{100*(trade[m]>0).mean():>7.1f}% {net*0.30*250000:>17.2f}")

print("\n[IC] done.")
