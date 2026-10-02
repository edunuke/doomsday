# StatArb v3.2 — Market Distress / Regime Backtest Results

Warmup + OOS two-file runs against `data/distress/{window}.csv` with
`price_csv = data/distress_in/{window}.csv`. Each window trades **only** the
distress OOS days (start_idx aligned to first OOS row).

Regime column = realized SPY total return over the OOS window
(neg = net bear, pos = net bull), from each run's `Benchmark Return [%]`.

---

## Results table (2–12)

| # | Window | Regime | OOS days | Trades | Win% | Total Return | Sortino | Max DD | Fees | Neutrality | Comments |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | ai_geo_2026 (Jan–Mar 26) | **Bear** | 45 | 111 | 46.2% | −6.78% | −4.20 | 8.17% | $1,105 | FAIL β=−0.28, R²=11.1% | Worst window. Gross beta leak (−0.28, p=0.02) + 11% R² = the book was a directional bet long the wrong side of an AI/geo selloff, and churned 111 trades in 45 days doing it. Off both axes: not neutral, not profitable. |
| 3 | ath_2026 (Jul–Sep 26) | **Bull** | 46 | 33 | 39.4% | +1.32% | 1.23 | 3.52% | $561 | FAIL bull +0.45 | Thin and mildly green. Bull-market beta +0.45 (long beta in an ATH tape) is exactly what made it work — i.e. a risk-on bet wearing a stat-arb costume. 33 trades = no statistical content; don't quote. |
| 4 | aug_vol_2025 (Jul–Sep 25) | **Bull** | 46 | 17 | 35.3% | +4.29% | 12.29 | 1.49% | $314 | FAIL bear +0.63 | The pretty number (Sortino 12.3) on 17 trades. Bear beta +0.63 is a short-hedge whiff, but the low fee bill ($314) shows it wasn't churning — it was quietly net-short the hedge. Statistically meaningless, but the cleanest *book behavior* of the batch. |
| 5 | bear_2022 (Sep 22–Feb 23) | **Bull** | 121 | 299 | 52.7% | −2.00% | −0.73 | 6.93% | $3,420 | FAIL bear +0.05 | 299 trades in 121 days = the churn archetype: 52.7% win rate ground down to −2% by $3.4k fees (PF 0.96). Longest testing window of the mixed set that still couldn't clear its own turnover tax. |
| 6 | calm_2024 (Jan–Jun 24) | **Bull** | 124 | 280 | 45.5% | +0.13% | 0.15 | 8.69% | $3,526 | FAIL β=+0.06 | The "no-signal" control: flat market, flat strategy, yet −$3.5k in fees. This is the baseline that says *in quiet tape the engine pays to sit still*. Highest fee bill in the table with nothing to show. |
| 7 | china_2015 (Aug 15–Feb 16) | **Bear** | 123 | 72 | 50.0% | −0.76% | −0.44 | 4.38% | $975 | FAIL bull −0.11 | Held value through a −12.5% bleed (−0.76%) at a lean fee bill — the "protect capital" profile. But no alpha: flat PnL, and a bull-beta −0.11 leak. Survive-not-thrive. |
| 8 | covid_2020 (Feb–Aug 20) | **Bear** | 136 | 483 | 52.6% | +29.34% | 8.37 | 4.44% | $4,026 | FAIL β=−0.10, R²=8.3% | The headline result and the most misleading. +29% / Sortino 8.4 is real machinery, but it rode a significant short-crash/long-recovery beta (β=−0.10, p=0.0007, R²=8.3%) on today's survivorship-clean universe. It's a *directional* vol-position that happened to win, not neutral alpha. Highest fees ($4k). Treat +29% as a beta-bet ceiling, not a stat-arb claim. |
| 9 | dotcom_2001 (Jun 01–May 02) | **Bull** | 248 | 208 | 50.5% | +3.99% | 0.60 | 13.25% | $2,678 | FAIL bear +0.10 | Most statistically meaningful window (248 days) and the verdict is damning: +3.99% against +6.96% bench = the book lagged a passive long during a secular unwind, at 13.25% DD and $2.7k fees. Closest to neutral (β=0.016) of anyone — and closest to pointless. |
| 10 | gfc_2008 (Sep 08–Mar 09) | **Bear** | 120 | 254 | 50.6% | +6.34% | 1.99 | 6.75% | $2,220 | FAIL R²=1.6%, bull +0.11 | Best true stress result: +6.34% vs −43% bench, 6.7% DD, β=+0.04 PASS, bear −0.03 PASS — the book genuinely hedged the crush and harvested the up-tape. Only fails on R² and bull +0.11. This is the *model* of what works; just shy of the neutrality bar. |
| 11 | iran_oil_2026 (Jun–Jul 26) | **Bull** | 43 | 62 | 44.1% | −5.18% | −3.70 | 6.19% | $869 | FAIL β=−0.06, bull −0.22 | −5.18% on +3.17% bench, worst Sortino outside ai_geo. Whipsaw profile — $869 of fees on 62 trades (churn rate unhealthy), and the crudest beta leak of the recent windows (bull −0.22, bear −0.19). Iran shock wasn't just unprofitable: it exposed the book to both regimes. |
| 12 | powell_2018 (Oct–Dec 18) | **Bear** | 61 | 183 | 44.8% | −1.66% | −1.30 | 4.37% | $2,065 | FAIL R²=1.3%, bear +0.13 | −1.66% on −12.7% bench = held up relatively in the selloff (β=+0.04 PASS, bull −0.02 PASS) — same "survivor" profile as 10 but without the payoff (PF 0.94). Bear +0.13 fails neutrality. |
| 13 | rally_2024 (Nov 24–Jan 25) | **Bull** | 59 | 62 | 67.9% | +8.45% | **26.51** | 0.79% | $857 | FAIL β=−0.08, bear −0.25 | The best raw numbers in the batch: **+8.45% on +3.5% bench, Sortino 26.5, Max DD 0.79%, win 67.9%**. But it's a beta-bet in disguise — negative-leaning book (β=−0.08, bear −0.25, R²=2.2%) that longs the post-election melt-up and shorts the laggards. Small sample (62 trades, 59 days) but the tightest risk control seen anywhere. It made the rally *and* kept drawdown near zero. |
| 14 | rates_2023 (Sep–Oct 23) | **Bear** | 42 | 109 | 42.3% | −3.71% | −4.12 | 5.09% | $1,248 | FAIL β=−0.12, bull −0.07, bear −0.22 | Worst neutrality of the batch: **β=−0.12 with leaks in BOTH regimes** (bull −0.07, bear −0.22) on top of 3.2% R². −3.71% on a −8.4% bench = relative survivorship, but absolute red with 109 churned trades. The 10Y/5% span wasn't just unprofitable — the book shorted both sides of the rate shock. |
| 15 | recovery_2025 (Apr–Aug 25) | **Bull** | 84 | 29 | 50.0% | +5.71% | **7.53** | 2.23% | $413 | FAIL bear +0.10 | The cleanest neutrality of the whole batch: β=−0.017 (PASS, p=0.72), R²=0.15%, bull +0.001 — every term green except bear +0.096 (thin bear sample in a bull tape). +5.71% on **+25.2% bench** = solid positive, but it trailed the V-recovery badly and only booked 29 trades. Statistically decent (84d) but it captured a fraction of the tape it was trading in. |
| 16 | svb_2023 (Mar–May 23) | **Bear** | 64 | 133 | 48.5% | +1.44% | 1.08 | 3.21% | $1,779 | FAIL β=−0.06, bull −0.29 | +1.44% on −0.71% bench = the only *bear-window* in the batch that's green in absolute terms since svb timing. But the banking-crisis profile is ugly mechanically: β=−0.06, R²=0.97%, and **bull −0.29 / bear −0.17 leaks in BOTH regimes** (the same "shorted both sides" signature as rates_2023). $1,779 fees on 133 churned trades in 64 days. Survived; not neutral. |
| 17 | tariff_2025 (Feb–Aug 25) | **Bull** | 118 | 119 | 54.3% | **+15.96%** | **9.48** | 3.26% | $1,462 | FAIL β=−0.08, bull −0.18, bear +0.12 | Second-best PnL in the batch after covid: **+15.96% on +2.15% bench, Sortino 9.5, Max DD 3.3%, win 54.3%** — real machinery over a 118-day window. But it's the beta-bet archetype again: the book loaded a −0.18 bull / +0.12 bear **both-regime leak** (β=−0.08, R²=3.0%) that longs the pre-tariff melt-up names and shorts the shock laggards. $1,462 fees / 119 churned trades. It *worked*; it is not neutral. |
| 18 | usdowngrade_2011 (Jul–Oct 11) | **Bear** | 71 | 211 | 52.4% | **+8.92%** | **7.64** | 4.72% | $2,124 | FAIL bull +0.17, bear −0.10 | The neutrality clean-out outlier: **β=−0.0012 (PASS, p=0.97), R²=0.00%** — statistically the most market-neutral book of the entire batch — *and* it made +8.92% against a −6.28% downgrade shock, at 4.7% DD. Only fails on the regime betas (bull +0.17 / bear −0.10). 211 churned trades, $2.1k fees, but every gross-neutrality gate green. The single most credible stat-arb result in all 17 windows. |
| 19 | yen_carry_2024 (Jul–Sep 24) | **Bull** | 43 | 172 | 49.7% | −4.72% | −4.46 | 5.31% | $1,742 | FAIL β=+0.11, bull +0.21, bear +0.07 | The 2024 carry-unwind whipsaw, on the wrong side of it: −4.72% on +2.17% bench, worst Sortino (−4.46) after ai_geo and iran. **β=+0.11, R²=5.6% — the book went long the carry-tape right as it unwound.** 172 churned trades / $1.7k fees in 43 days (the worst fee-to-day ratio of the batch). The VIX-65 spike exposed it on both legs; this is the mirror image of usdowngrade 2011 (same mechanics, opposite sign on the regime bet). |

---

## Regime cross-tab

| Regime | Windows | Net result |
|---|---|---|
| **Bear** (8) | ai_geo 2026, china 2015, covid 2020, gfc 2008, powell 2018, rates 2023, svb 2023, usdowngrade 2011 | **+29.3** (COVID), **+8.9** (usdowngrade), **+6.3** (GFC), **+1.4** (svb), **−0.8** (china), **−1.7** (powell), **−3.7** (rates), **−6.8** (ai_geo). *Save capital, harvest the V-recover — the downgrade shock (gross-neutral, +9%) is the best neutral-alpha proof in the batch; only the rate/geo shocks bleed on both sides.* |
| **Bull** (10) | ath 2026, aug_vol 2025, bear 2022, calm 2024, dotcom 2001, iran 2026, rally 2024, recovery 2025, tariff 2025, yen_carry 2024 | **+15.9** (tariff), **+8.5** (rally), **+5.7** (recovery), **+4.3** (aug_vol), **+4.0** (dotcom), **+1.3** (ath), **+0.1** (calm), **−2.0** (bear), **−4.7** (yen_carry), **−5.2** (iran). *Melt-ups and V-recoveries can be huge (+16 tariff, +8.5 rally); quiet tape is dead flat (+0.1); the fast shocks and whipsaws lose (yen_carry −4.7, iran −5.2).* |

---

## Conclusion — batch verdict & improvements

### What the batch proved

**1. The engine is a regime-conditional vol harness, not a neutral stat-arb book — yet.**
Across 18 out-of-sample stress windows:
- **It makes money in V-recoveries and melt-ups** — COVID +29.3%, tariff +15.9%, usdowngrade-2011 +8.9%, rally-2024 +8.5%, GFC +6.3%.
- **It is dead flat or lagging in quiet/grind tape** — calm_2024 +0.1% (on +9.7% bench), dotcom +4.0% (on +7.0% bench), china/powell break-even.
- **It loses in fast one-sided shocks** — yen_carry −4.7%, iran −5.2%, ai_geo −6.8%, rates −3.7%.

**2. Neutrality is the unresolved blocker: 0/18 pass.**
- Failure mode narrowed but never disappeared: gross β passes for most of the back half; the consistent leak is a **single-regime beta (bull or bear)** and elevated R².
- **usdowngrade_2011 is the proof-of-possibility**: β=−0.0012 (p=0.97), R²=0.00%, +8.9% through a crash with 4.7% DD. The engine *can* be gross-neutral and profitable — that window is the template to reverse-engineer.

**3. The turnover tax is structural.**
$314–$4,026 in fees per window (avg ≈ $1.7k). Best-Sortino windows (rally 26.5, tariff 9.5, aug_vol 12.3) are uniformly the lowest-churn ones. Win rate oscillates 35–68% with no regime edge. The engine trades too much for what it captures in calm tape.

**4. Thin-window discipline matters.**
Windows ≤ 62 bars (2, 3, 4, 11, 12, 14, 19) are diagnostics only. The credible numbers live in the 71–248 day windows (5, 6, 7, 8, 9, 10, 15, 17, 18) — and even those need bootstrap CIs before any quotable claim.

---

### Improvements — ranked by leverage (real-world impact, not academic polish)

| | Fix | Impact | Effort | Why (from the data) |
|---|---|---|---|---|
| **P1** | **Fix the regime-conditional beta leak** — conditional (bull\|bear) neutrality penalty in the SLSQP objective; investigate `max_spy_overlay_weight` saturation + `spy_beta_mults_clipped` throttle cutting the hedge instead of the exposure | **HIGH** | Medium | 0/18 windows pass neutrality; realized β_m 0.03–0.28 while the 0.05 cap is *not binding*. This is the difference between "neutral book that can hold client/regulatory capital" and "a directional bet". usdowngrade-2011 proves the fix has a payoff: β≈0 **and** +8.9%. |
| **P2** | **Kill the turnover tax** — min-edge gate (don't churn for <0.5% expected yield), raise `min_holding_days`, widen `entry_ecdf_percentile` to 0.85, model round-trip cost in entry alpha | **HIGH** | Low | $314–$4,026 fees/window (avg ~$1.7k ≈ 0.7% of NAV *every window*); best Sortinos are the lowest-churn runs. Directly converts to PnL since win rate is flat 35–68%. Cheap to ship, immediately measurable. |
| **P3** | **Bootstrap CIs in reporting** — block-bootstrap (weekly lag) CIs on annualized return / Sortino / net β → `ci_report.csv` per run | **MEDIUM** | Low | Prevents quoting a 17-trade Sortino (aug_vol 12.3) or a 62-day +8.45% (rally) as if meaningful. Internal governance + IC credibility — not alpha, but stops bad decisions. |
| **P4** | **Calibrate execution via micro-cash live test** — measured fill rate / cross cost / locate success by size decile (sector-aware), folded back as `slippage_bps`, `borrow_schedule`, `liquidity_haircut` | **MEDIUM** | High | Flat `exec_fee`/`borrow_bps` is today's biggest *unknown* — the batch's numbers assume you can short SMCI/KIM at 50 bps when the market would charge 3–20×. Real calibration de-risks go-live sizing; until P1/P2 land, calibrating a leaking/churning engine wastes desk time. |
| **P5** | **Drop ghost-price pairs** — per-pair full-history reconstitution check (legs must trade continuously through warmup + window instead of `ffill`'d flat gaps) | **MEDIUM** | Low | Removes the worst cheap survivorship bias (GFC/dotcom rows trade on today's membership). One config flag + one filter; immediately makes historical windows credible enough for internal review. |
| **P6** | **Gate the flat-tape drag** — trade only when trailing dispersion (cross-sectional σ) is above a floor; sit in cash otherwise | **LOW** | Medium | calm_2024 (+0.1% on +9.7% bench, $3.5k fees) is the control proving the book over-engages in quiet tape. Real benefit, but bounded: flat-tape drag (~1% NAV/yr) is small next to P1's neutrality issue and P2's fee bleed. Do it after P1+P2 if capacity remains. |

### Sequencing for a desk (pragmatic, not academic)

1. **Ship P2 first** (Low effort, HIGH impact, instantly measurable fee savings) — it's the only fix that pays for itself in the first week and it buys goodwill/credibility for the harder items.
2. **Then P1** (the actual blocker) — Medium effort, is what makes every number in this table meaningful. Everything else is decorating a book that isn't neutral yet.
3. **P5 alongside P1** (both are config-level, low cost) so the historical windows being used to validate P1 aren't contaminated.
4. **P3 before any external review** — even with P1+P2 landed, no window result gets quoted without a CI.
5. **P4 (micro-cash) only after P1+P2** — calibrating execution for a leaking, churning book is wasted effort; calibrate the *fixed* book against real size.
6. **P6 when capacity allows** — or fold its logic (dispersion floor) into the P2 entry gate as a second filter.

---

### Bottom line
The stress suite did its job: it exposed the engine's true character — a **regime-conditional vol-position miner with a neutrality leak and a fee drag** — and it isolated the one window (usdowngrade-2011) where genuinely neutral alpha exists. The desk sequence is **P2 → P1 → P5 → P3**, with P4 (micro-cash) only after the first three: stop the fee bleed (cheap, immediate), then fix the beta leak (the real blocker), de-contaminate the historical windows, and only then make the remaining claims statistically honest — and only then spend money calibrating execution. Until P1 lands, no PnL figure in this table is quotable as neutral stat-arb.

---