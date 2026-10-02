"""
PROCESSING — statistical kernels & pair computation

- Vectorized math kernels: Sortino, half-life, Hurst, Kalman, residualisation
- process_pair_history_fast: full Kalman-filtered pair history
- ResidualizerStage: market-reference residualisation + candidate pair screen
- AlphaCubeStage: parallel pair processing → alpha cube
"""

from modules.processing.utils import (
    compute_sortino_ratio,
    rolling_ecdf_fast,
    get_rolling_half_life,
    get_rolling_hurst,
    batch_kalman_filter_numpy,
    compute_in_sample_residualization,
)
from modules.processing.pair_processor import process_pair_history_fast
from modules.processing.residualizer import (
    ResidualizerStage, ResidualizerStageV2, ResidualizerStageV3, ResidualizerStageV4,
)
from modules.processing.alpha_cube import (
    AlphaCubeStage, AlphaCubeStageV2, AlphaCubeStageV3, AlphaCubeStageV4,
)

__all__ = [
    "compute_sortino_ratio",
    "rolling_ecdf_fast",
    "get_rolling_half_life",
    "get_rolling_hurst",
    "batch_kalman_filter_numpy",
    "compute_in_sample_residualization",
    "process_pair_history_fast",
    "ResidualizerStage",
    "ResidualizerStageV2",
    "ResidualizerStageV3",
    "ResidualizerStageV4",
    "AlphaCubeStage",
    "AlphaCubeStageV2",
    "AlphaCubeStageV3",
    "AlphaCubeStageV4",
]