"""Launch a walk-forward backtest from a config path.

Usage:
    python run_long.py [config_path]   # default: config_long.json
"""
import sys

sys.path.insert(0, "/Users/edunuke/Desktop/statarb-prival/src")

from modules.core import PipelineOrchestrator
from modules.data import Config, DataLoaderStage, DownloadDataStage
from modules.processing import AlphaCubeStage, ResidualizerStage
from modules.execution import WalkForwardStage
from modules.reporting import AttributionStage, ReportingStage

CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else "config_long.json"

flow = [
    DownloadDataStage,
    DataLoaderStage,
    ResidualizerStage,
    AlphaCubeStage,
    WalkForwardStage,
    AttributionStage,
    ReportingStage,
]

cfg = Config(config_path=CONFIG_PATH, overrides=None)
cfg.update({"download": False, "verbose": False})

orchestrator = PipelineOrchestrator(cfg, flow, verbose=False)
orchestrator.run()
print(f"\n[LONG] Pipeline completed ({CONFIG_PATH}).", flush=True)