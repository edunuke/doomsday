"""
StatArb modules package — functional folder structure.

    modules/
        core/           orchestration & infrastructure (orchestrator, logger)
        data/           data acquisition & reference data (loader, download, screener, config)
        processing/     statistical kernels & pair computation (utils, pair_processor, residualizer, alpha_cube)
        optimization/   portfolio construction (optimizer)
        risk/           market neutrality diagnostics (neutrality)
        execution/      walk-forward loop & simulation (walk_forward, portfolio_engine, order_generator)
        reporting/      attribution, tearsheets & experiment output (attribution, tearsheet, reporting)

Each subpackage re-exports its public API, so imports are stable:

    from modules.data import Config, DataLoaderStage
    from modules.processing import ResidualizerStage, AlphaCubeStage
    from modules.execution import WalkForwardStage
    from modules.reporting import AttributionStage, ReportingStage
"""

from typing import Any, Dict


class PipelineStage:
    """Abstract base for a pipeline execution stage.

    Each stage implements:
        __init__(self, config: Config)
        run(self, ctx: dict) -> None

    Stages mutate the shared context dict. The orchestrator iterates
    the flow list and calls run() in order.
    """

    def __init__(self, config: "Config"):
        self.config = config
        self.params = config.as_dict()

    def run(self, ctx: Dict[str, Any]) -> None:
        """Execute this stage, reading from and writing to ctx."""
        raise NotImplementedError


# Convenience re-exports for the most common imports
from modules.core import LogCapture, PipelineOrchestrator, TapeReader, TapeWriter
from modules.data import Config, DataLoaderStage, DownloadDataStage, UniverseScreener
from modules.processing import (
    AlphaCubeStage,
    ResidualizerStage,
    compute_in_sample_residualization,
    compute_sortino_ratio,
    process_pair_history_fast,
)
from modules.optimization import ConvexPortfolioOptimizer
from modules.risk import MarketNeutralityDiagnosticEngine
from modules.execution import (
    LiveMOOOrderGenerator, PortfolioResult, WalkForwardStage, compute_stats,
    run_target_weights,
)
from modules.reporting import AttributionStage, CommitteeTearsheetEngine, ReportingStage

__all__ = [
    "PipelineStage",
    # core
    "LogCapture",
    "PipelineOrchestrator",
    "TapeWriter",
    "TapeReader",
    # data
    "Config",
    "DataLoaderStage",
    "DownloadDataStage",
    "UniverseScreener",
    # processing
    "AlphaCubeStage",
    "ResidualizerStage",
    "compute_in_sample_residualization",
    "compute_sortino_ratio",
    "process_pair_history_fast",
    # optimization
    "ConvexPortfolioOptimizer",
    # risk
    "MarketNeutralityDiagnosticEngine",
    # execution
    "LiveMOOOrderGenerator",
    "PortfolioResult",
    "WalkForwardStage",
    "compute_stats",
    "run_target_weights",
    # reporting
    "AttributionStage",
    "CommitteeTearsheetEngine",
    "ReportingStage",
]