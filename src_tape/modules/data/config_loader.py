"""
CONFIG LOADER
Loads run parameters from config.json with optional overrides.
Replaces inline run_params dict in the original monolithic script.
"""

import json
import os
from typing import Any, Dict


def _project_root() -> str:
    """Absolute path to the project root (src/), three levels up from
    modules/data/config_loader.py -> src/.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Config:
    """Loads and exposes run parameters from a JSON config file.

    All parameters are validated on load. Missing keys raise KeyError
    so misconfiguration fails fast rather than silently defaulting.
    """

    _BASE_DIR = _project_root()

    def __init__(self, config_path: str = None, overrides: Dict[str, Any] = None):
        if config_path is None:
            # Default config lives in src/configs/ ; fall back to src/ for
            # backwards compatibility with older layouts.
            _primary = os.path.join(self._BASE_DIR, "configs", "config.json")
            _legacy = os.path.join(self._BASE_DIR, "config.json")
            config_path = _primary if os.path.exists(_primary) else _legacy
        self._params: Dict[str, Any] = self._load(config_path)
        if overrides:
            self._params.update(overrides)
        self._normalize()

    # ------------------------------------------------------------------
    # Canonical parameter names (back-compatible aliases)
    # ------------------------------------------------------------------
    def _normalize(self) -> None:
        """Map legacy/universe-specific keys to canonical, generic names and
        inject sane defaults. Keeps every existing config bit-identical: the
        canonical key simply mirrors whatever the legacy key held.

        Canonical:
          market_ticker          -> benchmark/reference instrument column
                                    (e.g. "SPY" for the US universe, "ILF"
                                    for the LatAm universe). Default "SPY".
          max_overlay_weight     <- max_spy_overlay_weight
          max_pair_market_beta   <- max_pair_spy_beta
        """
        p = self._params
        p.setdefault("market_ticker", "SPY")

        if "max_overlay_weight" not in p:
            p["max_overlay_weight"] = p.get("max_spy_overlay_weight", 0.55)
        # Mirror canonical -> legacy so code/scripts still reading the old key
        # see the same value (prevents divergence during the transition).
        p.setdefault("max_spy_overlay_weight", p["max_overlay_weight"])

        if "max_pair_market_beta" not in p:
            p["max_pair_market_beta"] = p.get("max_pair_spy_beta", 1.20)
        p.setdefault("max_pair_spy_beta", p["max_pair_market_beta"])

    # ------------------------------------------------------------------
    # Public access
    # ------------------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._params[key]

    def __contains__(self, key: str) -> bool:
        return key in self._params

    def get(self, key: str, default: Any = None) -> Any:
        return self._params.get(key, default)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._params)

    def items(self):
        return self._params.items()

    def keys(self):
        return self._params.keys()

    def values(self):
        return self._params.values()

    def update(self, overrides: Dict[str, Any]):
        self._params.update(overrides)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------
    @staticmethod
    def _load(path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Configuration file not found: {path}")
        with open(path, "r") as f:
            return json.load(f)

    def __repr__(self) -> str:
        return f"Config({len(self._params)} params)"