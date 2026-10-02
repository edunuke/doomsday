"""ALPHA — orthogonal alpha sources & augmented alpha-cube stages."""
from modules.alpha.sources import (
    dispersion_series, dispersion_z, sector_resid_z, ou_scale,
)
from modules.alpha.stage import AlphaCubeStageV5

ALPHA_STAGES = {
    "v5": AlphaCubeStageV5,   # base cube + orthogonal sources / dispersion tilt
}

__all__ = ["dispersion_series", "dispersion_z", "sector_resid_z", "ou_scale",
           "AlphaCubeStageV5", "ALPHA_STAGES"]
