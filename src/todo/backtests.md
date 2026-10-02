# Backtest Roadmap — Market Distress & Regime Testing (per-period windows)

A pairs/mean-reversion engine must be validated per regime: it can look great in
calm dispersion and blow up when correlations spike to 1.0 during panic.

**Strategy:** download **one focused CSV per period** (≤ 1 year each) into
`data/distress/`, then point a run at it via a per-window config override.
No code changes needed between windows — only data.

---

## 0. How downloads work now

`download_data.py` gained a custom-window mode. Two ways to invoke it:

```bash
# Standalone (recommended for bulk period downloads):
python -m modules.data.download_data window \
    --start 2025-02-19 --end 2025-08-08 --output data/distress/tariff_2025.csv

# Through the pipeline (runs only the download stage, then exits work downstream):
./entrypoint.sh --download --start 2025-02-19 --end 2025-08-08 --output data/distress/tariff_2025.csv
```

- Output dir is auto-created; `data/distress/constituents.csv` is written once
  next to the first window (reused thereafter).
- Each file: S&P 500 current membership + SPY, adjusted closes, indexed by date.
- `--end` is exclusive (Yahoo convention); add a day for a clean boundary.

**Run a backtest against one window** — point the config at the distress file:

```bash
# copy config.json -> config_distress.json, then:
./entrypoint.sh --config config_distress.json
```

with `config_distress.json` setting:

```json
{
  "price_csv": "data/distress/tariff_2025.csv",
  "oos_price_csv": "data/distress/tariff_2025.csv",
  "lookback_days": 126
}
```

> **Important**: a window ≤ 1 year cannot support a 504-day lookback. Set
> `lookback_days` to a sensible fraction of the window (e.g. 126 for a 1-year
> window → ~6 months of in-sample, ~6 months out-of-sample), and keep
> `min_history_days` below the window length. Alternatively, download a
> longer pre-window for in-sample and a short test window for OOS (see §5).

---

## 1. MARKET DISTRESS — Bad regimes (each ≤ 1 year)

Run commands (all write to `data/distress/`):

### 1.1 Tariff Shock / "Liberation Day" (2025-02-19 → 2025-08-08)
```bash
python -m modules.data.download_data window \
    --start 2025-02-19 --end 2025-08-08 --output data/distress/tariff_2025.csv
```
S&P **-18.9%** (48 days to trough, 128 recover); VIX **>60** early Apr 2025.
Fast V-shaped shock: correlation spike + short-squeeze risk; tests ECDF/override
entry gates and stop-loss whipsaw in a 2-σ regime.

### 1.2 Yen Carry Unwind / Nikkei Shock (2024-07-16 → 2024-09-16)
```bash
python -m modules.data.download_data window \
    --start 2024-07-16 --end 2024-09-16 --output data/distress/yen_carry_2024.csv
```
S&P **-8.5%** in 20 days; VIX **23.9 → 65+** intraday Aug 5, 2024.
Single-day vol cascade; tests friction gate and overlay saturation.

### 1.3 2026 AI & Geopolitical Correction (2026-01-27 → 2026-04-01)
```bash
python -m modules.data.download_data window \
    --start 2026-01-27 --end 2026-04-01 --output data/distress/ai_geo_2026.csv
```
S&P **-9.1%** (62 days); AI-bubble fears + Iran escalation.

### 1.4 Iran-Oil Shock (2026-06-01 → 2026-08-01)
```bash
python -m modules.data.download_data window \
    --start 2026-06-01 --end 2026-08-01 --output data/distress/iran_oil_2026.csv
```
Dow **-950 pts** in one session (Jun 10, 2026); oil $67 → $111/wk.
Sector-rotation shock; tests `max_sector_exposure` / `max_net_dollar_exposure`
when a whole sector (Energy) decouples.

### 1.5 Aug 2025 Vol Spike (2025-07-28 → 2025-10-01)
```bash
python -m modules.data.download_data window \
    --start 2025-07-28 --end 2025-10-01 --output data/distress/aug_vol_2025.csv
```
VIX 15 → 20.4 (Aug 1) → ~35 (AI pullback). Low→high vol **regime transition**.

### 1.6 SVB / Regional Banking Crisis (2023-03-01 → 2023-06-01)
```bash
python -m modules.data.download_data window \
    --start 2023-03-01 --end 2023-06-01 --output data/distress/svb_2023.csv
```
Sector (Financials) liquidity event; tests `low_density_threshold` sleeve caps.

### 1.7 Rates Shock (2023-09-01 → 2023-11-01)
```bash
python -m modules.data.download_data window \
    --start 2023-09-01 --end 2023-11-01 --output data/distress/rates_2023.csv
```
10Y → 5.0%; duration-sensitive gaps (REITs, utilities).

### 1.8 2022 Bear Trough (2022-09-06 → 2023-03-01)
```bash
python -m modules.data.download_data window \
    --start 2022-09-06 --end 2023-03-01 --output data/distress/bear_2022.csv
```
Trailing leg of the -25.4% 2022 bear + early 2023 turn. Great for burn-in/
initialization behaviour and falling-knife screens.

---

## 2. MARKET RALLIES — Good regimes (each ≤ 1 year)

### 2.1 Post-tariff Recovery Rally (2025-04-08 → 2025-08-08)
```bash
python -m modules.data.download_data window \
    --start 2025-04-08 --end 2025-08-08 --output data/distress/recovery_2025.csv
```
S&P +35% off the Apr trough; VIX 60 → 15-20. High dispersion: ghost pool should
expand.

### 2.2 Post-Election Rally (2024-11-05 → 2025-02-01)
```bash
python -m modules.data.download_data window \
    --start 2024-11-05 --end 2025-02-01 --output data/distress/rally_2024.csv
```
Risk-on melt-up; Financials lead. Watch OVERRIDE z-score behaviour in trending tape.

### 2.3 Fresh-All-Time-High (2026-07-01 → 2026-09-04)
```bash
python -m modules.data.download_data window \
    --start 2026-07-01 --end 2026-09-04 --output data/distress/ath_2026.csv
```
ATH 7,713 (Aug 13 2026); post-Iran recovery; complacency end of spectrum. Note:
partially overlaps §1.4 window — keep both, they answer different questions.

### 2.4 Baseline Calm (2024-01-02 → 2024-07-01)
```bash
python -m modules.data.download_data window \
    --start 2024-01-02 --end 2024-07-01 --output data/distress/calm_2024.csv
```
Low-VIX grind; Sharpe/Sortino baseline.

---

## 3. Canonical historical periods (downloadable now — each trimmed ≤ 1 year)

### 3.1 COVID Crash (2020-02-19 → 2020-09-01)
```bash
python -m modules.data.download_data window \
    --start 2020-02-19 --end 2020-09-01 --output data/distress/covid_2020.csv
```
S&P **-34% in 23 sessions**; VIX 85; correlations→1. The gold-standard fast-crash test.

### 3.2 GFC (2008-09-15 → 2009-03-09)
```bash
python -m modules.data.download_data window \
    --start 2008-09-15 --end 2009-03-09 --output data/distress/gfc_2008.csv
```
S&P **-57%** slow grind; persistent funding/borrow stress. Worst-case slow bleed.

### 3.3 Dot-com tail (2001-06-01 → 2002-06-01)
```bash
python -m modules.data.download_data window \
    --start 2001-06-01 --end 2002-06-01 --output data/distress/dotcom_2001.csv
```
1-yr slice of the -49% secular unwind; Tech pairs decouple permanently.

### 3.4 US Downgrade (2011-07-22 → 2011-11-01)
```bash
python -m modules.data.download_data window \
    --start 2011-07-22 --end 2011-11-01 --output data/distress/usdowngrade_2011.csv
```
VIX 82% spike; 3-week crash then V-recover.

### 3.5 China Deval (2015-08-17 → 2016-02-11)
```bash
python -m modules.data.download_data window \
    --start 2015-08-17 --end 2016-02-11 --output data/distress/china_2015.csv
```
Slow bleed; persistent stress. 6 months, within the ≤1y rule.

### 3.6 Powell QT (2018-10-03 → 2019-01-01)
```bash
python -m modules.data.download_data window \
    --start 2018-10-03 --end 2019-01-01 --output data/distress/powell_2018.csv
```
"Everything selloff"; even SPY liquidity dries.

---

## 4. Pass criteria per window

| Metric | Stress pass | Rally pass |
|---|---|---|
| Net β (CAPM) | \|β\| ≤ `max_net_beta_exposure` (0.05) | same |
| R² vs SPY | < 0.5% | < 0.5% |
| Max DD | ≤ 5% | n/a |
| Max gross exposure | ≤ `max_gross_exposure` + overlay cap | ≤ cap |
| SPY overlay saturation | overlay NOT pegged at `max_spy_overlay_weight` all window | expect ~0 |
| Win rate | ≥ 45% | ≥ 50% |
| Sortino | > 0.7 | > 1.0 |
| Time in market | engine EXITS on z convergence, no forced holds | n/a |

---

## 5. Suggested experiment matrix

Download pre-windows (in-sample) + short OOS windows (out-of-sample):

| Experiment | Config deltas | Expected insight |
|---|---|---|
| `E1` baseline | full current data | reference numbers |
| `E2` tariff OOS | in-sample 2024-06→2025-02, OOS tariff window | does beta blow up OOS? |
| `E3` carry OOS | in-sample 2023-07→2024-06, OOS yen-carry | single-day VIX +65 |
| `E4` COVID | in-sample 2019, OOS covid_2020 | correlation-spike test |
| `E5` GFC | in-sample 2007, OOS gfc_2008 | slow-bleed test |
| `E6` tight stops | `stop_loss_z 2.2→1.6`, `exit_z_spread 0.2→0.3` | whipsaw at VIX 35+? |
| `E7` overlay cap | `max_spy_overlay_weight 0.55→0.30`, `max_pair_spy_beta 1.2→1.0` | neutrality when overlay saturates |
| `E8` breadth gate | `min_correlation 0.45→0.55`, `p_value_threshold 0.04→0.02` | quality vs. depth in stress |

For in-sample + short OOS, download two files per case (e.g. `covid_in_2019.csv`,
`covid_oos_2020.csv`) and set `price_csv` / `oos_price_csv` accordingly (mirror
the existing train/val pattern). Lookback note from §0 still applies.

---

## 6. Structural risks THIS engine under stress (pre-read)

1. **`stop_loss_z` = 2.2 + `exit_z_spread` = 0.20** — in a 2-σ daily regime
   (VIX 35+), z-2.2 is hit routinely → churn + fees. Consider vol-conditional stop.
2. **Kalman `delta = 1e-4`** adapts beta slowly; single-leg gaps (SMCI-style halts)
   leave beta stale for days. Test with a synthetic gap injection.
3. **SPY overlay saturation** — when `net_pair_spy_beta` exceeds cap, engine scales
   pairs DOWN to hold the overlay; in a fast crash that cuts the highest-beta pairs
   which may be exactly the ones that recover. Measure "overlay time-in-cap".
4. **Borrow feasibility** — flat 50 bps is a cost, not a locate constraint; hard-to
   -borrow names trade at 100-500 bps in stress. Validate blotter vs borrow schedule
   during §1.1 / §1.4.
5. **Lookahead discipline** — residualisation fits beta in-sample; when mixing
   pre-window + OOS window keep the split clean (see §5 note).

---

## 7. Reference sources

- S&P 500 drawdown register (2026-08-05): 2022 -25.4% (282d); 2025 tariffs -18.9%
  (48d→128d); 2024 carry -8.5% (20d→65d); 2026 Jan -9.1% (62d→78d); Oct 2025 -5.1%
- SEC DER "Demystify the Surge in VIX" — Aug 5, 2024 intraday VIX 23.9→65 mechanics
- 2025 year-end VIX recap (StockCharts): Apr 2025 VIX >60, +35% recovery May-Jul
- 2026 tracker: S&P 0.6% below ATH (Aug 13, 2026 ATH 7,713); Jun 2026 oil $67→$111
  (Strait of Hormuz)

## Execution order is in alphabetical file name
[ 1] ai_geo_2026       warmup 2021-11-08→2026-01-26   OOS 2026-01-27→2026-03-31
[ 2] ath_2026          warmup 2022-04-11→2026-06-30   OOS 2026-07-01→2026-09-03
[ 3] aug_vol_2025      warmup 2021-05-10→2025-07-25   OOS 2025-07-28→2025-09-30
[ 4] bear_2022         warmup 2018-06-18→2022-09-02   OOS 2022-09-06→2023-02-28
[ 5] calm_2024         warmup 2019-10-14→2023-12-29   OOS 2024-01-02→2024-06-28
[ 6] china_2015        warmup 2011-05-31→2015-08-14   OOS 2015-08-17→2016-02-10
[ 7] covid_2020        warmup 2015-11-30→2020-02-18   OOS 2020-02-19→2020-08-31
[ 8] dotcom_2001       warmup 1997-03-12→2001-05-31   OOS 2001-06-01→2002-05-31
[ 9] gfc_2008          warmup 2004-06-28→2008-09-12   OOS 2008-09-15→2009-03-06
[10] iran_oil_2026     warmup 2022-03-14→2026-05-29   OOS 2026-06-01→2026-07-31
[11] powell_2018       warmup 2014-07-14→2018-10-02   OOS 2018-10-03→2018-12-31
[12] rally_2024        warmup 2020-08-17→2024-11-04   OOS 2024-11-05→2025-01-31
[13] rates_2023        warmup 2019-06-12→2023-08-31   OOS 2023-09-01→2023-10-31
[14] recovery_2025     warmup 2021-01-19→2025-04-07   OOS 2025-04-08→2025-08-07
[15] svb_2023          warmup 2018-12-10→2023-02-28   OOS 2023-03-01→2023-05-31
[16] tariff_2025       warmup 2020-11-30→2025-02-18   OOS 2025-02-19→2025-08-07
[17] usdowngrade_2011  warmup 2007-05-02→2011-07-21   OOS 2011-07-22→2011-10-31
[18] yen_carry_2024    warmup 2020-04-27→2024-07-15   OOS 2024-07-16→2024-09-13


# By period
[ 1] dotcom_2001       warmup 1997-03-12→2001-05-31   OOS 2001-06-01→2002-05-31
[ 2] gfc_2008          warmup 2004-06-28→2008-09-12   OOS 2008-09-15→2009-03-06
[ 3] usdowngrade_2011  warmup 2007-05-02→2011-07-21   OOS 2011-07-22→2011-10-31
[ 4] china_2015        warmup 2011-05-31→2015-08-14   OOS 2015-08-17→2016-02-10
[ 5] powell_2018       warmup 2014-07-14→2018-10-02   OOS 2018-10-03→2018-12-31
[ 6] covid_2020        warmup 2015-11-30→2020-02-18   OOS 2020-02-19→2020-08-31
[ 7] bear_2022         warmup 2018-06-18→2022-09-02   OOS 2022-09-06→2023-02-28
[ 8] svb_2023          warmup 2018-12-10→2023-02-28   OOS 2023-03-01→2023-05-31
[ 9] rates_2023        warmup 2019-06-12→2023-08-31   OOS 2023-09-01→2023-10-31
[10] calm_2024         warmup 2019-10-14→2023-12-29   OOS 2024-01-02→2024-06-28
[11] yen_carry_2024    warmup 2020-04-27→2024-07-15   OOS 2024-07-16→2024-09-13
[12] rally_2024        warmup 2020-08-17→2024-11-04   OOS 2024-11-05→2025-01-31
[13] tariff_2025       warmup 2020-11-30→2025-02-18   OOS 2025-02-19→2025-08-07
[14] aug_vol_2025      warmup 2021-05-10→2025-07-25   OOS 2025-07-28→2025-09-30
[15] recovery_2025     warmup 2021-01-19→2025-04-07   OOS 2025-04-08→2025-08-07
[16] ai_geo_2026       warmup 2021-11-08→2026-01-26   OOS 2026-01-27→2026-03-31
[17] iran_oil_2026     warmup 2022-03-14→2026-05-29   OOS 2026-06-01→2026-07-31
[18] ath_2026          warmup 2022-04-11→2026-06-30   OOS 2026-07-01→2026-09-03