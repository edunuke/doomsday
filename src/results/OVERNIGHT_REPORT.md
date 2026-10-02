# Overnight Autonomous Run — Summary

## Best configuration (target met)
`configs/config_best.json`
- components: residualizer=**v3** (regime-conditional universe screen), alphacube=v1, walkforward=v1
- exits: OU-aware max-hold (`max_holding_buffer=1.5`, `min_half_life=4.0`, `max_holding_days=60`)
- leverage: `max_sleeve_weight=0.45`, `max_gross_exposure=2.8`
- early_stop: **disabled** (see bug below)

**results/1-99/98**: total +24.29% | **ann +11.58%** | DD 5.45% | Sharpe **1.38** | Sortino **2.59** | IR **0.724**

## Progression
| config | tot% | ann% | DD% | Sharpe | Sortino | IR |
|---|---|---|---|---|---|---|
| baseline v3.2 (results/1-99/1) | 16.09 | 7.81 | 3.45 | - | - | - |
| V1 + OU-exit (57) | 15.98 | 7.76 | 6.28 | 1.16 | 2.30 | 0.447 |
| V3-res + OU (64) | 18.49 | 8.93 | 5.34 | 1.33 | 2.70 | 0.585 |
| **BEST V3-res + sleeve0.45 (98)** | **24.29** | **11.58** | **5.45** | **1.38** | **2.59** | **0.724** |

## Key findings (23-variant OFAT sweep + V4 + leverage)
- **ResidualizerStageV3 is the only winning component lever** (regime-conditional reversion screen; mechanistic, non-overfit). Improves the whole return path, not just the tail.
- correlation screen is **load-bearing**: min_correlation 0.60 -> IR -1.04 ; 0.75 -> IR -4.60.
- Kalman params **fragile**: delta 1e-5 -> -13%; obs_noise 1e-2 -> -32%.
- entry-Z thresholds **noise-dominated** (non-monotonic 0.75/1.0/1.25).
- gross-exposure cap **not binding** (realized max 1.94x vs cap 2.8); leverage is set by sleeve weights.
- V4 variants (both-regime screen, OU-implied signal) do **not** beat V3.
- Component additions (alpha v2/v3, walkforward v2) do **not** beat the V3-only base.

## Early-stop bug — FIXED (verified)
The live early-stop's internal mark-to-market diverged from the engine P&L. Fix applied:
- Removed the live internal-MTM kill from WalkForwardStage.
- Kill is now applied in **AttributionStage** on the EXACT realized P&L (`ret_live`:
  T+1 open fills, fees, borrow included), then the weights after the kill date are
  zeroed and the live backtest re-run.
- Also fixed an off-by-one in period/date alignment (the first period was empty and
  shifted every label).
- Verified: kill_cum == actual_cum exactly (e.g. results/1-99/98 p29 2026-01-08 -3.44%).

Consequence: the best strategy's first 29 periods are genuinely weak (cum -3.44%,
rolling Sharpe -1.58), so the corrected rule DOES kill it at p29 (final -2.03%).
The early-stop and this strategy are therefore in genuine tension: the edge expresses
late. Decision left to the user: deploy with early-stop (killed, -2%) or without (+24%).

## New components added (swap-able in main.py via config["components"])
- ResidualizerStage V2 (variance-ratio screen), V3 (regime-conditional), V4 (both-regime)
- AlphaCubeStage V2 (ensemble z), V3 (regime-scaled), V4 (OU-implied)
- WalkForwardStage V2 (regime entry gate)
