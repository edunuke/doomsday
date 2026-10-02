# RESULTS.md — Deployable Strategies & Operational Ledger

Final results of the systematic research program on the ILF (LatAm-40) universe,
2016-2026 daily bars, $1M notional, costs 1.5bps/side + 50bps short borrow,
idle cash credited at SOFR (`cash_yield_idle: true`). All runs reproducible:
`uv run python main.py --config <config>` from `src_tape/` (interpreter pinned
via `pyproject.toml`/`uv.lock`, Python 3.11.5 / numpy 1.26.3 / scipy 1.11.4 /
pandas 2.1.4).

---

## 1. Deployable candidates (best of each family, all gates applied)

| # | Strategy (config) | Mechanism | Activation | Size / hold | Measured performance (8.1y, live window) | Return above SOFR | Max DD | Margin requirement | Borrow | ADV / liquidity | Ops complexity | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **Quiet-dislocation reversion, unhedged + T-bill base** `config_ilf_p0_cash.json` (run 68) | Sector-matched cointegration pairs (EG + Johansen screen, Kalman β); entry on z + ECDF ≥ 0.8 + quiet-relvol gate (mover ≤ 1.0× ADV); exit on z ≤ 0.65 / ±2.2σ stop / max-hold | **Episodic: 11 activations in 8.1y** (~1/yr; COVID 2020, 2022-11 election, 2024-08 carry unwind, …) | 2 legs @ ~23% notional each (46% gross), held 1-3 days, hard ±2.2σ stop | **+29.2% total; +44bps/yr above SOFR; 9/11 episodes positive; worst episode −0.06% of equity** | **+44bps/yr** | **0.40%** | 1×: cash-funded, no margin loan; Reg-T sufficient | Short leg only, during episodes, mega-cap ADRs (VALE/PBR/EC class), 1-3 days | ~1-2% of ADV at 1×; fine | Low: PB account + borrow desk + T-bill sleeve | **Deployable — config of record** |
| 1a | #1 with **episode leverage 3×** (same config, scale episode notional ×3) | identical; notional funded by idle T-bills + margin loan | same | gross 138% episodic | mean episode +1.1% equity; realized worst episode −0.2% | **~+1.3pp/yr** | ~1.2% observed; **gap tail −4.5%/leg-day** | **Portfolio margin required** (stress-based ~15-25% of gross → 28-42% of equity); Reg-T caps at ~2× | Short notional ~70% of equity for 1-3 days; LatAm ADR borrow 100-500bps (robustness verified to 1000bps at this turnover — cost impact <$15/episode) | 1-3% of ADV on the smaller legs | Moderate: PM account, crisis-slippage reserve (10-30bps/side, **not modeled**), gap-risk budget | **Deployable with PM account** |
| 1b | #1 with episode leverage 5× | identical | same | gross 230% episodic | mean episode +1.9% | ~+2.2pp/yr | observed ~0.3%; **gap tail −7.4%/leg-day** | PM ~46-69% of equity | Short ~115% of equity episodic; recall risk concentrated in crises | 3-5% of ADV on smaller legs — at cap | Moderate-high | **Conditional — cap by thinnest leg's ADV** |
| 1c | #1 at 10× | — | — | gross 460% | — | ~+4.4pp/yr | single-leg gap −15%+ of equity | PM stressed | Recall + failure-to-deliver risk in crises | >5% ADV — breach | High | **Not recommended** — stop-loss does not protect against overnight gaps (2020-03: B3SA3 −16.6% in one day) |
| 2 | **Wide-universe pair book** `config_lw_p0.json` (run 70; 41 orig + 29 new LatAm names) | same engine, broader cross-section (`min_history_fraction: 0.6`) | ~2× activation frequency | 95 legs/8.1y | +28.9% total incl. cash yield; Sharpe 1.71 (cash-inclusive); **but new-name legs −$2,738/leg (drag), orig legs +$2,460/leg** | +0.32pp/yr | 2.81% | Same as #1 at 1× | Same | Same | + country/factor data maintenance | **Not recommended as-is** — edge is relationship-specific; naive breadth dilutes |
| 3 | **Layered book: #1 core (70%) + loud-trend rider satellite (30%)** (`scripts/run_layered_snipers.py`, research layer) | rider: continuation entry on ≥5% 5d move with relvol ≥ 1.2, trailing stop 3×20d-vol, 30d time stop | rider fires on loud names, continuous | 30% of capital, holds 10-30d | blend Sharpe **0.91-0.94** vs core 0.83 (ex-cash-yield basis); +10.5% vs +4.1% total | rider adds ~+8pp total | blend DD ~5-6% | same as #1 + rider longs (no borrow needed on longs) | rider shorts: episodic | fine | Moderate (two-layer attribution via tape) | **Conditional** — rider edge is 2022+-concentrated (pre-2022 ≈ 0, fresh gate 3/5 FAIL); committee sign-off on the regime caveat required |

## 2. Leverage ladder for candidate #1 (the only leverageable profile)

| Episode leverage | Gross/equity (episodic) | Above SOFR/yr | Worst observed episode | Worst single-leg gap (assumed −20% of leg notional) | Margin type | Notes |
|---|---|---|---|---|---|---|
| 1× | 46% | +44bps | −0.06% | −4.6% of equity | Reg-T | As tested |
| 3× | 138% | ~+1.3pp | −0.2% | −13.8% | Portfolio margin | **Recommended operating point** |
| 5× | 230% | ~+2.2pp | −0.3% | −23% | Portfolio margin | ADV-capped on smaller legs |
| 10× | 460% | ~+4.4pp | −0.6% | −46% | PM stressed | **Not recommended** |

Risk structure that makes this leverageable: the ±2.2σ stop bounds each
episode's loss (realized worst −0.06% of equity), the exposure is episodic
(1-3 days) rather than continuous, and idle capital earns SOFR throughout.
The unmodeled tail: overnight gaps through the stop (one 2020-03 leg moved
−16.6% of notional in a single day) — hence the leverage cap and the
crisis-slippage reserve.

## 3. Conditional / research-status items (not deployable as-is)

| Item | Measured | Blocker |
|---|---|---|
| Loud-trend rider as standalone | +28.5%, Sharpe 0.69, DD 14% | Fresh gate FAIL (3/5, but med +8.2% passes) — **all edge is 2022+** (pre-2022 Sharpe 0.04); one-regime concentration |
| Wide pair book with per-name pruning | orig legs +$2,460/leg, new legs −$2,738/leg | Pruning = in-sample selection; needs an out-of-window validation design |
| ADR-local lead-lag (daily bars) | local signal beats ADR-own control 4.4× (+161% vs +36% same construction) | DD 57% as constructed; blend Sharpe 0.63-0.65 < 0.83; correct implementation is intraday (B3 close 15:00 EST → ADR 15:00-16:00), out of scope |

## 4. Falsified families (do not revisit on this dataset)

| Family | Evidence |
|---|---|
| TSMOM, momentum direction (3/6/12m ensemble, trailing stops) | −54 to −69%, DD 71-76%, fresh 1/5 |
| TSMOM, anti direction | gate FAIL all 4 variants, DD 47-50%, profit = one 2022 window |
| Both families on the expanded panel | −86 to −91%, DD ~90%, fresh 0/5 (Argentina/whipsaw names amplify) |
| Cross-sectional momentum (63d), both universes | −29 to −37%, fresh FAIL |
| 5d loud momentum (rank construction) | ILF: −4.7%; LW: +40.7% but fresh FAIL (−26.8% window) |
| Sector-dispersion reversal (stockalpha) | −19.1%, fresh 1/5 |
| PBR/PBR-A dual-share basis | −9.8% (spread trends, not convergent) |
| FDR + persistence screening | removes the profitable pairs (per-leg $944 → $478) |
| Regime gates / trend stops / vol targeting on directional sleeves | all whipsaw-negative (−$8k to −$107k) |
| Macro-hedge constraints in the pair book | infeasible for thin books; risk-overlay only (PF 1.51, −12bps/yr) |
| Lead-lag weight modulation | **anti-placebo won** (flipped +3.68pp vs true +3.48pp) — fabricated |
| Index-level timing (4 gate families) | all negative |
| Naive breadth expansion | new-name legs −$2,738/leg; edge is relationship-specific |

## 5. Operational prerequisites (candidate #1)

- **Account**: prime brokerage with portfolio margin (needed only at ≥3× episode
  leverage); $1M equity held in T-bills/money-market as collateral (yield
  retained via repo pledge or liquidation-on-signal).
- **Borrow**: pre-arranged LatAm ADR borrow line with recall protection agreed
  *before* crisis windows; shorts are 1-3 day holds on mega-caps only.
- **Execution**: T+1 MOO (modeled). Crisis opens gap — budget 10-30bps/side
  slippage (not in the backtest) and re-validate the 44bps edge net of it.
- **Monitoring**: per-episode ledger (episode = one activation, 2 legs);
  kill-switch after 2 consecutive losing episodes; per-leg gap assumption
  −20% of notional for the risk budget; cohort tracking (both cohorts must
  stay non-negative).
- **Capacity**: measured at $1M with ~23% per-leg notional and 1.5% ADV
  participation; scale-up bounded by ADV on the smaller legs and by episode
  frequency (3/yr), not by the signal.

## 6. Reproducibility & governance

- Every configuration above is a JSON file in `configs/`; every run lands in
  `results/<N>/` with `run_environment.json` + `requirements.txt` (interpreter
  and package pins), config hash, and code-tree hash.
- Environment: uv-managed, locked (`uv.lock`, `.python-version` = 3.11.5).
  Results are reproducible **within** an interpreter; archived results
  reproduce exactly under the 3.11.5 stack (verified).
- Survivorship synthetics are bias-compensation only and are excluded from
  every tradeable sleeve (`exclude_synth_from_pairs: true`) — three fabricated
  results were caught by this rule during the program.
- The falsification ledger (§4) is part of the record: ~95 runs, every family
  tested with anti-placebo controls and pre-registered gates, on both the
  original and the expanded cross-section.
