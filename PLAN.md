## Where we are — honest progress analysis

Two universes tested end-to-end, and the picture is consistent: **there is a small, real, cost-sensitive edge in LatAm dislocations; it is underpowered at 41 names and currently sub-cash after fees.**

| Finding | Evidence | Implication |
|---|---|---|
| Synthetic legs fabricated all the apparent alpha | r40 +11.3% vs r41 (SYN excluded) −3.5%, PF 0.98, DD 22.5% / 1564d | Harness now honest; real book is negative at base config |
| Config transfer was a real constraint | exit_z 0.2→0.5/0.65, minhold 2→5: −3.5% → +4.4%, Sharpe 0.44; fees 61% of gross → 11% | The edge exists but fees/turnover govern it |
| Edge is *entry-quality* selection | negative volume gate: per-leg $1,052 → $2,030, Sharpe 0.44→0.68, DD 1.5%→0.46%, both cohorts + | High-volume (news/macro-shock) dislocations trend and lose; quiet ones revert |
| Factor residualisation *removes* the alpha | v3: per-leg $102 (−90%) | The factor content IS the tradable dislocation, not noise. Hedge it as risk, don't strip it from the signal |
| 7-layer funnel is S&P-calibrated and non-viable here | inert at α-floor 0.35/Hurst 0.43; −6.8% to −9.0% when calibrated; thin-book overlay −$102k | Use 3-layer `simple`; don't re-tune the 7-layer for LatAm |
| Parameter response is jagged | exit_z U-shape; thresholds 0.95→1.00→1.05 non-monotone | Any further threshold tuning on 24-44 legs is overfitting |
| Scale | best ILF: 24 pair-legs / 8.1y, ~0.4%/yr | **Breadth is the binding constraint**, not signal sophistication |

The S&P side is not better: r38's "best" config is −0.72% over 3y with 1,526 trades — knife-edge negative and fee-dominated. So "improve the signal" has to be answered at the level of *economics*, not just the z-score.

## What the current signal actually is — and its weaknesses

`spread = log A − β·log B` (Kalman β, static-ish), `z = (spread − μ)/σ`, entry on |z| past a percentile gate, exit on |z| ≤ threshold or OU-implied max hold, quality gates on Hurst/half-life/slope. Weaknesses revealed by the tests:

1. **Symmetric, cost-blind entry/exit.** We enter on a fixed z and exit on a fixed z; nothing conditions on whether the expected reversion covers round-trip fees + borrow + slippage. The fee ledger says this is the dominant leak.
2. **No dislocation-cause conditioning.** The volume test proved the single most informative discriminator we've found is unused by the core model.
3. **Spurious-pair risk.** 24 candidates → 0-4 pass per screen; we screen ~100+ pairs per retrain with no multiple-testing control, then trade survivors — a classic in-sample-selection trap that shows up as OOS failure.
4. **Static linear cointegration in a macro-driven universe.** LatAm relationships shift with FX/policy; β drift and regime shifts produce "dislocations" that never revert.
5. **No cross-sectional use of the panel.** Pair-by-pair with ~40 names is the least diversified way to express sector-relative value.

## Levers, ranked by expected value (and my priors)

**Tier 1 — economics and selection (days, no new data, low overfit risk)**
1. **Cost-aware EV gate** replacing fixed entry z: enter iff `E[reversion | z_entry] − round-trip fee − expected borrow·holding > k·friction`. The `friction_clearance_multiplier` hook already exists but is dead under `simple`; make it live. Directly attacks the 61% fee share. High prior, cheap.
2. **Continuous, not binary, volume conditioning**: size ∝ 1/relvol or gate at the calibrated cut; add a reversal-vs-momentum discriminator (sign of 1-5d residual vs sector, overnight gap). Medium-high prior, data in hand.
3. **Multiple-testing control + persistence screen**: FDR on cointegration p-values; require ≥N historical mean-crossings; rolling-ADF stability. Attacks the "pairs that pass in-sample and die OOS" problem. High prior, low cost.
4. **Borrow realism as a first-class input**: rerun tier-1 variants at 300/1000bps LatAm borrow; any edge that only exists at 50bps is not an edge.

**Tier 2 — signal construction (weeks)**
5. **Hedged-dislocation construction**: keep macro dislocation as the *trigger*, but build the traded spread with explicit FX/commodity offset legs, so the bet is idio and the signal keeps its macro content (the middle ground between v2 and the failed v3 residualisation).
6. **Sector-relative dispersion sleeve**: within each GICS sector, rank by residual z, long bottom-k / short top-k, sector-neutral. Uses all names, far fewer parameters, and is the natural fit for a 40-name universe — likely a better *factory* than pair hunting here.
7. **Robust cointegration**: Johansen rank tests, DOLS/FMOLS, regime-switching β — replace Engle-Granger fragility.

**Tier 3 — structural/mechanical edges (highest confidence, new data)**
8. **ADR/local basis** (PBR vs PBR/A is already in the universe; add local B3/MX listings): mechanical convergence, doesn't need the cointegration screen.
9. **Index-rebalance flows** (ILF semi-annual): known, dated dislocations; event overlay to avoid trading into flows or to fade post-flow overshoot.

**Tier 4 — the multiplier**
10. **Universe breadth to 150-200 LatAm names.** At $0.7-2.8k per leg, 24 legs/8y is a curiosity; 300-600 legs/yr on the same machinery and realistic costs is a strategy. Everything above gets re-measured on the expanded panel — and yes, per the harness this is now safe (`market_ticker`, `universe`-aware survivorship, distinct panels).

## How I'd validate (pre-registered, so we don't fool ourselves)

- **Primary metric**: per-leg *net* edge (realistic borrow), at similar-or-lower turnover; secondary: both-cohort Sharpe; tertiary: deflated Sharpe / PBO across the sweep family.
- **Placebo controls**: the v4a complement (high-volume half) is the template — every signal addition needs a negative control that must *not* work.
- **Walk-forward + block bootstrap**; nested CV for any tuned parameter; treat the 24-leg sample as evidence about *direction*, never about magnitude.
- **Capacity curve**: Sharpe vs AUM under ADV participation caps.

## Discussion — the strategic call

The temptation is to keep tuning z/thresholds on LatAm. The evidence says that's the wrong move: responses are jagged, the sample is tiny, and the binding constraints are *breadth* and *cost*. So the right sequence is: (1) make the signal economics correct (cost-aware gate) and the selection statistically honest (FDR/persistence) on the current universe — these are cheap and their effect should show as a higher per-leg edge at lower turnover; (2) simultaneously build the *hedged-dislocation* and *sector-dispersion* alternatives, because the pair engine may simply be the wrong instrument for 40 names; (3) expand breadth, which is where the economics actually clear fees; (4) add the mechanical sleeves (ADR basis, rebalance) which don't depend on the cointegration machinery at all.

What I would explicitly **not** do: add more technical indicators (no structural prior for cointegration edge, more turnover), re-residualise against FX/commodities (proven to remove the alpha), or further tune the 7-layer funnel for LatAm (S&P architecture, negative at every calibration).

If you want to proceed, the concrete next experiment set is Tier-1 only, all config-gated and ready in the harness:
- `config_ilf_v8_evgate`: cost-aware EV entry gate (k sweep), on v4c;
- `config_ilf_v9_volweight`: continuous volume weighting + reversal discriminator;
- `config_ilf_v10_fdr`: FDR-corrected screen + persistence filter;
- re-run v4c and each variant at 300/1000bps borrow.

That gives a clean read: does honest cost-awareness raise net per-leg edge, or does the edge only exist under optimistic frictions — which decides whether Tier 2/4 investment is warranted.