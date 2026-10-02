"""STOCKALPHA — stock-level (non-pair) alpha engines. v6: residual reversal."""
from modules.stockalpha.strategy import build_signal, target_weights, backtest

STOCK_ALPHAS = {"v1": target_weights}
__all__ = ["build_signal", "target_weights", "backtest", "STOCK_ALPHAS"]
