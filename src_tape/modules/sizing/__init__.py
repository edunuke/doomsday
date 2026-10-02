"""SIZING — independent leverage/exposure-scaling components."""
from modules.sizing.sizing import NoSizing, VolTargetSizing

SIZERS = {
    "v1": NoSizing,         # no leverage
    "v2": VolTargetSizing,  # vol-targeted exposure, capped leverage
}

__all__ = ["NoSizing", "VolTargetSizing", "SIZERS"]
