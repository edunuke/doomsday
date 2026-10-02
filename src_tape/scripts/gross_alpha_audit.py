"""
GROSS-ALPHA AUDIT (read-only).

Answers one question: does the raw pair signal have ANY positive gross
expectancy, independent of the walk-forward, screening, allocator and costs?

For every pair in the alpha cube we measure:
  1. IC = corr(z_{t-1}, Spread_Return_t)   (should be < 0 for a reversion edge)
  2. naive reversion book with ZERO costs:
         pos_t = -sign(z_{t-1}) if |z_{t-1}| >= entry else 0   (held H days)
         pnl_t = pos_t * Spread_Return_t
     aggregated equal-weight -> daily gross return -> ann / Sharpe

If the naive gross book is <= 0, the signal is edge-less or sign-inverted and
no downstream tuning can help. If it is positive, the edge exists and costs /
execution are the problem.

Usage: python scripts/gross_alpha_audit.py --config configs/config_v3_simple_5y.json
"""
from __future__ import annotations
import argparse, os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.data import Config, DataLoaderStage
from modules.processing import ResidualizerStageV3
from modules.alpha import AlphaCubeStageV5


def build_cube(cfg_path: str):
    cfg = Config(config_path=cfg_path)
    ctx: dict = {}
    DataLoaderStage(cfg).run(ctx)
    ResidualizerStageV3(cfg).run(ctx)
    AlphaCubeStageV5(cfg).run(ctx)
    return cfg, ctx["alpha_cube"], ctx["prices_full"].index


def naive_book(cube, dates, entry=0.75, hold=2, exit_z=0.2):
    """Equal-weight gross reversion book, zero costs. Returns daily series."""
    port = np.zeros(len(dates))
    n_used = 0
    for p, df in cube.items():
        z = df["Z_Spread"].values
        sr = df["Spread_Return"].values
        if len(z) < 50:
            continue
        zl = np.concatenate([[np.nan], z[:-1]])          # z_{t-1}
        cond = np.isfinite(zl) & (np.abs(zl) >= entry)
        if not cond.any():
            continue
        raw = np.where(cond & (np.abs(zl) > exit_z), -np.sign(zl), np.nan)
        pos = pd.Series(raw).ffill(limit=hold).fillna(0.0).values
        port += np.nan_to_num(pos * sr)
        n_used += 1
    if n_used == 0:
        return pd.Series(0.0, index=dates), 0
    return pd.Series(port / n_used, index=dates), n_used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_v3_simple_5y.json")
    a = ap.parse_args()
    cfg, cube, dates = build_cube(a.config)
    print(f"pairs={len(cube)}  days={len(dates)}")
    entry = float(cfg.get("entry_z_base", 0.75))

    # ---- IC (pooled + per pair) -------------------------------------------
    zs, srs = [], []
    per_ic = []
    for p, df in cube.items():
        z = df["Z_Spread"].values
        sr = df["Spread_Return"].values
        if len(z) < 50:
            continue
        zl = z[:-1]; ft = sr[1:]
        m = np.isfinite(zl) & np.isfinite(ft)
        if m.sum() > 30:
            zs.append(zl[m]); srs.append(ft[m])
            if np.std(zl[m]) > 0 and np.std(ft[m]) > 0:
                per_ic.append(np.corrcoef(zl[m], ft[m])[0, 1])
    pooled_ic = np.corrcoef(np.concatenate(zs), np.concatenate(srs))[0, 1]
    per_ic = np.array(per_ic)
    print(f"IC pooled  corr(z_t-1, sr_t) = {pooled_ic:+.4f}   "
          f"(<0 = reversion edge)")
    print(f"IC per-pair mean={per_ic.mean():+.4f} median={np.median(per_ic):+.4f} "
          f"frac<0={(per_ic < 0).mean()*100:.0f}%")

    # ---- naive gross book (zero costs) ------------------------------------
    print("\nnaive gross reversion book (ZERO costs), entry |z|>={:.2f}:".format(entry))
    for hold in (1, 2, 5, 10):
        r, n = naive_book(cube, dates, entry=entry, hold=hold)
        ann = (1 + r).prod() ** (252 / max(len(r), 1)) - 1
        sd = r.std()
        sharpe = r.mean() / sd * np.sqrt(252) if sd > 0 else 0.0
        print(f"  hold={hold:2d}d  pairs={n:4d}  ann {ann*100:+6.2f}%  "
              f"Sharpe {sharpe:+.2f}  vol {sd*np.sqrt(252)*100:.1f}%")


if __name__ == "__main__":
    raise SystemExit(main())
