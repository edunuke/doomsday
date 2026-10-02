"""REGIME — independent causal regime / change-point detectors."""
from modules.regime.detector import (
    QuantileRegimeDetector, MarkovRegimeDetector, VolatilityRegimeDetector, changes,
)

REGIMES = {
    "v1": QuantileRegimeDetector,   # double-quantile tail ratio (broken for equities)
    "v2": MarkovRegimeDetector,     # 2-state Markov switching (heavier)
    "v3": VolatilityRegimeDetector, # realised-vol percentile (standard, recommended)
}

__all__ = ["QuantileRegimeDetector", "MarkovRegimeDetector", "VolatilityRegimeDetector",
           "changes", "REGIMES"]
