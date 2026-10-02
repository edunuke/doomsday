"""
STAT-ARB v3.2 — MAIN ENTRY POINT

Declares the pipeline as an ordered execution list (flow) and
delegates execution to PipelineOrchestrator.

Usage:
    python main.py                                                      # defaults (quiet)
    python main.py --verbose                                            # show runtime output
    python main.py --download                                           # download data first
    python main.py --download --verbose                                 # download + show output
    python main.py --max_net_beta_exposure 0.03                       # + override params
    python main.py --config path/to/config.json                         # custom config

Adding / removing stages:
    To add a stage, create a class in modules/<subfolder>/ that extends PipelineStage,
    import it here, and insert it into the flow list at the desired position.

Logging:
    All runtime output is ALWAYS captured to results/{run}/logs.txt.
    --verbose additionally mirrors it to the terminal as it runs.
"""

import argparse
import sys

from modules.core import PipelineOrchestrator
from modules.data import Config, DataLoaderStage, DownloadDataStage
from modules.processing import (
    AlphaCubeStage, AlphaCubeStageV2, AlphaCubeStageV3, AlphaCubeStageV4,
    ResidualizerStage, ResidualizerStageV2, ResidualizerStageV3, ResidualizerStageV4,
)
from modules.execution import WalkForwardStage, WalkForwardStageV2
from modules.reporting import AttributionStage, ReportingStage

# ==============================================================================
# DECLARATIVE PIPELINE FLOW
# ==============================================================================
# Each stage is a class that extends PipelineStage and implements run(ctx).
# The orchestrator instantiates them in order and calls stage.run() sequentially.
#
# DownloadDataStage is conditional: it only executes when --download is passed.
# All other stages always run.
# ==============================================================================
# Component registries — the flow is built from config so versions can be
# swapped without editing code:
#   config["components"] = {"residualizer":"v1|v2|v3",
#                           "alphacube":"v1|v2|v3",
#                           "walkforward":"v1|v2"}
COMPONENTS = {
    "residualizer": {
        "v1": ResidualizerStage,     # SPY-residualise + correlation screen
        "v2": ResidualizerStageV2,   # + variance-ratio reversion screen
        "v3": ResidualizerStageV3,   # + regime-conditional reversion screen
        "v4": ResidualizerStageV4,   # + both-regime reversion screen
    },
    "alphacube": {
        "v1": AlphaCubeStage,        # Kalman innovation z-score
        "v2": AlphaCubeStageV2,      # multi-scale ensemble z
        "v3": AlphaCubeStageV3,      # regime-scaled signal
        "v4": AlphaCubeStageV4,      # OU-implied expected-reversion signal
    },
    "walkforward": {
        "v1": WalkForwardStage,      # exits → screening → SLSQP
        "v2": WalkForwardStageV2,    # + regime entry gate
    },
}


def build_flow(components: dict) -> list:
    """Assemble the pipeline flow from a component-version mapping."""
    c = components or {}
    return [
        DownloadDataStage,       # 0. (Conditional) download S&P 500 data
        DataLoaderStage,         # 1. Load & prepare price data
        COMPONENTS["residualizer"][c.get("residualizer", "v1")],
        COMPONENTS["alphacube"][c.get("alphacube", "v1")],
        COMPONENTS["walkforward"][c.get("walkforward", "v1")],
        AttributionStage,        # Backtests, neutrality, tearsheets, MOO blotter
        ReportingStage,          # Print summary, export CSVs, plot tearsheets
    ]


# Default flow (V1 components) — used only if a config omits `components`.
flow = build_flow({"residualizer": "v1", "alphacube": "v1", "walkforward": "v1"})


def parse_cli_args():
    """Parse --config, --download, --verbose, and extra key=value overrides."""
    parser = argparse.ArgumentParser(description="StatArb v3.2 Pipeline")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to custom config.json (default: configs/config.json)")
    parser.add_argument("--download", action="store_true", default=False,
                        help="Download S&P 500 data from Yahoo Finance before running")
    parser.add_argument("--verbose", action="store_true", default=False,
                        help="Mirror runtime output to the terminal (always saved to logs.txt)")
    known, unknown = parser.parse_known_args()

    # Extra overrides (everything after known args)
    # Note: a bare '--' may appear as the argparse end-of-options separator;
    # it carries no information for us, so drop it.
    overrides = {}
    i = 0
    while i < len(unknown):
        if unknown[i] == "--":
            i += 1
            continue
        if unknown[i].startswith("--"):
            key = unknown[i].lstrip("-")
            if i + 1 < len(unknown) and not unknown[i + 1].startswith("--"):
                val = unknown[i + 1]
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
                overrides[key] = val
                i += 2
            else:
                overrides[key] = True
                i += 1
        else:
            i += 1

    return known.config, known.download, known.verbose, overrides


def main():
    config_path, do_download, verbose, overrides = parse_cli_args()

    # Inject flags into config
    overrides["download"] = do_download
    overrides["verbose"] = verbose

    # Load config
    cfg = Config(config_path=config_path, overrides=overrides)

    # Build the flow from the config's component versions (swap without code
    # changes); falls back to V1 defaults if unspecified.
    _flow = build_flow(cfg.get("components", {}) or {})
    print(f"[main] components: "
          + ", ".join(s.__name__ for s in _flow
                      if s.__name__ in [v.__name__ for d in COMPONENTS.values()
                                        for v in d.values()]), flush=True)

    # Run pipeline — config header + stage output are captured by the
    # orchestrator (mirrored to terminal only with --verbose).
    orchestrator = PipelineOrchestrator(cfg, _flow, verbose=verbose)
    orchestrator.run()

    print("\n[v3.2] Pipeline completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())