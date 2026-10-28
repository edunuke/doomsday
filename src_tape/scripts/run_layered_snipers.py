"""
LAYERED SNIPERS — Layer-2 (TSMOM) and Layer-3 (loud-trend rider), ILF panel.

Layer-2  TSMOM sniper, per name:
    ensemble sign of 3m/6m/12m return; position only when ALL agree (strict)
    or a majority (loose); size = inverse-vol scaled with per-name cap;
    exit: trailing stop (k x 20d-return-vol from the entry-time extreme price)
    or ensemble flip against the position. Holds: weeks-months.
Layer-3  Loud-trend rider:
    entry when |5d move| >= loud threshold AND lagged relvol >= 1.2
    (continuation — the other side of the validated volume discriminator);
    exit: trailing stop or 30d time stop.

Excludes SYN* and the ILF reference (index timing already tested).
Returns-based sim: daily r_p = sum(w_i * r_i) - fees - short borrow
+ SOFR on idle cash; T+1 (weights decided at t, applied t+1). Cost
conventions match the engine (exec_fee 1.5bps/side, borrow 50bps).

Evaluation: full stats, cohort split, PRE-REGISTERED fresh-start gate
(pos >= 70% and med >= +5%), and the blend bar — a layer is kept only if
blending with P0 raises combined Sharpe above P0's 0.83.
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.core.run_paths import run_file  # noqa: E402
from modules.risk.benchmark import sofr_daily_returns  # noqa: E402
from modules.regime import REGIMES  # noqa: E402

import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--panel", default="data/ILF_survivorship.csv")
_ap.add_argument("--base", default=run_file(61, "csv", "metrics.csv"))
_args, _ = _ap.parse_known_args()
_PANEL = _args.panel
px = pd.read_csv(_PANEL, index_col=0, parse_dates=True)
px = px[~px.index.duplicated()].sort_index().ffill()
ret = px.pct_change(fill_method=None)
_v = pd.read_csv("data/ILF_volume.csv", index_col=0, parse_dates=True)
_v = _v[~_v.index.duplicated()].sort_index().reindex(px.index)
RELVOL = (_v / _v.rolling(20, min_periods=5).mean()).shift(1)
NAMES = [c for c in px.columns if c != "ILF" and not c.startswith("SYN")]
CASH_R = sofr_daily_returns(px.index).fillna(0.0)
BORROW_D = (50.0 / 1e4) / 252.0
EXEC_FEE = 0.00015

P0 = pd.read_csv(_args.base, index_col=0, parse_dates=True)[
    "Live_Realized_Return"].astype(float)
print(f"[snipers] panel={_PANEL} base={_args.base}")

_mret = px["ILF"].pct_change().fillna(0.0)
_det = REGIMES["v3"](params={"regime_gate_window": 252, "regime_gate_pctile": 0.3,
                             "regime_disp_window": 21})
_states = _det.states(_mret).fillna(0.0)
states_calm = (_states == 0)          # 0 = calm, 1 = stressed (v3 encoding)


def atr20(name: str, i: int) -> float:
    """Trailing 20d return std (fraction) — our daily-bar ATR proxy."""
    lo = max(0, i - 20)
    v = ret[name].iloc[lo:i].std()
    return float(v) if np.isfinite(v) else 0.02


def w_layer2(ensemble_strict: bool = True, per_name_cap: float = 0.04,
             trail_mult: float = 3.0, calm_gate: bool = False,
             direction: str = "mom"):
    """Layer-2 builder: per-name TSMOM with trailing stops."""
    m3, m6, m12 = px / px.shift(63) - 1, px / px.shift(126) - 1, px / px.shift(252) - 1
    s3, s6, s12 = np.sign(m3), np.sign(m6), np.sign(m12)
    allpos, allneg = (s3 > 0) & (s6 > 0) & (s12 > 0), (s3 < 0) & (s6 < 0) & (s12 < 0)
    if ensemble_strict:
        ens = pd.DataFrame(0.0, index=px.index, columns=px.columns)
        ens[allpos], ens[allneg] = 1.0, -1.0
    else:
        ens = (s3.clip(-1, 1) + s6.clip(-1, 1) + s12.clip(-1, 1)) / 2.0  # loose: |sum|>=2
    if direction == "anti":        # anti-TSMOM: long the ensemble-NEGATIVE
        ens = -ens
    _mkt_ret = ret["ILF"].fillna(0.0)
    warm = int(252 + 130)

    W = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    state = {}
    for i in range(warm, len(px.index)):
        d = px.index[i]
        row = pd.Series(0.0, index=px.columns)
        for t in NAMES:
            pos_prev = W.at[px.index[i - 1], t]
            # ---- exits (trailing stop or ensemble flip) ----
            if pos_prev != 0 and t in state:
                direction, extreme = state[t]
                p_now = px.at[d, t]
                extreme = max(extreme, p_now) if direction == "L" else min(extreme, p_now)
                state[t] = (direction, extreme)
                stop_px = (extreme * (1 - trail_mult * atr20(t, i)) if direction == "L"
                           else extreme * (1 + trail_mult * atr20(t, i)))
                hit = (direction == "L" and p_now < stop_px) or \
                      (direction == "S" and p_now > stop_px)
                flip = (direction == "L" and ens.at[d, t] < 0) or \
                       (direction == "S" and ens.at[d, t] > 0)
                if hit or flip:
                    state.pop(t, None)
                    continue
                row[t] = pos_prev                      # hold, no drift
                continue
            # ---- entries ----
            s = ens.at[d, t]
            want = 1 if s > 0 else (-1 if s < 0 else 0)
            if want == 0 or t in state:
                continue
            if calm_gate and bool(states_calm.at[d] is False):
                continue
            size = float(np.clip(0.10 / max(atr20(t, i) * np.sqrt(252), 0.10),
                                 0.01, per_name_cap))
            row[t] = size if want > 0 else -size
            state[t] = ("L" if want > 0 else "S", px.at[d, t])
        W.loc[d] = row.values
    return W.ffill()


def w_rider(loud_thr: float = 0.05, relvol_min: float = 1.2,
            trail_mult: float = 3.0, max_hold: int = 30,
            calm_gate: bool = False):
    """Layer-3 builder: loud continuation entries, trailing stop, time stop."""
    r5 = px / px.shift(5) - 1.0
    W = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    state = {}
    for i in range(130, len(px.index)):
        d = px.index[i]
        row = pd.Series(0.0, index=px.columns)
        for t in NAMES:
            pos_prev = W.at[px.index[i - 1], t]
            if pos_prev != 0 and t in state:
                direction, extreme, entered = state[t]
                p_now = px.at[d, t]
                extreme = max(extreme, p_now) if direction == "L" else min(extreme, p_now)
                state[t] = (direction, extreme, entered)
                stop_px = (extreme * (1 - trail_mult * atr20(t, i)) if direction == "L"
                           else extreme * (1 + trail_mult * atr20(t, i)))
                hit = (direction == "L" and p_now < stop_px) or \
                      (direction == "S" and p_now > stop_px)
                expired = (d - entered).days >= max_hold
                if hit or expired:
                    state.pop(t, None)
                    continue
                row[t] = pos_prev
                continue
            r = r5.at[d, t]
            rv = RELVOL.at[d, t] if t in RELVOL.columns else np.nan
            if not (np.isfinite(r) and np.isfinite(rv)) or rv < relvol_min:
                continue
            if abs(r) < loud_thr:
                continue
            size = float(np.clip(0.10 / max(atr20(t, i) * np.sqrt(252), 0.10),
                                 0.01, 0.05))
            row[t] = size if r > 0 else -size
            state[t] = ("L" if r > 0 else "S", px.at[d, t], d)
        W.loc[d] = row.values
    return W.ffill()


# ---------------------------------------------------------------- simulation
def simulate(W: pd.DataFrame, label: str) -> pd.Series:
    """Returns-based daily P&L from a decision weight matrix (T+1 applied)."""
    Wl = W.reindex(columns=px.columns).fillna(0.0)
    Wlag = Wl.shift(1).fillna(0.0)
    gross = Wlag.abs().sum(axis=1)
    name_ret = ret[NAMES].fillna(0.0)
    pos_ret = (Wlag[NAMES] * name_ret).sum(axis=1)  # Series over dates
    fee = (Wlag.diff().abs().sum(axis=1) * EXEC_FEE).fillna(0.0)
    borrow = Wlag.clip(upper=0).abs().sum(axis=1) * BORROW_D
    idle = (1.0 - gross).clip(lower=0.0)
    r = pos_ret + idle * CASH_R - borrow - fee
    # financing on gross > 1 (borrowed capital at SOFR + 50bps)
    over = (gross - 1.0).clip(lower=0.0)
    r = r - over * (CASH_R + BORROW_D)
    r = r.iloc[260:]
    return r


def stats(r: pd.Series, label: str, fresh=True):
    tot = (1 + r).prod() - 1
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else float("nan")
    eq = (1 + r).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    ann = (1 + tot) ** (252 / len(r)) - 1
    print(f"  {label:34s} total {tot*100:+7.2f}%  ann {ann*100:+6.2f}%  "
          f"sharpe {sh:+.2f}  dd {dd*100:5.1f}%  vol {r.std()*np.sqrt(252)*100:.1f}%")
    _split = pd.Timestamp("2021-12-31")
    for name, sub in [("pre-2022", r[r.index <= _split]), ("2022+", r[r.index > _split])]:
        if len(sub) > 20 and sub.std() > 0:
            print(f"    {name:10s}: {((1+sub).prod()-1)*100:+7.2f}%  "
                  f"sharpe {sub.mean()/sub.std()*np.sqrt(252):+.2f}")
    if fresh:
        starts = ["2018-10", "2020-03", "2021-09", "2023-03", "2024-06"]
        rets = []
        for s in starts:
            sub = r.loc[r.index >= pd.Timestamp(s)].iloc[:504]
            if len(sub) < 20:
                continue
            rets.append(float((1 + sub).prod() - 1))
        pos_ct = sum(1 for x in rets if x > 0)
        med = float(np.median(rets)) if rets else float("nan")
        gate = "PASS" if pos_ct >= 0.7 * len(rets) and med >= 0.05 else "FAIL"
        print(f"    fresh: {[round(x*100, 1) for x in rets]}  pos={pos_ct}/{len(rets)}"
              f"  med={med*100:+.1f}%  GATE {gate}")
    return sh


if __name__ == "__main__":
    import itertools
    layers = []
    print("=== Layer-2 TSMOM variants ===")
    for strict, cap, tm in itertools.product([True, False], [0.04], [2.0, 3.0]):
        lbl = f"L2 strict={strict} trail={tm}"
        W = w_layer2(ensemble_strict=strict, per_name_cap=cap, trail_mult=tm)
        r = simulate(W, lbl)
        stats(r, lbl)
        layers.append((lbl, r))
    print("\n=== Layer-2 ANTI-TSMOM (long ensemble-negative) ===")
    for strict, tm in itertools.product([True, False], [2.0, 3.0]):
        lbl = f"L2-anti strict={strict} trail={tm}"
        W = w_layer2(ensemble_strict=strict, per_name_cap=0.04, trail_mult=tm,
                     direction="anti")
        r = simulate(W, lbl)
        stats(r, lbl)
        layers.append((lbl, r))
    print("\n=== Layer-3 loud-trend rider variants ===")
    for thr, tm in [(0.05, 3.0), (0.06, 2.0)]:
        lbl = f"L3 loud>{thr} trail={tm}"
        W = w_rider(loud_thr=thr, trail_mult=tm)
        r = simulate(W, lbl)
        stats(r, lbl)
        layers.append((lbl, r))
    print("\n=== Layer-3 CALM-GATED (stress -> no new entries) ===")
    for thr, tm in [(0.05, 3.0), (0.06, 2.0)]:
        lbl = f"L3-calm loud>{thr} trail={tm}"
        W = w_rider(loud_thr=thr, trail_mult=tm, calm_gate=True)
        r = simulate(W, lbl)
        stats(r, lbl)
        layers.append((lbl, r))

    print("\n=== BLEND with P0 (bar: combined Sharpe > 0.83) ===")
    common0 = P0.index
    for lbl, r in layers:
        common = common0.intersection(r.index)
        for wp, wl in [(0.7, 0.3), (0.5, 0.5)]:
            b = wp * P0.reindex(common).fillna(0.0) + wl * r.reindex(common).fillna(0.0)
            sh = b.mean() / b.std() * np.sqrt(252) if b.std() > 0 else float("nan")
            tot = (1 + b).prod() - 1
            flag = "PASS" if sh > 0.83 else "FAIL"
            print(f"  {lbl:34s} blend({wp:.0%}P0): sharpe {sh:+.2f}  "
                  f"total {tot*100:+6.2f}%  {flag}")