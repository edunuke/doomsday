# Mix report — run 68

36 fills = 24 pair legs + 12 market-overlay legs. Ranked by realised return per leg; full detail in `csv/mix_report.csv`.

## Best trades (16 winners)

| Id | Pair | Asset | Dir | Entry | Exit | Ret % | PnL $ | Entry Z | Entry reason | Exit reason | Leg thesis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | SCCO/GGB | SCCO | Short | 2020-03-11 | 2020-03-12 | +15.67 | +14,567 | +0.97 | screen 2020-02-13 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=0.97 | allocator drop 2020-03-11: all-zero solve (n=3, viol=0.675); 30d quarantine | signal leg A: z=+0.97 > 0, A rich vs B -> SELL A |
| 7 | B3SA3/ITSA4 | ITSA4 | Short | 2020-03-10 | 2020-03-12 | +13.68 | +12,018 | -0.75 | screen 2020-02-13 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=0.75 | allocator drop 2020-03-11: all-zero solve (n=3, viol=0.675); 30d quarantine | hedge leg B: SELL B at beta 0.95x leg A (weight -8.4%) |
| 0 | VALE/GGB | GGB | Short | 2020-02-25 | 2020-02-26 | +3.96 | +10,392 | -0.95 | screen 2020-02-13 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=0.95 | allocator drop 2020-02-25: all-zero solve (n=4, viol=0.9); 30d quarantine | hedge leg B: SELL B at beta 0.56x leg A (weight -25.4%) |
| 9 | B3SA3/ITSA4 | B3SA3 | Short | 2020-05-18 | 2020-05-19 | +3.00 | +3,623 | +1.14 | screen 2020-05-07 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=1.14; VolGate 3->2 | allocator drop 2020-05-18: all-zero solve (n=3, viol=0.675); 30d quarantine | signal leg A: z=+1.14 > 0, A rich vs B -> SELL A |
| 14 | PBR/A/EC | PBR/A | Long | 2020-09-29 | 2020-09-30 | +2.44 | +11,499 | -0.68 | screen 2020-09-24 v3 5/32 pass; promoted top-20 by alpha (simple mode); \|z\|=0.68 | allocator drop 2020-09-29: all-zero solve (n=5, viol=1.125); 30d quarantine | signal leg A: z=-0.68 < 0, A cheap vs B -> BUY A |
| 35 | VALE/GGB | VALE | Long | 2026-07-09 | 2026-07-10 | +1.66 | +8,775 | -0.78 | screen 2026-06-25 v3 4/31 pass; promoted top-20 by alpha (simple mode); \|z\|=0.78 | allocator drop 2026-07-09: all-zero solve (n=4, viol=0.9); 30d quarantine | signal leg A: z=-0.78 < 0, A cheap vs B -> BUY A |
| 15 | B3SA3/ITSA4 | B3SA3 | Long | 2022-11-18 | 2022-11-21 | +1.32 | +1,965 | -0.76 | screen 2022-11-17 v3 4/20 pass; promoted top-20 by alpha (simple mode); \|z\|=0.76; VolGate 3->1 | allocator drop 2022-11-18: all-zero solve (n=2, viol=0.6); 30d quarantine | signal leg A: z=-0.76 < 0, A cheap vs B -> BUY A |
| 30 | VALE/GGB | GGB | Short | 2026-04-30 | 2026-05-01 | +1.28 | +1,469 | -0.87 | screen 2026-04-02 v3 5/31 pass; promoted top-20 by alpha (simple mode); \|z\|=0.87; VolGate 2->1 | allocator drop 2026-04-30: all-zero solve (n=2, viol=0.6); 30d quarantine | hedge leg B: SELL B at beta 0.66x leg A (weight -9.0%) |
| 18 | B3SA3/ITSA4 | B3SA3 | Long | 2023-02-09 | 2023-02-10 | +1.26 | +6,095 | -0.67 | screen 2023-01-12 v3 4/20 pass; promoted top-20 by alpha (simple mode); \|z\|=0.67 | allocator drop 2023-02-09: all-zero solve (n=4, viol=0.9); 30d quarantine | signal leg A: z=-0.67 < 0, A cheap vs B -> BUY A |
| 26 | SQM/GGB | SQM | Long | 2024-08-06 | 2024-08-07 | +1.22 | +3,724 | -0.65 | screen 2024-07-11 v3 6/29 pass; promoted top-20 by alpha (simple mode); \|z\|=0.65; VolGate 5->4 | allocator drop 2024-08-06: all-zero solve (n=5, viol=1.125); 30d quarantine | signal leg A: z=-0.65 < 0, A cheap vs B -> BUY A |
| 20 | B3SA3/ITSA4 | ITSA4 | Short | 2023-02-09 | 2023-02-10 | +1.01 | +4,124 | -0.67 | screen 2023-01-12 v3 4/20 pass; promoted top-20 by alpha (simple mode); \|z\|=0.67 | allocator drop 2023-02-09: all-zero solve (n=4, viol=0.9); 30d quarantine | hedge leg B: SELL B at beta 0.84x leg A (weight -37.9%) |
| 21 | ITUB/BBD | BBD | Long | 2023-08-07 | 2023-08-08 | +0.93 | +2,718 | +0.66 | screen 2023-07-27 v3 6/20 pass; promoted top-20 by alpha (simple mode); \|z\|=0.66; VolGate 5->2 | allocator drop 2023-08-07: all-zero solve (n=3, viol=0.675); 30d quarantine | hedge leg B: BUY B at beta 0.69x leg A (weight 26.1%) |
| 23 | ITUB/BBD | ITUB | Short | 2023-08-07 | 2023-08-08 | +0.86 | +3,645 | +0.66 | screen 2023-07-27 v3 6/20 pass; promoted top-20 by alpha (simple mode); \|z\|=0.66; VolGate 5->2 | allocator drop 2023-08-07: all-zero solve (n=3, viol=0.675); 30d quarantine | signal leg A: z=+0.66 > 0, A rich vs B -> SELL A |
| 29 | SQM/GGB | SQM | Long | 2024-10-15 | 2024-10-16 | +0.69 | +2,067 | -0.88 | screen 2024-10-03 v3 3/29 pass; promoted top-20 by alpha (simple mode); \|z\|=0.88; VolGate 2->1 | allocator drop 2024-10-15: all-zero solve (n=2, viol=0.6); 30d quarantine | signal leg A: z=-0.88 < 0, A cheap vs B -> BUY A |
| 17 | B3SA3/ITSA4 | ITSA4 | Short | 2022-11-18 | 2022-11-21 | +0.42 | +495 | -0.76 | screen 2022-11-17 v3 4/20 pass; promoted top-20 by alpha (simple mode); \|z\|=0.76; VolGate 3->1 | allocator drop 2022-11-18: all-zero solve (n=2, viol=0.6); 30d quarantine | hedge leg B: SELL B at beta 0.80x leg A (weight -11.2%) |
| 24 | SQM/GGB | GGB | Short | 2024-08-06 | 2024-08-07 | +0.30 | +363 | -0.65 | screen 2024-07-11 v3 6/29 pass; promoted top-20 by alpha (simple mode); \|z\|=0.65; VolGate 5->4 | allocator drop 2024-08-06: all-zero solve (n=5, viol=1.125); 30d quarantine | hedge leg B: SELL B at beta 0.40x leg A (weight -10.2%) |

## Worst trades (8 losers)

| Id | Pair | Asset | Dir | Entry | Exit | Ret % | PnL $ | Entry Z | Entry reason | Exit reason | Leg thesis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | SCCO/GGB | GGB | Long | 2020-03-11 | 2020-03-12 | -17.67 | -7,633 | +0.97 | screen 2020-02-13 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=0.97 | allocator drop 2020-03-11: all-zero solve (n=3, viol=0.675); 30d quarantine | hedge leg B: BUY B at beta 0.46x leg A (weight 4.1%) |
| 4 | B3SA3/ITSA4 | B3SA3 | Long | 2020-03-10 | 2020-03-12 | -16.62 | -15,415 | -0.75 | screen 2020-02-13 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=0.75 | allocator drop 2020-03-11: all-zero solve (n=3, viol=0.675); 30d quarantine | signal leg A: z=-0.75 < 0, A cheap vs B -> BUY A |
| 11 | B3SA3/ITSA4 | ITSA4 | Long | 2020-05-18 | 2020-05-19 | -3.54 | -4,292 | +1.14 | screen 2020-05-07 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=1.14; VolGate 3->2 | allocator drop 2020-05-18: all-zero solve (n=3, viol=0.675); 30d quarantine | hedge leg B: BUY B at beta 1.00x leg A (weight 11.6%) |
| 33 | VALE/GGB | GGB | Short | 2026-07-09 | 2026-07-10 | -2.07 | -6,085 | -0.78 | screen 2026-06-25 v3 4/31 pass; promoted top-20 by alpha (simple mode); \|z\|=0.78 | allocator drop 2026-07-09: all-zero solve (n=4, viol=0.9); 30d quarantine | hedge leg B: SELL B at beta 0.55x leg A (weight -22.8%) |
| 2 | VALE/GGB | VALE | Long | 2020-02-25 | 2020-02-26 | -1.29 | -6,002 | -0.95 | screen 2020-02-13 v3 4/22 pass; promoted top-20 by alpha (simple mode); \|z\|=0.95 | allocator drop 2020-02-25: all-zero solve (n=4, viol=0.9); 30d quarantine | signal leg A: z=-0.95 < 0, A cheap vs B -> BUY A |
| 32 | VALE/GGB | VALE | Long | 2026-04-30 | 2026-05-01 | -1.13 | -1,950 | -0.87 | screen 2026-04-02 v3 5/31 pass; promoted top-20 by alpha (simple mode); \|z\|=0.87; VolGate 2->1 | allocator drop 2026-04-30: all-zero solve (n=2, viol=0.6); 30d quarantine | signal leg A: z=-0.87 < 0, A cheap vs B -> BUY A |
| 12 | PBR/A/EC | EC | Short | 2020-09-29 | 2020-09-30 | -0.54 | -2,212 | -0.68 | screen 2020-09-24 v3 5/32 pass; promoted top-20 by alpha (simple mode); \|z\|=0.68 | allocator drop 2020-09-29: all-zero solve (n=5, viol=1.125); 30d quarantine | hedge leg B: SELL B at beta 0.87x leg A (weight -39.1%) |
| 27 | SQM/GGB | GGB | Short | 2024-10-15 | 2024-10-16 | -0.03 | -50 | -0.88 | screen 2024-10-03 v3 3/29 pass; promoted top-20 by alpha (simple mode); \|z\|=0.88; VolGate 2->1 | allocator drop 2024-10-15: all-zero solve (n=2, viol=0.6); 30d quarantine | hedge leg B: SELL B at beta 0.56x leg A (weight -14.1%) |

## Comments

- Every pair leg in this run closed via `DROPPED_BY_ALLOCATOR`. Tape EXIT events: 0, and `min_holding_days` = 5 is longer than any hold, so no stop-loss / reversion / max-hold exit ever fired. The pair was de-allocated, both legs flattened the next open, and the pair was quarantined.
- Exit reason counts across all 36 rows: DROPPED_BY_ALLOCATOR 24, OVERLAY_REBALANCED_FLAT 12.
- 16 of 24 pair legs made money (win rate 67%). Winners average +3.11% (+87,539 $); losers average -5.36% (-43,639 $).
- Net pair-leg PnL +43,900 $ over 24 one-day holds, i.e. the book's whole result comes from a handful of sessions, with the top 3 legs contributing +36,977 $.
- Returns are one-session close-to-close moves on a beta-matched leg pair. Because the position is dropped at the next rebalance, no leg actually holds long enough for the spread to revert to `exit_z_spread`; PnL here is mark-to-market noise, not captured convergence.
- The 12 overlay legs are excluded from the ranking: total overlay PnL -14,637 $ (-1.15% avg). They are the residual-beta hedge in ILF, not a standalone trade.
- `entry_reason` shows the funnel that let the pair in: the cointegration screen, the promotion rule (simple mode = top-N by alpha, so the ECDF / `entry_z_base` gates are NOT applied), and the entry z. `leg_thesis` explains the side: z<0 means leg A is cheap, so A is bought and B is sold at beta ratio; z>0 mirrors it.
## How to read a row (two examples)

**Best trade, Id 8 — SCCO short vs SCCO/GGB.** On 2020-03-11 the model found the beta-adjusted spread ~1.0 standard deviations above fair value (z = +0.97), so SCCO was rich and sold as the signal leg, with the other leg hedged at beta 0.46x. The pair was closed one session later for +14,567 $ (+15.67%).

**Worst trade, Id 5 — GGB long vs SCCO/GGB.** Same mechanism, opposite outcome: the entry z was +0.97, the leg was closed one session later at -17.67% (-7,633 $). Read the two legs of one pair together: the book profits from the spread converging, not from either leg alone, and on a one-day hold it is mostly capturing that day's spread move.

**In one line:** `entry_reason` says how the pair earned a live allocation, `entry_z` says how stretched the spread was (the larger the |z|, the more the model expects it to snap back), `leg_thesis` says which side of the pair each leg is on and why, and `exit_reason` says how the position ended — here always a portfolio rebalance dropping the sleeve, not a target or stop being hit.


## CSV column reference

| Column | Meaning |
|---|---|
| `trade_id` | Row id from `trade_records.csv`; unique per fill. |
| `origin` | Where the fill came from: `PAIR <A>/<B> (leg A\|B)` = a pair sleeve leg, `MARKET OVERLAY (ILF)` = residual market-beta hedge, not a pair leg. |
| `asset` | Ticker traded. |
| `direction` | `Long` (bought) or `Short` (sold) for that leg. |
| `entry_ts` | Fill date of the entry (T+1 open after the signal date). |
| `exit_ts` | Fill date of the exit (T+1 open after the exit signal). |
| `signal_ts` | Decision date the pair was promoted/allocated (tape `ENTRY` timestamp). |
| `entry_price` | Entry fill price. |
| `exit_price` | Exit fill price. |
| `notional` | Entry notional in dollars (price x shares). |
| `pnl` | Realised P&L in dollars for the leg. |
| `return_pct` | Leg return in percent (PnL / notional). |
| `pair` | Pair the fill belongs to, `A/B`; also shown for overlay rows (the pair it hedged). |
| `pair_beta` | Hedge ratio of the pair: leg B notional / leg A notional (from the Kalman/regression fit). |
| `entry_z` | Standardised spread at entry: (spread - mean) / vol. >0 = A rich vs B, <0 = A cheap vs B. |
| `entry_alpha` | Expected annualised reversion return used to rank the pair (OU model, `(\|z\|-exit_z)*spread_vol*(252/HL)*(1-exp(-HL/tau))`). |
| `ecdf` | Percentile rank of the entry z in its rolling 63-day history (shown only when the sleeve was top-3 by weight that day). |
| `sleeve_w_pct` | Pair sleeve weight as percent of equity (allocator output). Blank for overlay rows. |
| `leg_w_pct` | Signed weight of this leg in percent of equity; opposite sign to the other leg, B scaled by beta. For overlay rows it is the overlay weight. |
| `screen` | Last cointegration re-screen before the signal: date, passes/candidates, screener version. |
| `gates` | Promotion gates that cut pairs on the signal date (VolGate / EVGate / RevDisc for that rebalance). |
| `entry_live` | Pairs live after the entry rebalance (same `Live=` count as logs.txt). |
| `entry_overlay_pct` | Market-overlay target for that rebalance, in percent of equity. |
| `exit_signal_ts` | Rebalance date whose zero-weight `PAIR_BOOK` flattened the pair. |
| `exit_live_after` | Pairs live after that exit rebalance (0 = whole book flat). |
| `exit_alloc_warn` | Allocator warning on the exit date, if any: sleeve count and constraint violation. |
| `exit_reason` | Machine label: `DROPPED_BY_ALLOCATOR`, an exit-manager reason (`STOP_LOSS`/`REVERTED`/`MAX_HOLD`/`STALE`), or `OVERLAY_REBALANCED_FLAT` for the hedge. |
| `entry_reason` | Human summary of the promotion path: screen result, promotion rule, entry \|z\|, gates. |
| `exit_reason_text` | Human summary of the exit: what dropped the pair, allocator state, quarantine. |
| `leg_thesis` | Why this leg is long or short, with the beta ratio and leg weight. |

