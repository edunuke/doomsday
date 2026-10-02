# REFACTOR PLAN — Concern Separation via a Decision Tape

Status: **PLAN ONLY** (no code changes yet)
Scope: `src/` (pair-trading engine + harness)
Owner: quant dev
Baseline to preserve: `configs/config_best.json` → `results/103`
(+24.29% / 11.58% ann, Sharpe 1.38, Sortino 2.59, IR 0.724, DD 5.45%; circuit breaker armed).

---

## 1. Objective

Decompose the monolithic pipeline into **independent, single-responsibility components
communicating through one append-only artifact: `tape.txt`** (a decision tape).

The decision engine only **decides** and writes events. Everything downstream
(metrics, rebalancing/orders, reporting, live reconciliation) **reads the tape** and
does one job. No downstream component may import or reach into the decision engine's
internal state.

### Principles
1. **One source of truth**: the tape is the only interface between decision and everything else.
2. **Replayable & auditable**: the tape alone must reproduce every reported number.
3. **No look-ahead**: the tape carries execution timing explicitly (T+1 open).
4. **Deterministic**: same tape ⇒ same metrics (bit-for-bit).
5. **Independently runnable**: each consumer is a standalone script.
6. **No behaviour change**: the refactor is *architectural*, not alpha. Results must match `results/103` exactly.

---

## 2. Current coupling (analysis)

Today the walk-forward **fuses decision + bookkeeping + backtest inputs**, and the
attribution stage reaches back into those structures:

```
WalkForwardStage ──produces──> ctx: master_weights, ghost_weights, promoted_weights,
                                     pair_weight_matrices, trade_entry_log,
                                     metrics_history, active_pairs, early_stop
                                   │
AttributionStage ──consumes────> the wide weight matrices, re-runs vectorbt,
                                 then computes neutrality / tearsheets / MOO blotter
```

Problems:
- **Fused concerns**: decision (walk-forward) and measurement (attribution) share wide
  DataFrame state; the attribution re-implements P&L to judge runs.
- **Opaque interface**: the "output" is a 200×500 weight matrix, not an auditable event log.
- **Not replayable offline**: you cannot compute metrics or build an order blotter
  without re-running the whole pipeline.
- **Live path coupled**: execution/orders are generated inside reporting, not as a
  separate concern.
- **Bug surface**: the orphan-leg and early-stop-equity bugs both stemmed from
  bookkeeping living in two places (see §8).

---

## 3. Target architecture

```
                 ┌──────────────────────────────┐
 processing      │ Residualizer / AlphaCube     │  → signals (context only)
 (signal)        └──────────────────────────────┘
                              │
                 ┌──────────────────────────────┐
 execution       │ WalkForwardStage (DECISION)  │  → writes tape.txt  (ONLY output)
 (decision)      └──────────────────────────────┘
                              │
                        tape.txt  (append-only JSONL)
                              │
        ┌─────────────────────┼─────────────────────┬────────────────────┐
        ▼                     ▼                     ▼                    ▼
 tape_metrics.py       tape_execute.py       tape_report.py       tape_audit.py
 (measurement)         (rebalancing/orders)  (tearsheet/MDD)      (live reconcile)
```

Ownership:
| Concern | Component | Consumes | Produces |
|---|---|---|---|
| Signal | `residualizer`, `alpha_cube` | prices | signals |
| **Decision** | `walk_forward` | signals | **`tape.txt`** |
| Measurement | `scripts/tape_metrics.py` | tape | `metrics.csv`, `portfolio_stats.csv` |
| Rebalancing/exec | `scripts/tape_execute.py` | tape (+ live positions) | MOO blotter, orders |
| Reporting | `scripts/tape_report.py` | tape + metrics | tearsheet, MDD, plots |
| Live reconcile | `scripts/tape_audit.py` | tape + broker fills | slippage, reconciliation diff |

---

## 4. Tape schema (`tape.txt`)

**Format**: JSON Lines — one object per line, append-only, human-readable, streamable.
**Location**: `results/{run}/tape.txt` (alongside `logs.txt`).
**Versioned**: every line carries `"v": 1`.

### 4.1 Header (first line)
```json
{"v":1,"type":"HEADER","run":"103","ts":"2024-09-09T00:00:00","config_sha":"<hash>",
 "universe":"data/universe_daily_1y.csv","n_tickers":501,"start_idx":3945,
 "initial_capital":1000000.0,"exec":"T+1","fill":"MOO","fees":{"exec_fee":0.00015,"borrow_bps":50.0}}
```

### 4.2 Event lines
```json
{"v":1,"type":"REBALANCE","ts":"2024-09-09","cause":"INITIAL_ENTRY",
 "gross":1.83,"net_dollar":0.02,"net_beta":-0.01,"spy_overlay":0.31,
 "n_pairs":6,"n_long":7,"n_short":7}

{"v":1,"type":"ENTRY","ts":"2024-10-23","pair":["KEY","NTRS"],"legs":["KEY","NTRS"],
 "beta":0.94,"entry_z":-1.07,"ecdf":0.97,"alpha":0.91,"entry_type":"BASE",
 "sleeve_w":0.30,"w_a":-0.30,"w_b":0.281,"px_a":18.42,"px_b":61.10}

{"v":1,"type":"EXIT","ts":"2024-11-06","pair":["KEY","NTRS"],"reason":"REVERTED",
 "exit_z":0.05,"days_held":5,"alpha":0.00,"px_a":18.12,"px_b":62.03}

{"v":1,"type":"BOOK","ts":"2024-10-23","targets":{"KEY":-0.30,"NTRS":0.281,"SPY":0.31}}
```
Notes:
- `BOOK` events capture the **full target book row** (all tickers + SPY overlay). This is
  what makes downstream rebalancing/order generation exact and removes the need for the
  wide `master_weights` matrix. (Motivated directly by the orphan-leg bug — §8.)
- `ENTRY/EXIT` are the audit/attribution view; `BOOK` is the execution view. Both derived
  from the same decision, written atomically per rebalance.
- Optional events: `{"type":"BARRIER","kind":"CIRCUIT_BREAKER","detail":{...}}`,
  `{"type":"GATE","state":"OFF","reason":"disp_below_q30"}`.

### 4.3 Invariants (validated by a tape linter)
1. Every `ENTRY` has exactly one matching `EXIT` (or `open` at tape end).
2. Every `REBALANCE` is followed by ≥1 `BOOK` with the same `ts`.
3. A ticker's net weight across concurrently open pairs equals its `BOOK` target
   (**the orphan invariant**).
4. `abs(sum(book weights)) ≤ max_gross_exposure`.

---

## 5. Components

### New
- `modules/core/tape.py` — `TapeWriter` (append-only, fsync-per-event, schema-validated),
  `TapeReader` (generator of typed events), `lint()`.
- `scripts/tape_metrics.py` — read tape → reconstruct daily book → P&L (T+1 open, fees,
  borrow) → metrics: total/ann return, vol, Sharpe, Sortino, max DD, win rate, PF,
  expectancy, exposure, turnover, **excess vs SOFR, Information Ratio**, circuit-breaker
  status. Writes `metrics.csv` + `portfolio_stats.csv`.
- `scripts/tape_execute.py` — read tape (latest `BOOK`) + live positions/prices → MOO
  blotter (reuses `LiveMOOOrderGenerator`). Pure function of the tape + broker state.
- `scripts/tape_report.py` — read tape + metrics → tearsheet, neutrality audit, MDD factsheet.
- `scripts/tape_audit.py` — read tape + broker fills → implementation shortfall / slippage.

### Refactor
- `modules/execution/walk_forward.py` — decision only; emits the tape. No longer exports
  wide weight matrices as the interface (keep behind a `--legacy-matrices` flag during migration).
- `modules/reporting/attribution.py` — becomes a thin adapter that calls `tape_metrics`
  (or is retired in favour of `tape_metrics.py`).
- `modules/reporting/reporting.py` — orchestrates tape consumers; prints from `metrics.csv`.
- `main.py` — flow stays config-driven; decision stage writes the tape; a
  `--from-tape` mode runs consumers **without** re-deciding.

---

## 6. Migration phases (incremental, each independently validated)

**P1 — Emit (no behaviour change).**
Add `TapeWriter`; `walk_forward` writes `tape.txt` *in addition* to the existing matrices.
Validate: reconstructed book from tape == `master_w` for every date (max abs diff < 1e-9).
Acceptance: `results` unchanged (still +24.29%).

**P2 — Measurement decoupled.**
`tape_metrics.py` computes all reported metrics. Validate vs the engine:
`|tape_metrics − results/103 portfolio_stats| < 1e-6` on return, DD, Sharpe, Sortino, IR.
Acceptance: exact match.

**P3 — Execution decoupled.**
`tape_execute.py` reproduces the MOO blotter from the tape alone.
Validate vs the engine blotter: identical rows (ticker, side, shares, weight).

**P4 — Tape as source of truth.**
`attribution`/`reporting` read metrics from `tape_metrics` and the TapeReader, not ctx.
`main.py --from-tape` runs P2–P4 with no decision re-run.
Acceptance: identical `results/` outputs.

**P5 — Retire the fused path.**
Remove the wide-matrix interface (or keep behind the legacy flag); delete duplicated P&L.

Definition of done: the decision engine imports **nothing** from reporting; reporting
imports **only** the tape; `tape_execute` imports **only** the tape + broker adapter.

---

## 7. Acceptance criteria (global)

1. **Numerical identity**: tape-derived metrics == engine metrics (< 1e-6) on `results/103`.
2. **Book identity**: tape-reconstructed book == engine book at every timestamp.
3. **Order identity**: tape-derived MOO blotter == engine blotter (row-for-row).
4. **Replay**: `tape_metrics.py results/103/tape.txt` runs standalone, offline, deterministically.
5. **Lint**: tape passes all §4.3 invariants (esp. the orphan invariant).
6. **No alpha drift**: the decision engine's logic is byte-for-byte unchanged.

---

## 8. Why this refactor also reduces bugs (prior evidence)

Both significant bugs in this campaign were **bookkeeping duplicated across layers**:
- **Orphan-leg bug**: leg weights patched in one place (`=`) while accumulated in another (`+=`);
  a shared ticker stranded a position for 1,070 days. A single authoritative `BOOK` event
  (§4.2) makes the book the tape's job, not the engine's.
- **Early-stop equity bug**: the stop computed its own P&L that diverged from the engine's.
  With one measurement consumer (`tape_metrics`), there is exactly one P&L implementation.

---

## 9. Risks & non-goals

**Risks**
- *Tape size*: ~1k rebalances × (ENTRY+EXIT+BOOK) ≈ tens of MB for a 5y run — acceptable; gzip option.
- *Reconstruction correctness*: T+1 open fills, fees, borrow must be modelled identically to
  vectorbt; mitigated by the P2 numerical-identity gate.
- *Multi-pair leg netting*: `BOOK` must net overlapping legs (the orphan invariant) — enforced by the linter.
- *Determinism*: JSON float repr and event ordering pinned; no wall-clock in decisions.

**Non-goals**
- No change to alpha, sizing, exits, or risk logic.
- No new data sources.
- Not a microservices/distributed rewrite — still one process, files on disk.

---

## 10. Task checklist (execution order)

- [ ] **T1** Define `TapeWriter`/`TapeReader`/`lint` in `modules/core/tape.py` (+ schema tests).
- [ ] **T2** Emit HEADER/REBALANCE/ENTRY/EXIT/BOOK from `walk_forward` (behind `tape_enabled`).
- [ ] **T3** Book-identity test: tape book == `master_w` (< 1e-9) on `configs/config_best.json`.
- [ ] **T4** `scripts/tape_metrics.py` + numerical-identity test vs `results/103`.
- [ ] **T5** `scripts/tape_execute.py` + blotter-identity test.
- [ ] **T6** `scripts/tape_report.py` (tearsheet/MDD from the tape).
- [ ] **T7** `main.py --from-tape` (consumers without re-deciding).
- [ ] **T8** Retire the fused attribution path; keep legacy behind a flag.
- [ ] **T9** Update `README` + `OVERNIGHT_REPORT.md`; add `tape.txt` to artifacts.

---

## 11. Interface contracts (freeze before P1)

```python
# modules/core/tape.py
class TapeWriter:
    def header(self, meta: dict) -> None: ...
    def event(self, obj: dict) -> None: ...        # validates schema, appends JSONL, fsync
    def close(self) -> None: ...

class TapeReader:
    def events(self, types: set[str] | None = None) -> Iterator[dict]: ...
    def header(self) -> dict: ...

# scripts/tape_metrics.py
def compute_metrics(tape_path: str, params: dict) -> dict: ...
#   returns {total_return, ann_return, vol, sharpe, sortino, max_dd, win_rate,
#            profit_factor, expectancy, turnover, gross_mean, excess_vs_sofr,
#            information_ratio, circuit_breaker_fired, circuit_breaker_date}

# scripts/tape_execute.py
def build_blotter(tape_path: str, live_positions: dict, prices: pd.Series,
                  nav: float, params: dict) -> pd.DataFrame: ...
```
