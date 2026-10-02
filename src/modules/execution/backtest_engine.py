"""
VECTORBT BACKTEST ENGINE

Runs a walk-forward backtest using vectorbt's Portfolio.from_orders
with execution fees, short borrow costs, and open-price fills.
"""

from typing import Tuple

import numpy as np
import pandas as pd
import vectorbt as vbt


class VectorbtBacktestEngine:
    """Thin wrapper around vectorbt for net portfolio simulation."""

    def __init__(self, params: dict):
        self.initial_capital = params.get("initial_capital", 250_000.0)
        self.exec_fee = params.get("exec_fee", 0.00015)
        self.daily_borrow_rate = (params.get("borrow_bps", 50.0) / 10000.0) / 252.0

    def run_backtest(
        self,
        close_prices_df: pd.DataFrame,
        open_prices_df: pd.DataFrame,
        weights_df: pd.DataFrame,
    ) -> Tuple["vbt.Portfolio", pd.Series, pd.Series]:
        """Simulate portfolio from sparse target-percent weights.

        Returns
        -------
        pf_gross : vbt.Portfolio
        net_portfolio_value : pd.Series — value after borrow costs
        true_gross_exposure : pd.Series — gross leverage at each step
        """
        tradable = weights_df.columns.intersection(close_prices_df.columns)
        p_close = close_prices_df[tradable].ffill()
        p_open = open_prices_df[tradable].ffill()
        sparse_w = weights_df[tradable]

        pf_gross = vbt.Portfolio.from_orders(
            close=p_close,
            price=p_open,
            size=sparse_w,
            size_type="targetpercent",
            group_by=True,
            cash_sharing=True,
            init_cash=self.initial_capital,
            fees=self.exec_fee,
            # vectorbt 1.0 infers `index.inferred_freq` -> 'B' (business day)
            # on perfectly regular trading-day indices, then crashes calling
            # pd.Timedelta(1, unit='B'). Pin a valid timedelta unit to bypass.
            freq="D",
        )
        asset_values = pf_gross.asset_value(group_by=False)
        net_value = pf_gross.value() - (
            asset_values.where(asset_values < 0, 0).abs().sum(axis=1)
            * self.daily_borrow_rate
        ).cumsum()
        gross_exp = (
            asset_values.abs().sum(axis=1) / net_value.replace(0.0, np.nan)
        ).fillna(0.0)

        return pf_gross, net_value, gross_exp