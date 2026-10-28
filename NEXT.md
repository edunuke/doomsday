# NEXT.md — diagnosis of run 68 and the next experiment

Basis: `src_tape/results/68` (the "config of record"), `MIX_REPORT.md`,
`csv/trade_records.csv`, `tape.txt`, `csv/metrics.csv`, `logs.txt`, `config.json`.
Goal: hold positions long enough for the edge to exist, size them for risk, and
raise the trade count — without re-tuning z thresholds on 24 legs.

---

## 1. TL;DR

Run 68 is not an episodic risk-managed strategy. It is a strategy that gets
liquidated one session after every entry by an allocator that is mathematically
infeasible whenever no pair has positive modelled alpha. 99.4% of rebalances
end flat, so the reported Sharpe 5.59 / DD 0.40% / +44bps-a-year are properties
of an almost-never-invested book, not of risk control.

Three defects compound:

1. **Entries are priced at the exit threshold.** `funnel_mode=simple` removed
   the `entry_z_base` / ECDF / alpha floors, so pairs are promoted on rank only.
   Median entry |z| is 0.76 against `exit_z_spread=0.65` — expected reversion
   edge at entry is ≈0 and the model's own hold time is ≈0.
2. **The allocator cannot hold what it entered.** Sleeve upper bounds are
   `max_sleeve * clip(alpha/median, 0.2, 1)`, and `alpha` is exactly 0 whenever
   `|z| <= exit_z`. Once the spread ticks back to 0.65 every sleeve bound is
   zero-width, `min_gross` becomes infeasible, SLSQP fails "all independent
   variables were fixed by bounds", the penalised fallback returns an all-zero
   book, and `min_weight_cutoff` cements it.
3. **A size failure is treated as a signal exit.** `walk_forward.py:1165-1178`
   de-allocates every pair missing from `allocs` and applies the full
   `quarantine_days=30`. One allocator glitch benches the pair for six weeks,
   which is why the whole programme produced 12 entries in 8.1 years.

Fix order: hold-and-size first (mechanical, no signal change), then entry
quality, then frequency, then costs. Leverage is the last thing to touch.

---

## 2. Evidence (run 68, measured)

| Observation | Value | Source |
|---|---|---|
| Rebalances with gross > 0 | **0.6%** (13 of 2,105); mean gross 0.3%, p95 0.0% | `csv/metrics.csv` |
| `Live_Size` > 0 | 0.6% of days, max 2 pairs | `csv/metrics.csv` |
| Pair entries / legs / overlay legs | 12 / 24 / 12 | `csv/mix_report.csv` |
| Holding period | 1 session for 31/36 legs; 2 sessions for the 2020-03 B3SA3/ITSA4 episode (3-day rows are weekend spans) | `trade_records.csv` |
| Tape `EXIT` events | **0** | `tape.txt` |
| Exit reason | 24/24 pair legs `DROPPED_BY_ALLOCATOR` | `MIX_REPORT.md` |
| Exit-rebalance allocator state | hard SLSQP failed on **12/12** exits; violation == `min_gross` exactly (0.6 / 0.675 / 0.9 / 1.125) | `logs.txt`, `MIX_REPORT.md` |
| Entry `\|z\|` distribution | 0.65, 0.66, 0.67, 0.68, 0.75, 0.76, 0.78, 0.87, 0.88, 0.95, 0.97, 1.14 | `tape.txt` |
| Episodes positive | 9 of 12; worst episode −0.33% of equity | derived |
| Total return vs benchmark | +29.22% vs +25.63% over 8.1y (~0.4pp/yr) | `logs.txt` |
| PnL concentration | top 3 legs = +36,977 of +43,900 pair PnL; overlay −14,637 | `MIX_REPORT.md` |

`violation == min_gross` is the decisive arithmetic: with `lb=0` and non-zero
upper bounds the hard solve could not violate the gross floor by exactly its
full value. That equality requires `ub=0` for every sleeve, i.e. every
`Parametric_Alpha <= 1e-6` on the exit date — including the pair entered the
day before. That is the signature of entries sitting at the exit threshold.

---

## 3. What RESULTS.md currently overstates (must be corrected)

| RESULTS.md claim | Measured in run 68 |
|---|---|
| "exit on z ≤ 0.65 / ±2.2σ stop / max-hold" | No exit rule ever fired; 0 `EXIT` events. Positions were de-allocated by the optimizer. |
| "held 1–3 days" | 1 session for 31/36 legs, 2 sessions for one episode; nothing was held to a reversion. |
| "entry on z + ECDF ≥ 0.8 + quiet-relvol gate" | `funnel_mode=simple` makes `entry_ecdf_percentile`, `entry_z_base` and `min_override_z` **inert**. Only VolGate and a top-20 alpha rank apply. |
| "9/11 episodes positive; worst episode −0.06%" | 9/12 positive; worst episode −0.33% of equity. |
| Sharpe 5.59, DD 0.40%, deployable at 3×/5× | Computed over 2,946 daily returns that are ~99% exact zeros. Levering this scales a 1-day mark-to-market coin flip, not an edge. |

Until these are restated, the leverage ladder in RESULTS.md §2 is scaling an
artifact. Correction is a prerequisite for any capital decision.

---

## 4. Root-cause chain (with code touchpoints)

```
funnel_mode=simple                    walk_forward.py:751-760
  └─ promotion = top-20 by alpha, no floor  → entries at |z| ≈ exit_z
       └─ alpha = max(0,|z|-exit_z)·σ·(252/HL)·(1-e^(-HL/τ))  walk_forward.py:421-429
            └─ alpha = 0 for every sleeve next day
                 └─ allocator ub = 0 for all sleeves          factor_allocator.py:145-159
                      └─ sum(w)=0 < min_gross → SLSQP infeasible   factor_allocator.py:160-193
                           └─ penalised fallback → w=0 → cutoff   factor_allocator.py:197
                                └─ active_pairs - allocs = everything  walk_forward.py:1165-1178
                                     └─ 30-day quarantine on a size failure
                                          └─ 12 entries / 8.1y, 1-day holds
```

Two independent design errors are visible here:

- **Scoring and sizing share one variable.** `alpha` is both "is this worth
  entering" and "how big is this allowed to be". When alpha decays to zero the
  position is closed, which converts a mean-reversion signal's natural decay
  into a forced exit. A position must be closed by its exit rule, not by the
  entry score.
- **Thin-book infeasibility is not handled.** `min_gross` = 0.5 × target gross
  with factor-neutrality tolerances is unsatisfiable when only 2–5 sleeves have
  non-zero bounds. The fallback returns zero rather than the best feasible book,
  and the caller reads zero as "close everything".

---

## 5. Experiment: run 69 (`config_ilf_p1_hold.json`)

One change set, pre-registered, from the run 68 config. No z-threshold tuning
in this step.

### E0 — instrumentation only (no behaviour change)

- Log per sleeve at every rebalance: `alpha`, `ub`, `w_hard`, `w_fallback`,
  `w_final`, and the constraint that binds.
- Emit `ALLOC_INFEASIBLE` gate events, and `EXIT` events with
  `reason=ALLOC_DROP` instead of silently zeroing.
- Fail the run (non-zero exit) if >50% of exits are `ALLOC_DROP`.
- Add benchmark to `cumulative_performance.png`; report Sharpe on invested days
  alongside the calendar-day Sharpe.

### E1 — decouple holding from allocation (the main fix)

- Active sleeves are **carried at their last weight** when the allocator cannot
  re-size them (`alloc_carry_active: true`); the optimizer sizes entries only.
- `min_gross_floor: 0.0`, `min_gross_frac: 0.0`; if the solve is infeasible,
  keep the prior book rather than flatten.
- `alpha_bound_floor: 0.35` (upper bound never collapses below 35% of the
  sleeve cap while a position is held).
- Quarantine only on real exits: `STOP_LOSS` 30d escalating, `REVERTED` 2d,
  `MAX_HOLD` 0d, `ALLOC_DROP` 0d.
- Exit rule becomes the only thing that closes a pair: `|z| <= 0.65`,
  `|z| >= 2.2`, `days >= max(2, OU-implied hold)`.

Expected: hold distribution 2–10 sessions, invested-days 30–60%, exits mostly
`REVERTED`. This is a mechanical hypothesis; if trades still die in one session,
E1 failed and the diagnosis is wrong — say so and stop.

### E2 — entry floor (3-point sweep, pre-registered)

`simple_min_abs_z` ∈ {0.75, 1.00, 1.25} with `exit_z_spread` fixed at 0.65, so
the entry-to-exit gap is 0.10 / 0.35 / 0.60 sigma. Pick by net per-leg edge at
300bps borrow, not by total return. This is the economic content the simple
funnel lost.

### E3 — risk-based sizing (replaces one 45% sleeve)

- `max_sleeve_weight: 0.12`, `max_sleeve_weight_low_density: 0.20`,
  `low_density_threshold: 20` (so caps stop scaling with sleeve count).
- Inverse-volatility sleeve scaling (already in `init`), cap 6–10 concurrent
  sleeves, `max_gross_exposure: 1.0`, book vol target 8% annualised.
- Report per-sleeve risk contribution; reject any sleeve >25% of book variance.

Expected: same or lower total return per episode, materially lower book
variance and a defensible "risk-adjusted" claim.

### E4 — cost and turnover (enable what already exists)

- `ev_gate_enabled: true`, sweep `ev_gate_mult` ∈ {1.0, 1.5, 2.0}.
- `volume_weight_mode: continuous`, `vol_w_floor: 0.5`.
- Re-run the winning E2/E3 config at `borrow_bps` 50 / 300 / 1000.

Expected: with 10× the trades, fees and borrow decide the result. Any variant
that only clears at 50bps is not a strategy — that is the PLAN.md Tier-1 test.

### E5 — breadth, only after E1–E3 pass

Lower `screening_freq_days` 20 → 10 and add FDR control on the screen
(PLAN.md Tier 1 #3). Do **not** widen the universe first: the current book
cannot hold its own positions, so breadth would only multiply a broken loop.

---

## 6. Success criteria (pre-registered, run 69 vs run 68)

| Metric | Run 68 | Target |
|---|---|---|
| Invested-day share | 0.6% | ≥ 40% |
| Pair legs over the window | 24 | ≥ 100 |
| Median holding (sessions) | 1 | 3–10 |
| `ALLOC_DROP` share of exits | 100% | ≤ 10% |
| `REVERTED` share of exits | 0% | ≥ 50% |
| Net per-leg edge @300bps borrow | n/a (1-day noise) | > 0, ≥ $1.5k/leg at 1× |
| Sharpe on invested days | n/a (zeros dominate) | ≥ 1.0 |
| Max drawdown | 0.40% (flat book) | ≤ 6% |
| Factor-regression alpha | not measured | t-stat > 2 vs ILF/market |
| Return net of SOFR at 1× gross | ~+0.4pp/yr | ≥ +2pp/yr |

Kill criteria: if E1 alone does not produce multi-session holds, the allocator
is not the binding constraint — stop and re-instrument rather than stack E2–E4.
If E2 shows per-leg edge falling as the entry floor rises, the pair engine has
no economically exploitable dislocation at this cadence; escalate to PLAN.md
Tier 2 (sector-dispersion / hedged dislocation) instead of adding leverage.

---

## 7. Guardrails

- Do not raise leverage on run 68's P&L. 3× would multiply the allocator
  artifact and the −16.6% single-leg 2020 gap, not an edge.
- No threshold tuning beyond the pre-registered E2 sweep and the EV multiplier;
  PLAN.md documents that responses are jagged at 24 legs.
- Every variant keeps a negative control: the E2 floor sweep must show edge
  monotone in the entry-to-exit gap, not a single lucky cell.
- Report both cohorts, days-invested, gross-time, turnover and per-leg net edge
  in every results table; the calendar-day Sharpe alone is not admissible.
- Correct RESULTS.md §1/§2 wording before the next committee read (see §3).

---

## 8. Deliverables

1. `configs/config_ilf_p1_hold.json` (E1), plus `_z100`, `_z125` (E2) and the
   sizing variants (E3).
2. Runs 69–7x under `results/`, each with `MIX_REPORT.md` and the corrected
   metric block; the mix report already separates promotion vs exit reasons, so
   `ALLOC_DROP` vs `REVERTED` becomes the primary diagnostic.
3. A one-page delta vs run 68: trades, invested days, holds, exit mix, net
   per-leg edge at 300bps borrow.
