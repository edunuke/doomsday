"""SCREENER — pair-admission tests, selectable by component version."""
from modules.screener.engle_granger import coint_screen
from modules.screener.stat_screener import stat_screen
from modules.screener.ranked_screen import ranked_screen
from modules.screener.ranked_screen_v4 import ranked_screen_v4
from modules.screener.fdr_screen import fdr_screen

SCREENERS = {
    "v1": coint_screen,     # Engle-Granger p-value (baseline)
    "v2": stat_screen,      # Johansen + Hurst + half-life (legacy 7-step)
    "v3": ranked_screen,    # ranked multi-stage: cheap screen -> rank -> Johansen top-K
    "v4": ranked_screen_v4, # + hard score floor & Johansen-margin floor (turnover-aware)
    "v5": fdr_screen,       # v3 + BH-FDR multiple-testing control + persistence
}

__all__ = ["coint_screen", "stat_screen", "ranked_screen", "ranked_screen_v4",
           "fdr_screen", "SCREENERS"]
