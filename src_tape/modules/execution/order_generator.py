"""
LIVE MARKET-ON-OPEN (MOO) ORDER GENERATOR

Converts target portfolio weights into actionable MOO order rows
respecting ADV participation limits, minimum order sizes, and
optional round-lot constraints.
"""

from typing import Dict

import numpy as np
import pandas as pd


class LiveMOOOrderGenerator:
    """Translates target weights into a sorted MOO order blotter."""

    def __init__(
        self,
        min_order_usd: float = 250.0,
        max_adv_part: float = 0.015,
        round_lots: bool = False,
    ):
        self.min_order_usd = min_order_usd
        self.max_adv_part = max_adv_part
        self.round_lots = round_lots

    def generate_blotter(
        self,
        target_weights: pd.Series,
        current_positions: Dict[str, int],
        latest_prices: pd.Series,
        rolling_adv_shares: pd.Series,
        portfolio_nav: float,
    ) -> pd.DataFrame:
        """Produce ordered MOO orders from target weights.

        Columns
        -------
        Ticker, Action, Order_Type, Delta_Shares, Target_Shares,
        Current_Shares, Est_Order_USD, Target_Weight_%, ADV_Participation_%, Price_Ref
        """
        blotter = []
        all_tickers = set(target_weights.index).union(current_positions.keys())

        for ticker in sorted(all_tickers):
            t_weight = float(target_weights.get(ticker, 0.0))
            price = float(latest_prices.get(ticker, np.nan))
            adv = float(rolling_adv_shares.get(ticker, 1_000_000.0))

            if np.isnan(price) or price <= 0:
                continue

            curr_shares = int(current_positions.get(ticker, 0))
            raw_target = int((t_weight * portfolio_nav) / price)
            max_delta = int(adv * self.max_adv_part)
            desired_delta = raw_target - curr_shares

            if desired_delta > max_delta:
                target_shares = curr_shares + max_delta
            elif desired_delta < -max_delta:
                target_shares = curr_shares - max_delta
            else:
                target_shares = raw_target

            if self.round_lots:
                target_shares = (target_shares // 100) * 100

            delta_shares = target_shares - curr_shares
            delta_value = delta_shares * price

            if abs(delta_value) >= self.min_order_usd and delta_shares != 0:
                blotter.append(
                    {
                        "Ticker": ticker,
                        "Action": "BUY" if delta_shares > 0 else "SELL",
                        "Order_Type": "MOO",
                        "Delta_Shares": abs(delta_shares),
                        "Target_Shares": target_shares,
                        "Current_Shares": curr_shares,
                        "Est_Order_USD": round(abs(delta_value), 2),
                        "Target_Weight_%": round(
                            (target_shares * price / portfolio_nav) * 100, 2
                        ),
                        "ADV_Participation_%": round(
                            (abs(delta_shares) / max(1.0, adv)) * 100, 3
                        ),
                        "Price_Ref": round(price, 2),
                    }
                )

        if not blotter:
            return pd.DataFrame(
                columns=[
                    "Ticker", "Action", "Order_Type", "Delta_Shares",
                    "Target_Shares", "Current_Shares", "Est_Order_USD",
                    "Target_Weight_%", "ADV_Participation_%", "Price_Ref",
                ]
            )

        return (
            pd.DataFrame(blotter)
            .sort_values("Est_Order_USD", ascending=False)
            .reset_index(drop=True)
        )