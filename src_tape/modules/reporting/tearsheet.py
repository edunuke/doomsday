"""
INVESTMENT COMMITTEE TEARSHEET ENGINE

Per-pair performance tearsheets (return, Sharpe, Sortino, max drawdown, win
rate, trade count) computed with our own pair simulation, grouped by entry
regime.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from modules.execution.portfolio_engine import run_target_weights


class CommitteeTearsheetEngine:
    """Per-pair backtest attribution for the investment committee review."""

    def __init__(self, params: dict):
        self.init_cap = params.get("initial_capital", 250_000.0)
        self.fee = params.get("exec_fee", 0.00015)
        self.borrow = (params.get("borrow_bps", 50.0) / 10000.0) / 252.0

    def generate_tearsheets(
        self,
        prices_df: pd.DataFrame,
        pair_weight_matrices: dict,
        trade_entry_log: dict,
    ) -> pd.DataFrame:
        """Build tearsheet DataFrame for every pair that traded."""
        results = []
        for pair_key, w_df in pair_weight_matrices.items():
            a, b = pair_key
            active_dates = w_df.dropna(how="all").index
            if len(active_dates) == 0:
                continue

            start = active_dates[0]
            p_oos = prices_df[[a, b]].loc[start:].copy()
            w_target = w_df.loc[start:].copy()
            if len(w_target):
                w_target.iloc[-1] = 0.0  # flatten on last day

            res = run_target_weights(
                close=p_oos, open_=p_oos, weights=w_target,
                init_cash=self.init_cap, exec_fee=self.fee,
                daily_borrow_rate=self.borrow,
            )
            net_val = res.value
            net_ret = net_val.pct_change().fillna(0.0)

            mean_ret = net_ret.mean()
            std_ret = net_ret.std()
            down_std = np.sqrt((net_ret[net_ret < 0] ** 2).mean())
            trades = res.n_trades
            closed = res.closed_trades
            wins = int((closed["PnL"] > 0).sum()) if len(closed) else 0

            results.append(
                {
                    "Strategy_Origin": trade_entry_log.get(pair_key, "UNKNOWN"),
                    "Pair_Legs": f"{a} / {b}",
                    "Total_Return_[%]": ((net_val.iloc[-1] / self.init_cap) - 1.0) * 100,
                    "Sharpe_Ratio": (mean_ret / std_ret) * np.sqrt(252)
                    if std_ret > 0 else 0.0,
                    "Sortino_Ratio": (mean_ret / down_std) * np.sqrt(252)
                    if down_std > 0 else 0.0,
                    "Max_DD_[%]": (
                        ((1 - net_val / net_val.cummax()).max()) * 100
                        if not net_val.empty else 0.0
                    ),
                    "Win_Rate_[%]": (wins / trades) * 100 if trades > 0 else 0.0,
                    "Total_Trades": trades,
                }
            )

        if not results:
            return pd.DataFrame()

        return (
            pd.DataFrame(results)
            .sort_values(["Strategy_Origin", "Sharpe_Ratio"], ascending=[True, False])
            .reset_index(drop=True)
        )
