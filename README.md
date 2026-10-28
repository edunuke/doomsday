# StatArb (src_tape) — Runbook

Statistical-arbitrage pipeline: sector-matched cointegration pairs, Kalman
alpha cube, walk-forward execution, factor-neutral allocator, survivorship
compensation, and committee reporting.

This README is the operational guide: how to run the US (SPY) and LatAm (ILF)
universes, how the configs differ, and — most importantly — the **end-to-end
checklist for onboarding a brand-new universe**.

---

## 1. Layout

```
statarb-prival/
  src_tape/
    main.py                 # entry point (all runs)
    configs/                # run configs (JSON)
    configs/config_ilf*.json# LatAm (ILF) configs
    modules/                # pipeline stages
    scripts/                # data prep, sweeps, audits, survivorship, factors
    results/<N>/            # per-run outputs (csv/, imgs/, logs.txt, tape.txt)
    results/1-20/<N>/       # archived runs, banded by number
    results/21-67/<N>/
    results/69-86/<N>/
    results/_sweep/         # parameter sweeps
    data/ -> ../src/data    # SYMLINK: the real data directory
  src/data/                 # price/constituent/factor CSVs (actual files)
```

Note: `src_tape/data` is a **symlink** to `src/data`. Always treat `src/data`
as the real location; the pipeline reads `data/...` relative to `src_tape`.

---

## 2. Prerequisites

- Python 3.x. **Two Anaconda installs coexist on this machine** (see §9.1):
  `~/anaconda3/bin/python` (3.11.5 / numpy 1.26 / scipy 1.11 / pandas 2.1) and
  `/opt/anaconda3/bin/python` (3.14.6 / numpy 2.4 / scipy 1.18 / pandas 3.0).
  PATH currently resolves `python` to the 3.14 build. **Pin the interpreter
  explicitly** in every command; results are only reproducible within one
  interpreter.
- Packages: `pandas`, `numpy`, `scipy`, `statsmodels`, `joblib`, `matplotlib`,
  `yfinance` (only for downloading new data / market references).
- macOS/Linux shell.

**Execution environment (uv-managed).** The repo is now a uv project pinned to
the archive-parity stack (Python **3.11.5**, numpy 1.26.3, scipy 1.11.4,
pandas 2.1.4, statsmodels 0.14.0, joblib 1.2.0, matplotlib 3.8.0,
yfinance 0.2.58):

```
pyproject.toml      # dependencies (exact pins)
uv.lock             # fully resolved lockfile (35 packages)
.python-version     # 3.11.5
.venv/              # created by uv sync
```

Set it up once from the **repo root**, then always run through uv so PATH
cannot switch interpreters (this is what caused the earlier 3.11↔3.14 drift):

```
# from the repo root
uv sync                       # creates .venv with the locked stack

# run the pipeline (uv finds the project by walking up from src_tape)
cd src_tape
uv run python main.py --config configs/config_ilf_v4c_negvolume.json
```

`uv sync` provisions CPython 3.11.5 itself, so the stack is identical on any
machine. Verify with `uv run python -c "import numpy,scipy,pandas;print(numpy.__version__,scipy.__version__,pandas.__version__)"`.
Verified: `uv run python main.py --config results/38/config.json` reproduces
the archived result exactly (`−0.721216%`, 1526 trades).

Run everything from `src_tape/`, and pin the interpreter (see §9.1):

```
cd src_tape
PY=~/anaconda3/bin/python        # archive-parity stack (3.11.5)
# PY=/opt/anaconda3/bin/python   # current default on PATH (3.14.6)
```

---

## 3. Quick start

### US universe (S&P 500, market reference = SPY)

```
$PY main.py --config configs/config_best.json
# or the tuned reference config used for the best-known result:
$PY main.py --config results/38/config.json
```

### LatAm universe (ILF, market reference = ILF)

```
$PY main.py --config configs/config_ilf_v4c_negvolume.json   # recommended baseline
```

Other ILF variants (see §6): `config_ilf.json` (base),
`config_ilf_v2.json` (fee-honest exits), `config_ilf_v3_factors.json`
(commodity/FX residualisation), `config_ilf_v4a/v4c_volume.json`
(volume gate), `config_ilf_v5_full.json` (7-layer funnel).

### Useful flags

```
$PY main.py --config <path> --verbose          # verbose stage logging
$PY main.py --config <path> --download         # download data first (S&P downloader)
$PY main.py --config <path> --use-survivorship-model   # force synthetic panel
$PY main.py --config <path> --from-tape results/<N>/tape.txt  # replay a run
```

Outputs land in `results/<N>/` where `<N>` is the next integer. Numbering is
global across flat and banded layouts (`results/1-20/…`, `results/69-86/…`), so
new runs never reuse an archived id. Code that needs an archived run should use
`modules.core.run_paths.run_dir(N)` / `run_file(N, ...)` (or
`python scripts/mix_report.py <N>`) instead of hard-coding a path.

---

## 4. Data formats

### 4.1 Price panel (wide CSV)

- Index: dates (`YYYY-MM-DD`), one row per trading day.
- Columns: instrument tickers (one column each), values = close prices.
- **Must include the market-reference column** (e.g. `SPY` or `ILF`).
- No volume columns in the price file (volume is a separate panel, §7).

Example:

```
,ITUB,VALE,ILF
2016-09-23,10.1,4.3,27.98
...
```

### 4.2 Constituents CSV

```
Symbol,GICS Sector
ITUB,Financials
VALE,Materials
```

For sub-industry matching also provide
`Symbol,GICS Sector,GICS Sub-Industry` (the `constituents_gics.csv` shape), and
set `sector_column: "GICS Sub-Industry"` in the config.

The market-reference ticker should appear in the panel; the survivorship
generator labels non-equity overlays as `Index/Overlay` automatically.

---

## 5. Onboarding a NEW universe — start to finish

This is the required procedure. Follow it in order.

### Step 1 — Get constituents

- Obtain the index/ETF constituent list with GICS classifications.
- Required columns: `Symbol`, `GICS Sector` (and `GICS Sub-Industry` if you
  want sub-industry pair matching).
- Save to `src/data/<UNIVERSE>_constituents_gics.csv`.
- Decide the **pair-matching granularity**: `GICS Sector` (safe for small
  universes) or `GICS Sub-Industry` (needs enough names per bucket).

### Step 2 — Get / standardise the price panel

- Source daily closes for every constituent **plus the market reference**.
- Convert to the wide CSV format (§4.1):
  - dates → `YYYY-MM-DD`, tickers as plain symbols (strip venue suffixes such
    as ` US Equity`, ` MM Equity`).
  - save to `src/data/<UNIVERSE>_daily.csv`.
- If the source is a Bloomberg export (interleaved `PX_LAST`/`PX_VOLUME`
  columns, `MM/DD/YYYY` dates, `#N/A N/A`), use the existing pattern in
  `scripts/prepare_ilf.py` as a template:

```
python scripts/prepare_ilf.py     # edit the tickers/paths for your universe
```

That script: strips venue suffixes, extracts `PX_LAST`, fixes dates, removes
non-positive/`#N/A` cells, and writes the standardised panel.

### Step 3 — Choose and add the market reference

- The benchmark is **config-driven** via `market_ticker` — never hardcoded.
  - US → `"SPY"`, LatAm → `"ILF"`.
- The reference column **must exist in the price panel**. If you cannot source
  it with the panel, download it and append:

```
python - <<'PY'
import yfinance as yf, pandas as pd
px = pd.read_csv("src/data/<UNIVERSE>_daily.csv", index_col=0, parse_dates=True)
ref = yf.download("ILF", start=str(px.index.min().date()),
                  end=str(px.index.max().date() + pd.Timedelta(days=1)),
                  progress=False, auto_adjust=False)["Close"]
ref.index = pd.to_datetime(ref.index)
px["ILF"] = ref.reindex(px.index).bfill().ffill()
px.to_csv("src/data/<UNIVERSE>_daily.csv")
PY
```

- Set `market_ticker` in the config (Step 6).

### Step 4 — (Optional) Volume panel for the volume overlay

If you want the relative-volume entry gate, build a volume panel with the same
shape as the price panel:

- `src/data/<UNIVERSE>_volume.csv`
- Reference `volume_csv` in the config and set `volume_gate_enabled: true`.
- Calibrate the cut **on the data's own distribution** — this ticker tape is
  often smoothed (std of relative volume ≈ 0.06 for ILF), so multiplier cuts
  like 1.25 are unreachable. `volume_gate_max_relvol: 1.0` (i.e. exclude the
  high-volume half) was the only setting that worked for ILF.

### Step 5 — Generate the survivorship panel

Synthetic delisted names compensate survivorship bias. Two supported routes:

**(a) Let `main.py` generate it** — activate with either the config key
`use_survivorship_model: true` or the CLI flag `--use-survivorship-model`
(both work), and give the config a `universe` tag (e.g. `"ILF"`). It then
writes **universe-aware files**:

```
data/<UNIVERSE>_survivorship.csv
data/<UNIVERSE>_constituents_survivorship.csv
data/<UNIVERSE>_survivorship_meta.json     # validation sidecar
```

Legacy configs without a `universe` key keep the historical filenames
(`<price_basename>_survivorship.csv` + shared `constituents_survivorship.csv`),
so existing US artifacts are untouched.

**Calibration + reuse validation.** Point the config at a region calibration
file and a seed; a sidecar records the signature (`seed + params + price`) and
is checked before reuse:

```
"survivorship_params_file": "data/ILF_survivorship_params.json",
"survivorship_seed": 7,
"survivorship_require_explicit": false
```

- Sidecar missing → the panel is reused but a **warning** is printed
  (legacy behaviour preserved).
- Sidecar present and signature matches → validated reuse.
- Signature mismatch (seed/params changed) → **regenerate**; if
  `survivorship_require_explicit: true` it aborts instead of silently
  regenerating.
- If `price_csv` already ends in `_survivorship.csv`, generation is **skipped**
  (the config already points at a compensated panel) — no bogus
  `…_survivorship_survivorship.csv` names, no needless rewrites.

**(b) Pre-generate with a region-calibrated model** (recommended for non-US
universes; avoids the shared filename and lets you document the calibration):

```
python scripts/generate_survivorship_ilf.py     # ILF/LatAm (region-adjusted)
python scripts/generate_survivorship.py         # US baseline
```

Then point the config at the distinct files and disable implicit generation:

```
"use_survivorship_model": false,
"price_csv":  "data/ILF_survivorship.csv",
"oos_price_csv": "data/ILF_survivorship.csv",
"constituents_path": "data/ILF_constituents_survivorship.csv",
"constituents_base": "data/ILF_constituents_gics.csv"
```

Region calibration (e.g. Damodaran country risk premia) is documented in the
generated `data/<UNIVERSE>_survivorship_meta.json`.

**Important:** synthetics must never be tradeable pair legs (their generated
OU dynamics are trivially cointegratable and fabricate alpha). Keep
`exclude_synth_from_pairs: true` in every run where you intend to judge real
performance.

### Step 6 — Write the config

Copy the closest existing config and update at minimum:

| Key | Meaning |
|---|---|
| `price_csv`, `oos_price_csv` | price panel (use the survivorship panel if applicable) |
| `constituents_path` | constituents used for sectors |
| `constituents_base` | base constituents for survivorship generation |
| `sector_column` | `GICS Sector` or `GICS Sub-Industry` |
| `market_ticker` | benchmark column, e.g. `SPY` / `ILF` |
| `use_survivorship_model` | true only if you want implicit generation |
| `exclude_synth_from_pairs` | true for honest real-name performance |
| `max_overlay_weight` | market-overlay hedge cap (legacy alias `max_spy_overlay_weight`) |
| `cohort_splits` | regime cohorts for the report, e.g. `[["","2021-12-31"],["2022-01-01",""]]` |
| `universe` | universe tag for universe-aware survivorship filenames (e.g. `ILF`) |
| `survivorship_params_file` | region/sector/TTF calibration JSON for the generator |
| `survivorship_seed` | generator seed (default 7) |
| `survivorship_require_explicit` | if true, never auto-generate; require a pre-built panel |
| `components` | stage versions (see §7) |

Universe-specific behaviour must be expressed through these keys (and only
these); **never** hardcode a ticker or a market name into code. Note the tree
has no automated test suite — verification is by run-to-run determinism
(repeated identical configs must produce identical stats) and by the checks in
§5 Step 8.

### Step 7 — Run

```
python main.py --config configs/config_<UNIVERSE>.json --verbose
```

Watch the logs for:

- `[DataLoader] market reference '<MKT>' detected — using it as the market series.`
- `[DataLoader] exclude_synth_from_pairs: removed N synthetic names ...`
- `[Screen] ... candidates -> K pass`
- `--> <MKT> Overlay=...`
- **`EMPTY BOOK WARNING`** — if this fires, the run made zero trades; it is a
  gate misconfiguration, **not** a valid result. Fix before reading metrics.

### Step 8 — Validate

- `results/<N>/csv/portfolio_stats.csv` — return, Sharpe, drawdown, PF.
- `results/<N>/csv/cohort_report.csv` — per-cohort Sharpe (regime decay check).
- `results/<N>/csv/trade_records.csv` — leg-level P&L; verify no `SYN*` legs
  when `exclude_synth_from_pairs` is on.
- `results/<N>/csv/neutrality_metrics.csv` — factor neutrality.
- `results/<N>/logs.txt` — full run log.

Success bar used in this project: **per-leg gross edge up at similar or lower
turnover, and both cohorts non-negative** — not headline Sharpe alone.

### Step 9 — Sweep (optional)

```
python scripts/sweep_config.py --tag <tag> --base configs/config_<UNIVERSE>.json --set ilf1
```

Sets available in `scripts/sweep_config.py`: `oat1`, `oat2`, `oat3` (US),
`ilf1`, `ilf2` (execution knobs), `ilfvol` (volume gate), `ilffull`,
`ilffullcal` (7-layer funnel). Output: `results/_sweep/summary_<tag>.csv`.

Note: under `funnel_mode: "simple"` the promotion path bypasses
`entry_z_base`, `entry_ecdf_percentile`, `min_override_z`,
`friction_clearance_multiplier`, `ghost_truncation_quantile`,
`promote_top_n` — sweeping those produces bit-identical tapes.

---

## 6. Config inventory

| Config | Universe | Notes |
|---|---|---|
| `configs/config.json` | US | default |
| `configs/config_best.json` | US | legacy v1 allocator |
| `results/38/config.json` | US (5y) | reference "best" config, allocator v2 |
| `configs/config_ilf.json` | ILF | raw base (no SYN gate) |
| `configs/config_ilf_v2.json` | ILF | fee-honest exits (`exit_z 0.65`, `min_hold 5`) |
| `configs/config_ilf_v3_factors.json` | ILF | FX/commodity residualisation |
| `configs/config_ilf_v4c_negvolume.json` | ILF | **recommended baseline** (negative volume gate) |
| `configs/config_ilf_v5_full.json` | ILF | 7-layer funnel |
| `configs/config_ilf_v7_full_probe.json` | ILF | all gates relaxed (diagnostic) |

`config_ilf_v4a/v4b_volume.json` are threshold variants retained for the
sensitivity record.

---

## 7. Key configuration parameters

**Components** (`components` dict selects stage versions):

```
residualizer: v1|v3     alphacube: v1|v5    selection: v1
walkforward:  v1|v3     allocator: v1|v2    regime: v3
sizing: v1              screener:  v1|v3    funnel_mode: simple|full
```

**Execution / signal**

- `entry_z_base`, `exit_z_spread`, `min_holding_days`, `max_holding_days`,
  `max_holding_buffer`, `stop_loss_z` — entry/exit thresholds.
- `entry_ecdf_percentile`, `min_override_z`, `friction_clearance_multiplier`,
  `ghost_truncation_quantile`, `promote_top_n` — **full funnel only**.
- `max_hurst_exponent`, `max_half_life`, `max_slope_tstat` — **full funnel
  only** reversion gates (S&P-calibrated; re-fit for new universes).

**Market reference / overlay**

- `market_ticker` — benchmark column (`SPY`, `ILF`, ...).
- `max_overlay_weight` — overlay hedge cap (alias `max_spy_overlay_weight`).
- `max_pair_market_beta` — per-pair market-beta cap (alias `max_pair_spy_beta`).

**Residualisation**

- `residualization_mode`: `spy` (default) or `multifactor`.
- `factors_csv`, `country_map_csv` — extra factors for multifactor mode
  (see `scripts/build_factors_latam.py`).

**Volume overlay**

- `volume_csv`, `volume_gate_enabled`, `volume_gate_min_relvol`,
  `volume_gate_max_relvol`, `volume_gate_mover_window`.

**Reporting / robustness**

- `exclude_synth_from_pairs` — drop `SYN*` from the tradeable universe.
- `cohort_splits` — regime cohorts for `cohort_report.csv`.

---

## 8. Outputs

```
results/<N>/
  config.json                 # exact config used
  run_environment.json        # python/numpy/scipy/pandas, platform, tree+config hash
  requirements.txt            # pinned package set for this run's interpreter
  logs.txt                    # full runtime log
  tape.txt                    # decision tape (replayable)
  csv/
    metrics.csv               # daily portfolio metrics
    portfolio_stats.csv       # headline stats
    trade_records.csv         # leg-level fills (Asset, Direction, PnL, Fees)
    committee_report.csv      # committee summary
    neutrality_metrics.csv    # factor neutrality checks
    progressive_metrics.csv   # rolling diagnostics
    cohort_report.csv         # per-cohort Sharpe/Sortino/DD
    moo_blotter.csv           # next-open execution blotter
  imgs/                       # plots (funnel, cumulative performance)
```

---

## 9. Known caveats and traps

1. **Reproducibility — two coexisting interpreters.** This machine has TWO
   Anaconda installs, both pre-existing (dated Aug 20):

   | Interpreter | Python | numpy | scipy | pandas |
   |---|---|---|---|---|
   | `~/anaconda3/bin/python` | 3.11.5 | 1.26.3 | 1.11.4 | 2.1.4 |
   | `/opt/anaconda3/bin/python` (on PATH by default) | 3.14.6 | 2.4.6 | 1.18.0 | 3.0.3 |

   Which one runs is decided by PATH/invocation, not by any install. The
   numeric stacks differ enough (numpy 1.x→2.x, scipy 1.11→1.18) to change
   SLSQP floating-point tie-breaks, so results are reproducible **only within
   one interpreter**. Verified: rerunning `results/38/config.json` under
   `~/anaconda3/bin/python` (3.11.5) reproduces the archived result exactly
   (`−0.721216%`, 1526 trades), while `/opt/anaconda3/bin/python` (3.14.6) does
   not (`−1.239747%`, 1531 trades).

   **Always pin the interpreter explicitly** in every run, e.g.
   `~/anaconda3/bin/python main.py --config ...` (archive parity) or
   `/opt/anaconda3/bin/python main.py --config ...` (current). Every run now
   records `run_environment.json` + `requirements.txt` (§8), and pinned locks
   are provided (`requirements-lock-py311.txt`, `requirements-lock-py314.txt`).

   **Recommendation — standardize on Python 3.11.5 / numpy 1.26 / scipy 1.11 /
   pandas 2.1** for research and production: it is the stack that reproduces
   the archived results, and pandas 3.0 is a major release with behavioural
   changes. Treat numpy 2.x/3.14 as a future migration that requires a full
   re-baseline (re-run every config and re-validate), not an in-place upgrade.
   Neither stack is "more exact": numpy 2.0 changed sorting/argsort via SIMD and
   type promotion (NEP 50) — different implementations, not better precision.

2. **Survivorship filenames are universe-aware** for configs that set
   `universe` (e.g. `data/ILF_survivorship.csv` +
   `data/ILF_constituents_survivorship.csv`). Configs without a `universe` tag
   still use the legacy shared `data/constituents_survivorship.csv`; do not run
   two legacy universes in one process.

3. **Panel reuse is validated** when a sidecar meta exists (signature =
   seed + params + price). A missing sidecar triggers a warning and reuse
   (legacy), a signature mismatch triggers regeneration, and
   `survivorship_require_explicit: true` makes any invalid panel a hard error
   instead.

4. **Synthetics must not be tradeable.** Keep `exclude_synth_from_pairs: true`
   when judging performance; without it, the screen preferentially selects
   synthetic OU paths and reports fabricated alpha (observed: "+11.3%" vs a
   real-name "−3.5%").

5. **Empty book = misconfiguration.** Full-funnel gates are S&P-calibrated
   (`friction_clearance_multiplier=8` implies a 0.35 alpha floor vs ~0.09 for
   ILF). Too-tight gates produce zero trades; the reporting stage now prints an
   `EMPTY BOOK WARNING`.

6. **Thin books over-hedge.** When few pairs are promoted, the market overlay
   can exceed net exposure several-fold and dominate P&L. Watch
   `<MKT> Overlay=` lines and `max_overlay_weight`.

7. **Small-universe statistics.** 40-50 names yield few legs per year; per-leg
   edge and cohort splits are the honest metrics, not aggregate Sharpe.
   Universe breadth is the binding constraint for capacity.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `market reference 'X' not found` | reference column missing from the price panel; add it (§5 Step 3) |
| `EMPTY BOOK WARNING` / 0 trades | entry gates mis-calibrated for this universe; relax `friction_clearance_multiplier`/`max_hurst_exponent` or use `funnel_mode: simple` |
| All sweep variants identical | the swept knob is inert under the active `funnel_mode` (see §5 Step 9) |
| `ValueError: ... array ... has size N` in residualisation | string column 2-D indexing issue; the code converts to object arrays — ensure you are on current `modules/processing/*` |
| Trades involve `SYN*` names | set `exclude_synth_from_pairs: true` |
| `FileNotFoundError: .../imgs/*.png` | reporting now creates `csv/`/`imgs/` defensively; update `modules/reporting/reporting.py` if older |
| Legacy S&P config aborts with `override_legs` | v1 allocator signature now accepts it; update `modules/optimization/optimizer.py` |
```