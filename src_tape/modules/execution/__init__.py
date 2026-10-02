"""
EXECUTION — walk-forward loop, simulation & order generation

- WalkForwardStage: day-by-day execution loop (exits, screening, SLSQP)
- PortfolioEngine: own target-percent MOO simulation (no external backtester)
- LiveMOOOrderGenerator: MOO blotter with ADV limits
"""

from modules.execution.walk_forward import WalkForwardStage, WalkForwardStageV2
from modules.execution.walk_forward_v3 import WalkForwardStageV3
from modules.execution.portfolio_engine import run_target_weights, PortfolioResult
from modules.execution.stats import compute_stats
from modules.execution.order_generator import LiveMOOOrderGenerator

__all__ = ["WalkForwardStage", "WalkForwardStageV2", "WalkForwardStageV3", "run_target_weights",
           "PortfolioResult", "compute_stats", "LiveMOOOrderGenerator"]
