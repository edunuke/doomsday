"""ALLOCATION — portfolio construction components (independent of the optimizer')."""
from modules.optimization.optimizer import ConvexPortfolioOptimizer
from modules.allocation.factor_allocator import FactorNeutralAllocator

ALLOCATORS = {
    "v1": ConvexPortfolioOptimizer,   # sleeve SLSQP, heuristic beta overlay
    "v2": FactorNeutralAllocator,     # + ex-ante factor neutrality constraints
}

__all__ = ["ConvexPortfolioOptimizer", "FactorNeutralAllocator", "ALLOCATORS"]
