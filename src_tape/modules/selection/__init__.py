"""SELECTION — walk-forward parameter (re)selection components."""
from modules.selection.selector import WalkForwardSelector
from modules.selection.stage import SelectionStage

SELECTORS = {
    "v1": WalkForwardSelector,   # trailing-window proxy-Sharpe re-selection
}

__all__ = ["WalkForwardSelector", "SelectionStage", "SELECTORS"]
