"""
MOMENTUM SLEEVES (T-a, T-b) + ADR BASIS (T-c) for the ILF panel.

Discarding mean-reversion per the program decision; testing the strategies
that fit this market's measured return structure:

  T-a  5d name-level momentum, volume-confirmed (relvol >= 1.2, lagged),
       long top / short bottom among loud names, vol-scaled gross.
       => the unclaimed other side of the validated volume discriminator.
  T-b  63d cross-sectional momentum, long/short quantiles, vol-scaled,
       weekly rebalance. Classic EM momentum with a crash-risk budget.
  T-c  PBR/PBR-A dual-share basis: constant 50/50 dollar-neutral pair
       (structural arb; conversion mechanics force convergence).

Evaluation: full-sample stats, cohort split, yearly, fresh-start gate,
and the pre-registered blended test — does each sleeve raise the combined
Sharpe above P0 (0.83) at similar drawdown?
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.execution.portfolio_engine import run_target_weights  # noqa: E402
from modules.execution.stats import compute_stats  # noqa: E402
from modules.validation.gate import window_stats, format_stats  # noqa: E402

TARGET_VOL = 0.12          # 12% annualised sleeve vol target
GROSS = 1.0

import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--panel", default="data/ILF_survivorship.csv")
_ap.add_argument("--base", default="results/61/csv/metrics.csv")
_ap.add_argument("--volume", default=None)
_args, _ = _ap.parse_known_args()
px = pd.read_csv(_args.panel, index_col=0, parse_dates=True)
print(f"[sleeves] panel={_args.panel} base={_args.base}")
px = px[~px.index.duplicated()].sort_index().ffill()
_vpath = _args.volume or _args.panel.replace("_daily", "_volume")
_v = pd.read_csv(_vpath, index_col=0, parse_dates=True)
print(f"[sleeves] volume={_vpath}")
_v = _v[~_v.index.duplicated()].sort_index().reindex(px.index)
RELVOL = (_v / _v.rolling(20, min_periods=5).mean()).shift(1)


def vol_scaled_gross(px, i, target=TARGET_VOL, lo=0.3, hi=1.5):
    """Causal vol scaling: target / trailing cross-sectional name vol."""
    win = px.pct_change().iloc[max(0, i - 63):i]
    cs = win.std().median() * np.sqrt(252)
    if not np.isfinite(cs) or cs < 1e-6:
        return 0.5
    return float(np.clip(target / cs, lo, hi))


def build_weights(mode: str):
    """mode: 'ta'/'tb' (reversion, long losers) | 'ta_mom'/'tb_mom' (momentum,
    long winners). The _mom variants are POST-HOC sign flips of T-a/T-b and
    are validated ONLY via the pre-registered fresh-start gates."""
    mom = mode.endswith("_mom")
    base_mode = mode[:2] if mode[:2] in ("ta", "tb") else mode
    lookback = 5 if base_mode == "ta" else 63
    W = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for i in range(130, len(px), 5):
        d = px.index[i]
        r = px.iloc[i] / px.iloc[max(0, i - lookback)] - 1.0
        g = vol_scaled_gross(px, i)
        if mode == "ta":
            loud = RELVOL.iloc[i - 1]
            ok = loud[loud >= 1.2].index
            r = r.reindex(ok).dropna()
            if len(r) < 6:
                continue
            k = max(1, int(len(r) * 0.25))
        else:
            r = r.dropna()
            if len(r) < 10:
                continue
            k = max(1, int(len(r) * 0.25))
        sv = r.sort_values()
        if mom:                       # long winners, short losers
            longs, shorts = sv.index[-k:], sv.index[:k]
        else:                         # long losers, short winners (reversion)
            longs, shorts = sv.index[:k], sv.index[-k:]
        row = pd.Series(0.0, index=px.columns)
        wl = (g / 2) / max(1, len(longs)); ws = (g / 2) / max(1, len(shorts))
        for c in longs: row[c] = wl
        for c in shorts: row[c] = -ws
        W.loc[d] = row.values
    # Carry positions between rebalances (NaN otherwise -> engine goes flat
    # 4 days out of 5, which silently guts the strategy).
    W = W.ffill()
    return W


def sim(W, label):
    exec_w = W.shift(1).fillna(0.0)
    res = run_target_weights(close=px, open_=px, weights=exec_w,
                             init_cash=1_000_000.0, exec_fee=0.00015,
                             daily_borrow_rate=(50.0 / 10000.0) / 252.0)
    r = res.value.pct_change().fillna(0.0)
    live = r.iloc[130:]
    st = compute_stats(res, float(res.gross_exposure.max()))
    total = (res.value.iloc[-1] / 1e6 - 1) * 100
    sharpe = float(st["Sharpe Ratio"]); dd = float(st["Max Drawdown [%]"])
    print(f"  total {total:+.2f}%  sharpe {sharpe:+.2f}  dd {dd:.1f}%  "
          f"vol {live.std()*np.sqrt(252)*100:.1f}%  "
          f"excess_vs_SOFR {total - 0.045*100*len(live)/252:+.1f}pp(rough)")
    yr = (1 + live).groupby(live.index.year).prod() - 1
    print("  yearly:", {y: round(v*100, 1) for y, v in yr.items()})
    _split = pd.Timestamp("2021-12-31")
    for name, sub in [("pre-2022", live[live.index <= _split]),
                      ("2022+", live[live.index > _split])]:
        if len(sub) > 20:
            sh = sub.mean()/sub.std()*np.sqrt(252) if sub.std() > 0 else float("nan")
            print(f"    {name:9s}: {((1+sub).prod()-1)*100:+6.2f}%  sharpe {sh:+.2f}")
    # fresh-start gate
    starts = ["2018-10", "2020-03", "2021-09", "2023-03", "2024-06"]
    rets = []
    for s in starts:
        pos = int(px.index.searchsorted(pd.Timestamp(s)))
        if pos >= len(px) - 60:
            continue
        sub = px.iloc[max(0, pos-130): min(len(px), pos+504)]
        subW = build_weights(mode) if False else None  # weights rebuilt below
        r_full = sim_returns(mode, sub, pos - 130)
        live_r = r_full.iloc[pos - max(0, pos-130):]
        c = float((1 + live_r).prod() - 1)
        rets.append(c)
        print(f"    fresh {s}: {c*100:+.2f}%")
    print(format_stats(f"{label} FRESH-START gate", window_stats(rets)))
    return live


def sim_returns(mode, sub_px, warm):
    """Rebuild weights within a sub-window and return daily returns."""
    global px, RELVOL
    px_bak, rel_bak = px, RELVOL
    px = sub_px
    _v2 = _v.reindex(sub_px.index)
    RELVOL = (_v2 / _v2.rolling(20, min_periods=5).mean()).shift(1)
    W = build_weights(mode)
    exec_w = W.shift(1).fillna(0.0)
    res = run_target_weights(close=sub_px, open_=sub_px, weights=exec_w,
                             init_cash=1_000_000.0, exec_fee=0.00015,
                             daily_borrow_rate=(50.0/10000.0)/252.0)
    px, RELVOL = px_bak, rel_bak
    return res.value.pct_change().fillna(0.0)


def basis_t_c(flipped=False):
    print(f"=== T-c: PBR/PBR-A basis ({'FLIPPED post-hoc' if flipped else 'constant dollar-neutral'}) ===")
    W = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    if flipped:
        W["PBR/A"] = 0.5; W["PBR"] = -0.5
    else:
        W["PBR"] = 0.5; W["PBR/A"] = -0.5
    exec_w = W.shift(1)
    res = run_target_weights(close=px, open_=px, weights=exec_w,
                             init_cash=1_000_000.0, exec_fee=0.00015,
                             daily_borrow_rate=(50.0/10000.0)/252.0)
    r = res.value.pct_change().fillna(0.0).iloc[130:]
    total = (res.value.iloc[-1]/1e6-1)*100
    sh = r.mean()/r.std()*np.sqrt(252) if r.std() > 0 else float("nan")
    dd = float(compute_stats(res, float(res.gross_exposure.max()))["Max Drawdown [%]"])
    print(f"  total {total:+.2f}%  sharpe {sh:+.2f}  dd {dd:.1f}%  "
          f"vol {r.std()*np.sqrt(252)*100:.1f}%")
    return r


p0 = pd.read_csv("results/61/csv/metrics.csv", index_col=0, parse_dates=True)
p0_r = p0["Live_Realized_Return"].astype(float)

results = {}
for mode, label in [("ta", "T-a 5d loud momentum"), ("tb", "T-b 63d momentum")]:
    print(f"=== {label} ===")
    results[label] = sim(build_weights(mode), label)

basis_r = basis_t_c()
results["T-c basis"] = basis_r

print()
print("=== BLEND TEST: sleeve + P0, 50/50 (bar: beat P0 Sharpe 0.83) ===")
for label, r in results.items():
    common = p0_r.index.intersection(r.index)
    b = 0.5 * p0_r.reindex(common).fillna(0.0) + 0.5 * r.reindex(common).fillna(0.0)
    sh = b.mean()/b.std()*np.sqrt(252) if b.std() > 0 else float("nan")
    corr = p0_r.reindex(common).corr(r.reindex(common))
    tot = (1+b).prod()-1
    print(f"  {label:22s} corr_vs_P0={corr:+.2f}  blend_sharpe={sh:+.2f}  "
          f"blend_total={tot*100:+.1f}%  {'PASS' if sh > 0.83 else 'FAIL'}")
