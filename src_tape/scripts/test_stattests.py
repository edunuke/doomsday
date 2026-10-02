"""
VALIDATE the registered stat tests (modules/stattests) on synthetic ground
truth and on real pair spreads.

Ground truth:
  mean-reverting OU : H<0.5, finite half-life, VR<1, (pair) Johansen coint
  random walk       : H~0.5, half-life ~ inf, VR~1
  trending          : H>0.5
  independent pair  : Johansen NOT coint
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.stattests import (hurst_exponent, half_life_ar1, variance_ratio,
                               johansen_test, STAT_TESTS)  # noqa: E402

rng = np.random.default_rng(7)
N = 2000
res = []
def check(name, cond, **kw):
    res.append((name, "PASS" if cond else "FAIL", kw)); 
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {kw}")

# --- synthetic series ---
def ou(n, kappa=0.05, sig=1.0):
    s = np.zeros(n); 
    for i in range(1, n): s[i] = s[i-1]*(1-kappa) + rng.normal(0, sig)
    return s
rw = np.cumsum(rng.normal(0, 1, N))
inc = np.zeros(N)
for i in range(1, N): inc[i] = 0.7*inc[i-1] + rng.normal(0, 1.0)
trend = np.cumsum(inc)   # persistent (long-memory) increments -> H>0.5
mrv = ou(N, 0.05, 1.0)

print("=== univariate tests ===")
h_mrv = hurst_exponent(mrv); h_rw = hurst_exponent(rw); h_tr = hurst_exponent(trend)
check("hurst mean-reverting <0.5", h_mrv < 0.5, H=round(h_mrv,3))
check("hurst random-walk ~0.5", abs(h_rw-0.5) < 0.1, H=round(h_rw,3))
check("hurst trending >0.5", h_tr > 0.5, H=round(h_tr,3))
check("hurst persistent >0.5", hurst_exponent(trend, max_lag=40) > 0.5, H=round(hurst_exponent(trend, max_lag=40),3))
hl_mrv = half_life_ar1(mrv); hl_rw = half_life_ar1(rw)
check("half-life MRV finite", np.isfinite(hl_mrv), hl=round(float(hl_mrv),1))
check("half-life RW long (>50)", (not np.isfinite(hl_rw)) or hl_rw > 50, hl=round(float(hl_rw),1))
vr_mrv = variance_ratio(mrv,5); vr_rw = variance_ratio(rw,5)
check("VR MRV <1", vr_mrv < 1.0, VR=round(vr_mrv,3))
check("VR RW ~1", abs(vr_rw-1.0) < 0.2, VR=round(vr_rw,3))

print("=== johansen ===")
base = np.cumsum(rng.normal(0,1,N))
coint_pair = base + rng.normal(0,0.3,N)          # cointegrated with base
indep = np.cumsum(rng.normal(0,1,N))
j_c = johansen_test(base, coint_pair); j_i = johansen_test(base, indep)
check("johansen cointegrated pair detected", j_c.get("trace_coint_95") is True,
      trace=round(j_c.get("trace_stat",0),1), cv95=round(j_c.get("trace_cv95",0),1))
check("johansen independent pair NOT coint", j_i.get("trace_coint_95") is False,
      trace=round(j_i.get("trace_stat",0),1), cv95=round(j_i.get("trace_cv95",0),1))

print("=== real spreads (10y) ===")
px = pd.read_csv("data/universe_daily_10y.csv", index_col=0, parse_dates=True).ffill()
cols = [c for c in px.columns if c != "SPY"]
pair_specs = [("twin-sector", cols[0], cols[1])]
# a known-looking pair: two large caps from same sector via constituents
try:
    con = pd.read_csv("data/constituents.csv")
    sym = "Symbol" if "Symbol" in con.columns else con.columns[0]
    sec = con.set_index(sym)["GICS Sector"]
    s0 = sec.dropna().index[0]; grp = [c for c in sec[sec == sec[s0]].index if c in px.columns][:2]
    if len(grp) == 2: pair_specs.append((f"same-sector {sec[s0]}", grp[0], grp[1]))
except Exception as e:
    print("  (constituents lookup skipped:", e, ")")
for lbl, a, b in pair_specs:
    w = px[[a,b]].iloc[-504:]
    lsp = np.log(w[a]) - np.log(w[b])
    j = johansen_test(lsp.values, w[b].values)  # any 2 series; report on price pair too
    print(f"  {lbl}: {a}/{b} H={hurst_exponent(lsp.values):.3f} "
          f"HL={half_life_ar1(lsp.values):.1f} VR={variance_ratio(lsp.values,5):.3f} "
          f"joh_trace={j.get('trace_stat'):.1f} coint95={j.get('trace_coint_95')}")

npass = sum(1 for _,s,_ in res if s=="PASS")
print(f"\nSUMMARY: {npass}/{len(res)} PASS")
print("registered tests:", list(STAT_TESTS.keys()))
