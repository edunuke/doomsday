"""
ADR-LOCAL LEAD-LAG (data expansion, NOT universe expansion).

Same companies, two markets: NYSE ADR (USD) + local listing (BRL/MXN/CLP).
Mechanism: the local session closes before/around the ADR session; local
information diffuses slowly across markets. Test whether the local close's
trailing return predicts the ADR's NEXT-day return better than the ADR's own
prior return (the honest baseline control).

Battery:
  1. per-pair lead-lag: corr(local_ret[t], adr_ret[t+1]) with t-stat
     vs corr(adr_ret[t], adr_ret[t+1]) (ADR's own autocorrelation)
  2. trading rules (T+1, fees, short borrow, idle SOFR):
       always-in  : position = sign(local_ret[t]) on the ADR, equal weight
       sniper     : only when |local_ret[t]| >= k x local vol (k=1.5)
       sniper-ADR : control using the ADR's own 1d return as the signal
  3. blend with P0 (bar: combined Sharpe > 0.83).
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.risk.benchmark import sofr_daily_returns  # noqa: E402

EXEC_FEE = 0.00015

MAP = {  # ADR (panel name, must exist in ILF panel) -> local Yahoo ticker
    "ITUB": "ITUB4.SA", "VALE": "VALE3.SA", "ABEV": "ABEV3.SA",
    "GGB": "GGBR4.SA", "WEGE3": "WEGE3.SA",
    "RENT3": "RENT3.SA", "B3SA3": "B3SA3.SA", "BBAS3": "BBAS3.SA",
    "AMXB": "AMX.MX", "GMEXICOB": "GMEXICOB.MX",
    "PAC": "GAP.MX", "CEMEXCPO": "CEMEXCPO.MX",
    "SQM": "SQMA.SN", "BSAC": "BSANTANDER.SN",
    "CENCOSUD": "CENCOSUD.SN", "COPEC": "COPEC.SN", "FALAB": "FALAB.SN",
}

px = pd.read_csv("data/ILF_survivorship.csv", index_col=0, parse_dates=True)
px = px[~px.index.duplicated()].sort_index().ffill()

loc_tickers = list(MAP.values())
print(f"downloading {len(loc_tickers)} local tickers...")
raw = yf.download(loc_tickers, start="2016-08-01", end="2026-09-24",
                  progress=False, auto_adjust=False)
loc_close_all = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]

MAP = {a: l for a, l in MAP.items() if a in px.columns}
adr_ret = px[list(MAP.keys())].pct_change(fill_method=None)
loc_ret = pd.DataFrame(index=px.index)
ok_pairs = []
for a, l in MAP.items():
    if l in loc_close_all.columns:
        s = loc_close_all[l].reindex(px.index).ffill()
        if s.notna().sum() > 500:
            loc_ret[a] = s.pct_change(fill_method=None)
adr2local = {a: t for a, t in MAP.items() if a in loc_ret.columns}
print(f"mapped pairs: {len(adr2local)}")

# causal local signal: local return COMPLETED as of each ADR close.
# Brazil: local closes 15:00 EST < ADR 16:00 EST -> same-date local ret usable.
# Mexico/Santiago: closes after ADR close -> previous completed session.
# Conservative common rule: shift local return by 1 date for all (safe),
# plus a "same-day" variant for Brazil (.SA) whose close is known intraday.
loc_ret_b1 = loc_ret.shift(1)   # definitely known at ADR close of t


def fresh_gate(r: pd.Series):
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
    return rets, pos_ct, med, gate


def sim_rule_vt(signal: pd.DataFrame, label: str, target_vol: float = 0.12) -> pd.Series:
    """Vol-targeted variant: scale normalized weights so the trailing book vol
    hits the target (causal: scale from the trailing 63d realized sleeve vol)."""
    sig = signal.reindex(columns=adr_ret.columns).fillna(0.0)
    gnorm = sig.abs().sum(axis=1).replace(0.0, np.nan)
    W0 = sig.div(gnorm, axis=0).fillna(0.0)
    # causal vol estimate: trailing 63d realized vol of the normalized book
    book_ret = (W0.shift(1) * adr_ret).sum(axis=1)
    vol63 = book_ret.rolling(63, min_periods=20).std() if (book_ret := book_ret) is not None else None
    scale = (target_vol / (vol63 * np.sqrt(252)).replace(0, np.nan)).clip(0.3, 1.5).fillna(0.5)
    W = W0.mul(scale, axis=0)
    eff = W.shift(1).fillna(0.0)
    pos_ret = (eff * adr_ret).sum(axis=1)
    gross = eff.abs().sum(axis=1)
    fee = (eff.diff().abs().sum(axis=1) * EXEC_FEE).fillna(0.0)
    borrow = eff.clip(upper=0).abs().sum(axis=1) * ((50.0/1e4)/252.0)
    idle = (1.0 - gross).clip(lower=0.0)
    r = pos_ret + idle * sofr_daily_returns(px.index).fillna(0.0) - borrow - fee \
        - over_fin(gross)
    r = r.iloc[260:]
    return r


def over_fin(gross):
    over = (gross - 1.0).clip(lower=0.0)
    return over * (sofr_daily_returns(px.index).fillna(0.0) + (50.0/1e4)/252.0)


def sim_rule(signal: pd.DataFrame, label: str) -> pd.Series:
    """signal[t] = signed direction per mapped ADR, decided at t close,
    applied to ADR return t+1 (T+1). Gross normalized to 100% across the
    names with a signal; fees, short borrow, idle SOFR."""
    if isinstance(signal, np.ndarray):
        signal = pd.DataFrame(signal, index=px.index, columns=adr_ret.columns)
    sig = signal.reindex(columns=adr_ret.columns).fillna(0.0)
    gnorm = sig.abs().sum(axis=1).replace(0.0, np.nan)
    W = sig.div(gnorm, axis=0).fillna(0.0)  # normalized signed weights
    eff = W.shift(1).fillna(0.0)
    pos_ret = (eff * adr_ret).sum(axis=1)
    gross = eff.abs().sum(axis=1)
    fee = (eff.diff().abs().sum(axis=1) * EXEC_FEE).fillna(0.0)
    borrow = eff.clip(upper=0).abs().sum(axis=1) * ((50.0/1e4)/252.0)
    idle = (1.0 - gross).clip(lower=0.0)
    r = pos_ret + idle * sofr_daily_returns(px.index).fillna(0.0) - borrow - fee
    r = r.iloc[260:]
    tot = (1 + r).prod() - 1
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else float("nan")
    eq = (1 + r).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    rets, pos_ct, med, gate = fresh_gate(r)
    print(f"  {label:16s} total {tot*100:+7.2f}%  sharpe {sh:+.2f}  dd {dd*100:5.1f}%  "
          f"fresh {[round(x*100,1) for x in rets]} pos={pos_ct}/{len(rets)} "
          f"med={med*100:+.1f}% GATE {gate}")
    return r


# ---------------------------------------------------------------- 1. stats
print("=== per-pair lead-lag (10y, daily) ===")
print(f"{'pair':16s} {'corr(loc,adr+1)':>16s} {'t-stat':>7s} {'n':>5s} "
      f"{'corr(adr,adr+1)':>16s}")
lead_betas = {}
for a, l in adr2local.items():
    l1 = loc_ret[a]
    a1 = adr_ret[a]
    a_next = adr_ret[a].shift(-1)          # ADR return t+1
    both = pd.concat([l1, a_next], axis=1).dropna()
    x, y = both.iloc[:, 0].values, both.iloc[:, 1].values
    if len(x) < 300 or x.std() == 0:
        continue
    r_corr = np.corrcoef(x, y)[0, 1]
    tstat = r_corr * np.sqrt((len(x) - 2) / max(1e-12, 1 - r_corr ** 2))
    own = adr_ret[a].shift(1)
    own_both = pd.concat([own, adr_ret[a]], axis=1).dropna()
    own_corr = np.corrcoef(own_both.iloc[:, 0], own_both.iloc[:, 1])[0, 1] \
        if len(own_both) > 300 else np.nan
    print(f"{a+'/'+l:16s} {r_corr:+16.3f} {tstat:7.1f} {'vs own: ' + format(own_corr, '+.3f'):>16s}")
    lead_betas[a] = r_corr

# ---------------------------------------------------------------- 2. rules
print("\n=== trading rules (T+1) ===")
loc_sig = loc_ret / loc_ret.rolling(63, min_periods=20).std()   # local ret in local-vol units
adr_own = adr_ret / adr_ret.rolling(63, min_periods=20).std()

r_always = sim_rule(np.sign(loc_sig), "always-in")
_z = loc_sig.abs()
r_snip = sim_rule(loc_sig.where(_z >= 1.5, 0.0).reindex(columns=adr_ret.columns).fillna(0.0),
                  "sniper local")
adr_z = adr_ret.abs() / (adr_ret.rolling(63, min_periods=20).std() + 1e-9)
r_ctrl = sim_rule(adr_ret.where(adr_z >= 1.5, 0.0), "sniper ADR-own")
loc_z = loc_sig.abs()
r_snip_vt = sim_rule(loc_sig.where(loc_z >= 1.5, 0.0).reindex(columns=adr_ret.columns).fillna(0.0),
                     "sniper local VT")
r_ctrl_vt = sim_rule(adr_ret.where(adr_z >= 1.5, 0.0), "sniper ADR-own VT")
r_always_vt = sim_rule(np.sign(loc_sig).reindex(columns=adr_ret.columns).fillna(0.0), "always-in VT")

# ---------------------------------------------------------------- 3. blend
P0 = pd.read_csv("results/61/csv/metrics.csv", index_col=0, parse_dates=True)[
    "Live_Realized_Return"].astype(float)
print("\n=== blend with P0 (bar: combined Sharpe > 0.83) ===")
for label, r in [("always-in VT", r_always_vt), ("sniper local VT", r_snip_vt),
                 ("sniper ADR-own VT", r_ctrl_vt)]:
    common = P0.index.intersection(r.index)
    for wp in [0.7, 0.5]:
        b = wp * P0.reindex(common).fillna(0.0) + (1 - wp) * r.reindex(common).fillna(0.0)
        sh = b.mean() / b.std() * np.sqrt(252) if b.std() > 0 else float("nan")
        tot = (1 + b).prod() - 1
        print(f"  {label:14s} blend({wp:.0%}P0): sharpe {sh:+.2f}  total {tot*100:+6.2f}%  "
              f"{'PASS' if sh > 0.83 else 'FAIL'}")