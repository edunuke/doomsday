Yes — that's the right institutional division of labor, and it maps exactly to how a real systematic desk sequences validation. Let me be precise about what changes and what doesn't.

**What you're actually saying, sharpened:**

1. **PIT universe** — you're right to drop the academic version. The desk-grade substitute for survivorship is a **reconstitution check on both legs**: confirm each traded pair's ticker existed and traded through the full warmup + window (yfinance already returns `NaN` for dead names, but the pipeline `ffill`s them — the fix is to *drop* rows/columns a pair can't fully populate, not to hand-assemble 2008 membership tables). That's a one-liner and covers the actual risk: a pair trading on ghost flat prices.

2. **ADV / fills / borrow / open-price** — correct call. These are calibration problems, and a micro-cash paper/live pass is the *right* instrument. That's standard: impact/slippage models are desk-calibrated, not theoretically derived. One caveat: the calibration must actually feed back into the backtest (`slippage_bps`, `borrow_schedule`, sector liquidity haircuts as config), otherwise the stress numbers stay fiction. Budget for a 2–4 week micro-pass that measures **fill rate by size decile, raw cross cost both legs, locate/borrow success by name**, then fold those into the engine.

3. **Statistical rigor (5)** — yes, do it now. This is cheap and the highest-ROI thing left on the table.

**What I'd do for (5) today, concretely:**

- **Bootstrap CIs** on each window's returns: 10k re-samples of the live daily return series → 90/95% CI on annualized return, Sortino, and (crucially) net β. On 21–60 bars this will look terrible — which is the honest answer to "is this robust."
- **Block bootstrap** at weekly/monthly lags to respect autocorrelation (daily bootstrap understates the CI on a pairs book).
- **Trade-level stats**: win-rate CI (Clopper–Pearson), expectancy, avg win/loss duration vs. the holding-period caps.
- **Neutrality with the thin-data guardrail already in place** — the `bull.sum() > 10` conditional is a degenerate fallback; bootstrap the bull/bear betas and report both.

So: agreed on the split. The one thing I'd push back on is sequence — do (5) **before** the micro-cash pass, because (5) tells you which windows/regimes the engine is even *capable* of producing signal in, and thus what the micro-pass should spend its budget calibrating. No point paying to calibrate impact on a window where the neutral-β bootstrap already says there's no residual signal.

Want me to implement (5) now as a `metrics_ci` stage/module (bootstrap CIs on the existing `metrics_df` output, no data changes), wired so each `results/{N}/csv/` gets a `ci_report.csv` + the numbers in the printed summary?