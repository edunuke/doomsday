# OPTIMIZATION — `src_tape/` performance pass

Goal: make the tape pipeline faster while keeping the decision layer
(config-driven entries, SLSQP book, exits) **exactly** as before. Any change
that altered the decision tape was rejected.

## Result

| | before | after |
|---|---|---|
| Wall clock | 223.3s | **55.9–57.9s** |
| CPU (`user`) | 324s | ~370s (cores saturated) |
| AlphaCubeStage | — | 5.6s |
| WalkForwardStage | — | 45.3s |
| AttributionStage | — | 2.4s |
| ReportingStage | — | 1.4s |

**~4.0× faster**, with `tape.txt`, `metrics.csv`, `portfolio_stats.csv`,
`trade_records.csv` and `committee_report.csv` **byte-identical** to the
pre-optimization baseline.

## Profiling findings

Initial `cProfile` on the main process (331s profiled):

- `statsmodels` `coint` → `adfuller`: **~278s** — the walk-forward
  cointegration screen, run as a **serial** list comprehension.
- Everything else was noise by comparison.

After parallelizing the screen, the main process spent ~48s in `time.sleep`
(waiting on joblib workers): the wall time is now worker compute, i.e.
`coint` (screen) + `process_pair_history_fast` (alpha-cube retrain).

## Changes

### 1. Parallel cointegration screen — `modules/execution/walk_forward.py`
The screen tested every candidate pair with `coint` sequentially. It now runs
as a chunked joblib task returning a **boolean pass-mask**, recombined in the
original candidate order:

```
masks = _pool(delayed(_screen_mask)(chunk_pairs, vals, cmap, thr) for ch in chunks)
mask = np.concatenate(masks)
cached_keys = [p for p, ok in zip(candidate_pairs, mask) if ok]
```

Order preservation matters: the accepted set/order feeds the ghost pool and the
optimizer, so it is kept bit-identical to the serial path.

### 2. Persistent worker pool
One `Parallel(n_jobs=-1, backend="loky")` is created once and reused for both
the alpha-cube retrain extension and every screen. Previously a fresh pool was
started per `with Parallel(...)` block; over a run there are ~25 screens plus
retrains, so worker startup is now paid once.

### 3. Shared-memory inputs to workers
`max_nbytes=256KB` makes joblib memmap the windowed residual matrix into one
shared buffer instead of pickling a ~1MB copy per task. Chunk count is one per
core (`min(os.cpu_count(), n_cand)`) rather than a fixed 40, cutting IPC ~4×.

### 4. Vectorized rolling ECDF — `modules/processing/utils.py`
`rolling_ecdf_fast` was an `O(n·window)` Python loop. It now uses a strided
window view:

```
win = sliding_window_view(arr, window)          # (n-window+1, window)
counts = (win <= arr[window-1:][:, None]).sum(axis=1)
out[window-1:] = counts / window
```

The rank count is an integer sum of booleans, so this is **bit-exact** to the
loop and **12× faster** (3.78ms → 0.31ms per call).

### 5. Portfolio engine skips no-op assets — `modules/execution/portfolio_engine.py`
The daily fill loop iterated all 502 columns. It now iterates only indices where
the target actually moved:

```
for i in np.flatnonzero(delta):
```

On non-rebalance days `delta` is all zeros, so the loop is skipped entirely —
removing ~250k Python iterations per simulation.

### 6. BLAS/OMP thread pinning — `main.py`
`OMP/OPENBLAS/MKL/NUMEXPR/VECLIB` thread counts are pinned to 1 before any
numeric import. The heavy stages already saturate all cores via joblib; without
this each worker can spawn its own multithreaded BLAS pool and oversubscribe the
CPU. (Measured neutral here, retained as a guard for other hosts.)

### 7. Per-stage timing — `modules/core/orchestrator.py`
Each stage now logs `<<< Stage: Name complete. (Xs)`, making regressions
visible without a profiler.

## Deliberately rejected

**Kalman scalar/JIT rewrite.** A flat-scalar (optionally `numba.njit`) version of
`batch_kalman_filter_numpy` is ~400× faster per call (8.3ms → 0.02ms), but it
reorders floating-point operations by ~1e-14. That perturbs the SLSQP objective
at machine epsilon, so the solved sleeve weights differ in the last digits and
the tape is no longer byte-identical (entries still identical, but the
reproducibility guarantee is broken). It saved only ~0.7s of wall time, so the
matrix form was kept, with a comment in the source explaining why.

## Remaining floor

The run is now genuinely CPU-bound on the workers:

- `statsmodels.coint`: ~4.4ms × ~45k calls ÷ 10 cores ≈ **20s**, and
- alpha-cube retrain (`process_pair_history_fast`): ~**9s**.

Going lower means replacing the ADF/Engle-Granger math with a faster
decision-equivalent, which risks changing which pairs pass the screen, or adding
cores. Both were judged not worth breaking the identical-decision guarantee.

## Reproduce

```
cd src_tape
python main.py --config configs/config_best.json
```

Verify the tape is unchanged with, e.g.:

```
diff baseline/tape.txt results/1/tape.txt
python scripts/tape_audit.py results/1/tape.txt   # lint 0, orphan invariant 0
```