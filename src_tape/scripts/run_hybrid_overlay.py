"""
HYBRID TEST: pair book (P0, untouched) + directional single-name sleeve on
ORTHOGONAL (non-pair) assets, regime-gated.

Hypothesis (complementarity by construction): the cointegration screen REJECTS
trending names, so the names left out of the pair book are the non-pair
trenders — a momentum sleeve on exactly that subset is complementary to the
pair book by construction.

Sleeve: 63d / 21d momentum, long top / short bottom quantile among ORTHOGONAL
names only, calm-regime gated (v3 vol-percentile states: 0 = calm), vol-scaled.
Orthogonal set = pool minus names that EVER traded in the P0 pair book
(static approximation of time-varying membership).

Evaluation: sleeve standalone (fresh-start gate), then blended with P0's live
returns — the pre-registered hybrid bars:
  combined Sharpe vs P0's 0.83, combined return, combined DD.
Honest expectation set in advance: any positive-expectancy sleeve raises
ABSOLUTE return but dilutes the 0.83 Sharpe; accept only if the sleeve passes
its own gate.
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.core.run_paths import run_file  # noqa: E402
from modules.execution.portfolio_engine import run_target_weights  # noqa: E402
from modules.regime import REGIMES  # noqa: E402
from modules.validation.gate import window_stats, format_stats  # noqa: E402

TARGET_VOL = 0.12

px = pd.read_csv("data/ILF_survivorship.csv", index_col=0, parse_dates=True)
px = px[~px.index.duplicated()].sort_index().ffill()
POOL = [c for c in px.columns if c != "ILF"]

# ---- orthogonal set: names never traded in the P0 pair book -----------------
tr = pd.read_csv(run_file(61, "csv", "trade_records.csv"))
pair_names = set(tr[~tr.Asset.str.startswith(("SYN", "ILF"))]["Asset"].unique())
REAL = [c for c in POOL if not c.startswith("SYN")]  # synthetics NEVER tradeable
ORTHO = [c for c in REAL if c not in pair_names]
print(f"pool={len(POOL)} real={len(REAL)} pair-active={len(pair_names)} "
      f"orthogonal-real={len(ORTHO)}: {ORTHO}")

# ---- causal regime states (v3 vol percentile) on the market reference -------
mret = px["ILF"].pct_change().fillna(0.0)
det = REGIMES["v3"](params={"regime_gate_window": 252, "regime_gate_pctile": 0.3,
                            "regime_disp_window": 21})
states = det.states(mret).fillna(0.0)          # 0 calm, 1 stressed
calm = (states == 0)

# ---- P0 live returns (baseline for blending) --------------------------------
p0 = pd.read_csv(run_file(61, "csv", "metrics.csv"), index_col=0, parse_dates=True)
p0_r = p0["Live_Realized_Return"].astype(float)


def sleeve_momentum(lookback: int, universe, warm: int = 130):
    W = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for i in range(warm, len(px), 5):
        d = px.index[i]
        if not bool(calm.iloc[i]):
            continue
        r = px[universe].iloc[i] / px[universe].iloc[max(0, i - lookback)] - 1.0
        r = r.dropna()
        if len(r) < 6:
            continue
        wv = px[universe].pct_change(fill_method=None).iloc[max(0, i-63):i].std().median()
        g = float(np.clip(TARGET_VOL / (wv * np.sqrt(252)) if wv and wv > 0 else 0.5,
                          0.3, 1.2))
        sv = r.sort_values()
        k = max(1, int(len(sv) * 0.25))
        row = pd.Series(0.0, index=px.columns)
        for c in sv.index[-k:]:  row[c] = (g/2)/k      # long winners
        for c in sv.index[:k]:   row[c] = -(g/2)/k     # short losers
        W.loc[d] = row.values
    W = W.ffill()
    exec_w = W.shift(1).fillna(0.0)
    sub = px
    res = run_target_weights(close=sub, open_=sub, weights=exec_w,
                             init_cash=1_000_000.0, exec_fee=0.00015,
                             daily_borrow_rate=(50.0/10000.0)/252.0)
    return res.value.pct_change().fillna(0.0).iloc[warm:]


def report(label, r):
    tot = (1+r).prod()-1
    sh = r.mean()/r.std()*np.sqrt(252) if r.std() > 0 else float("nan")
    dd = (1-(1+r).cumprod()/((1+r).cumprod().cummax())).max()
    print(f"  {label:28s} total {tot*100:+7.2f}%  sharpe {sh:+.2f}  dd {dd*100:5.1f}%  vol {r.std()*np.sqrt(252)*100:.1f}%")
    # fresh gate
    starts = ["2018-10", "2020-03", "2021-09", "2023-03", "2024-06"]
    rets = []
    for s in starts:
        pos = int(px.index.searchsorted(pd.Timestamp(s)))
        if pos >= len(px) - 60:
            continue
        sub_r = r.loc[r.index >= pd.Timestamp(s)]
        sub_r = sub_r.iloc[:504]
        rets.append(float((1+sub_r).prod()-1))
    pos_ct = sum(1 for x in rets if x > 0)
    print(f"  {'':28s} fresh: {[round(x*100,1) for x in rets]}  pos={pos_ct}/{len(rets)}")
    common = p0_r.index.intersection(r.index)
    b = 0.5*p0_r.reindex(common).fillna(0.0) + 0.5*r.reindex(common).fillna(0.0)
    bsh = b.mean()/b.std()*np.sqrt(252) if b.std() > 0 else float("nan")
    corr = p0_r.reindex(common).corr(r.reindex(common))
    print(f"  {'':28s} blend(50/50): sharpe {bsh:+.2f} (P0=0.83)  corr={corr:+.2f}  "
          f"blend_total {(1+b).prod()*100-100:+.1f}%")
    return r


print("\n=== hybrid: orthogonal-asset momentum sleeves (calm-gated) ===")
r63 = report("orthogonal 63d momentum", sleeve_momentum(63, ORTHO))
r21 = report("orthogonal 21d momentum", sleeve_momentum(21, ORTHO))

# ---- control: same sleeve on the FULL pool (subset effect test) -------------
print("\n=== control: same sleeves on the FULL pool (is the subset special?) ===")
r63_full = report("full-pool 63d momentum", sleeve_momentum(63, REAL))

# ---- pair-book regime scaling (analytic on P0 returns) ----------------------
print("\n=== pair-book regime scaling (analytic on P0 returns) ===")
for scale in [0.5, 0.75, 1.25]:
    scaled = p0_r * np.where(calm.reindex(p0_r.index).fillna(True), 1.0, scale)
    sh = scaled.mean()/scaled.std()*np.sqrt(252)
    tot = (1+scaled).prod()-1
    print(f"  stressed-scale {scale:.2f}: total {tot*100:+.2f}%  sharpe {sh:+.2f}  (P0: +4.09% / 0.83)")

print("\n=== capital-weighted hybrid books (pair book + orthogonal momentum) ===")
def combined(label, r_sleeve, w_pair, w_sleeve):
    common = p0_r.index.intersection(r_sleeve.index)
    a = p0_r.reindex(common).fillna(0.0); b_ = r_sleeve.reindex(common).fillna(0.0)
    r = w_pair*a + w_sleeve*b_
    sh = r.mean()/r.std()*np.sqrt(252) if r.std()>0 else float("nan")
    tot = (1+r).prod()-1
    eq = (1+r).cumprod()
    dd = float((1-eq/eq.cummax()).max())
    print(f"  {label:34s} capital pair {w_pair:.0%} / sleeve {w_sleeve:.0%}: "
          f"total {tot*100:+7.2f}%  sharpe {sh:+.2f}  dd {dd*100:.1f}%")
combined("P0 + orth-63d (50/50)", r63, 0.5, 0.5)
combined("P0 + orth-63d (70/30)", r63, 0.7, 0.3)
combined("P0 + orth-63d (30/70)", r63, 0.3, 0.7)
combined("P0 + orth-21d (50/50)", r21, 0.5, 0.5)
