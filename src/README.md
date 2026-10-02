# StatArb v3.2 — Statistical Arbitrage Engine for S&P 500

Modular pipeline for designing, backtesting, and analysing statistical arbitrage
pairs strategies with institutional-grade risk management.

## Project Structure

Functional folder layout — related modules live together by responsibility:

```
src/
├── main.py                     # Entry point — declare pipeline flow
├── config.json                 # All run parameters
├── data/                       # Price CSVs (train / val / constituents)
├── results/                    # Experiment outputs (auto-created per run)
└── modules/
    ├── __init__.py             # PipelineStage base class + convenience re-exports
    ├── core/                   # Orchestration & infrastructure
    │   ├── orchestrator.py     #   PipelineOrchestrator — iterates flow list
    │   └── logger.py           #   LogCapture — stdout capture + logs.txt
    ├── data/                   # Data acquisition & reference data
    │   ├── config_loader.py    #   Config — JSON config + CLI overrides
    │   ├── download_data.py    #   DownloadDataStage (conditional: --download)
    │   ├── data_loader.py      #   DataLoaderStage (load CSVs, build DAG)
    │   └── screener.py         #   UniverseScreener (GICS sectors)
    ├── processing/             # Statistical kernels & pair computation
    │   ├── utils.py            #   Vectorized math kernels (Sortino, Kalman, Hurst…)
    │   ├── pair_processor.py   #   Per-pair Kalman filter processing
    │   ├── residualizer.py     #   ResidualizerStage (SPY residualisation)
    │   └── alpha_cube.py       #   AlphaCubeStage (parallel pair processing)
    ├── optimization/           # Portfolio construction
    │   └── optimizer.py        #   ConvexPortfolioOptimizer (SLSQP)
    ├── risk/                   # Risk diagnostics
    │   └── neutrality.py       #   MarketNeutralityDiagnosticEngine
    ├── execution/              # Trading loop & simulation
    │   ├── walk_forward.py     #   WalkForwardStage (execution loop)
    │   ├── backtest_engine.py  #   VectorbtBacktestEngine
    │   └── order_generator.py  #   LiveMOOOrderGenerator (MOO blotter)
    └── reporting/              # Reporting & attribution
        ├── attribution.py      #   AttributionStage (backtests, neutrality)
        ├── tearsheet.py        #   CommitteeTearsheetEngine
        └── reporting.py        #   ReportingStage (summary, plots, exports)
```

## Pipeline Flow

The pipeline is declared as an ordered list in `main.py`:

```python
flow = [
    DownloadDataStage,     # 0. (conditional) S&P 500 data download
    DataLoaderStage,       # 1. Load & prepare price data
    ResidualizerStage,     # 2. SPY-residualise, screen candidates
    AlphaCubeStage,        # 3. Parallel Kalman filter → alpha cube
    WalkForwardStage,      # 4. Walk-forward: exits → screening → SLSQP
    AttributionStage,      # 5. Backtests, neutrality, tearsheets
    ReportingStage,        # 6. Print summary, export CSVs + plots
]
```

Each stage extends `PipelineStage` and communicates via a shared `ctx` dict.
To add a new stage, create a class in the appropriate `modules/<subfolder>/`,
import it in `main.py`, and insert it into the `flow` list.

### Import conventions

```python
from modules.data import Config, DataLoaderStage, DownloadDataStage
from modules.processing import AlphaCubeStage, ResidualizerStage
from modules.execution import WalkForwardStage
from modules.reporting import AttributionStage, ReportingStage
from modules.core import PipelineOrchestrator
```

## Quick Start

### Prerequisites

Everything runs in the **`portoptv1`** conda environment, which already has all
required packages installed:

```bash
conda activate portoptv1
```

Required packages (already present in `portoptv1`):
`numpy 1.26.4`, `pandas 2.2.2`, `scipy 1.13.1`, `statsmodels 0.14.2`,
`matplotlib 3.8.4`, `joblib 1.4.2`, `vectorbt 1.0.0`, `yfinance 1.4.0`.

To recreate from scratch elsewhere:

```bash
conda create -n portoptv1 python=3.12
conda activate portoptv1
pip install numpy pandas scipy statsmodels matplotlib joblib vectorbt yfinance
```

### Run with existing data (prices already in `data/`)

Use the entrypoint script — it runs `main.py` with the `portoptv1` interpreter
directly (no need to activate/work around the dual conda install):

```bash
cd src
./entrypoint.sh                         # quiet: output captured to logs.txt only
./entrypoint.sh --verbose               # mirror all runtime output to the terminal
```

Or directly:

```bash
conda activate /Users/edunuke/anaconda3/envs/portoptv1
python main.py
```

### Download fresh S&P 500 data first

```bash
./entrypoint.sh --download
```

This fetches ~4 years of daily closes from Yahoo Finance and splits into
train / validation (252 trading days) + SPY constituents.

### Download a custom date window (regime testing)

For targeted backtests (e.g. market-distress windows), download one focused
CSV per period — each ≤ 1 year — into `data/distress/`:

```bash
# Through the pipeline:
./entrypoint.sh --download --start 2025-02-19 --end 2025-08-08 --output data/distress/tariff_2025.csv

# Or standalone (recommended for bulk downloads):
python -m modules.data.download_data window \
    --start 2025-02-19 --end 2025-08-08 --output data/distress/tariff_2025.csv

# Train/val split standalone:
python -m modules.data.download_data full
```

- `--end` is exclusive (Yahoo convention); add a day for a clean boundary.
- Output dir is auto-created; `constituents.csv` is written once next to the
  first window and reused afterwards.
- Point a backtest at the window via a per-window config (see `todo/backtests.md`).

### Override parameters

```bash
./entrypoint.sh --max_net_beta_exposure 0.03 --max_gross_exposure 3.0
```

Any `--key value` pair not consumed as a known flag is applied as a config override.
A literal `--` separator is also accepted (`./entrypoint.sh -- --max_net_beta_exposure 0.03`)
but is optional — both forms work identically.

### Custom config file

```bash
./entrypoint.sh --config my_config.json
```

### Combined

```bash
./entrypoint.sh --download --verbose --max_net_beta_exposure 0.03
```

### Logging / verbosity

- **Always**: the full runtime transcript (config header + all stage output +
  summary / reports) is saved to `results/{run}/logs.txt`.
- **`--verbose`**: additionally mirrors the same transcript to the terminal as it runs.
- Without `--verbose`, the terminal stays quiet apart from the final
  "Saved runtime log" and completion lines.

## Configuration

All parameters live in `config.json`. Key groups:

| Group | Parameters | Description |
|---|---|---|
| Data | `price_csv`, `oos_price_csv`, `constituents_path` | File paths |
| Research | `lookback_days`, `min_correlation`, `p_value_threshold` | Pair screening |
| Kalman | `kalman_delta`, `kalman_obs_noise` | Adaptive beta estimation |
| Reversion | `min_half_life`, `max_half_life`, `max_hurst_exponent` | Stationarity gates |
| Risk | `stop_loss_z`, `min_holding_days`, `max_holding_days` | Exit rules |
| Portfolio | `max_gross_exposure`, `max_net_beta_exposure`, `max_sector_exposure` | SLSQP constraints |
| Execution | `exec_fee`, `borrow_bps`, `max_adv_participation` | Cost assumptions |

## Output

Every run creates a numbered folder in `results/{run}/`:

```
results/1/
├── config.json                  ← Parameter snapshot (incl. run flags)
├── logs.txt                     ← FULL runtime transcript (always saved)
├── csv/
│   ├── metrics.csv              ← Rebalance event metrics
│   ├── committee_report.csv     ← Per-pair tearsheets
│   ├── moo_blotter.csv          ← Next-session MOO orders
│   ├── trade_records.csv        ← All executed trades
│   └── portfolio_stats.csv      ← Portfolio-level stats
└── imgs/
    ├── funnel_sizing.png        ← Sleeve allocation chart
    └── cumulative_performance.png ← Live / ghost / promoted curves
```

## Key Design Decisions

- **Pragmatism over purity**: vectorized NumPy/Pandas for math, flat SLSQP for
  allocation, no unnecessary abstraction layers.
- **Market-neutral by design**: CAPM neutrality diagnostic runs every session.
  v3.2 relaxes beta neutrality from 1 % → 5 % for meaningful allocation room.
- **Anti-fragile data handling**: NaNs, missing tickers, and failed downloads
  are handled explicitly at every stage.
- **Declarative flow**: stages are plain classes in an ordered list — easy to
  reorder, add, or skip for rapid iteration.