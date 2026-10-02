"""
PORTFOLIO STATISTICS — metrics derived from a PortfolioResult.

Computes the same statistic set the reporting layer prints/exports, from the
engine's own value series and trade records (no external library).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from modules.execution.portfolio_engine import PortfolioResult


def _annualised_return(value: pd.Series, init_cash: float) -> float:
    n = len(value)
    if n == 0 or init_cash <= 0:
        return 0.0
    total = value.iloc[-1] / init_cash
    if total <= 0:
        return -1.0
    return total ** (252.0 / n) - 1.0


def compute_stats(res: PortfolioResult, max_gross: float) -> pd.Series:
    v = res.value
    n = len(v)
    ret = v.pct_change().fillna(0.0)
    trades = res.trades

    total_return = (v.iloc[-1] / res.params["init_cash"] - 1.0) if n else 0.0
    ann_return = _annualised_return(v, res.params["init_cash"])

    # Drawdown + duration
    peak = v.cummax()
    dd = 1.0 - v / peak.replace(0.0, np.nan)
    max_dd = float(dd.max()) if n else 0.0
    max_dd_duration = pd.Timedelta(0)
    if n and max_dd > 0:
        trough = dd.idxmax()
        peak_date = v.loc[:trough].idxmax()
        max_dd_duration = trough - peak_date

    closed = res.closed_trades
    opened = res.open_trades

    if len(closed):
        wins = closed[closed["PnL"] > 0]
        losses = closed[closed["PnL"] <= 0]
        win_rate = len(wins) / len(closed) * 100.0
        best = closed["Return"].max() * 100.0
        worst = closed["Return"].min() * 100.0
        avg_win = wins["Return"].mean() * 100.0 if len(wins) else 0.0
        avg_loss = losses["Return"].mean() * 100.0 if len(losses) else 0.0
        gross_win = wins["PnL"].sum()
        gross_loss = abs(losses["PnL"].sum())
        pf = gross_win / gross_loss if gross_loss > 0 else np.inf
        expectancy = closed["PnL"].mean()
        win_dur = (wins["Exit Timestamp"] - wins["Entry Timestamp"]).mean() if len(wins) else pd.Timedelta(0)
        loss_dur = (losses["Exit Timestamp"] - losses["Entry Timestamp"]).mean() if len(losses) else pd.Timedelta(0)
    else:
        win_rate = best = worst = avg_win = avg_loss = 0.0
        pf = expectancy = 0.0
        win_dur = loss_dur = pd.Timedelta(0)

    open_pnl = float(opened["PnL"].sum()) if len(opened) else 0.0

    std = ret.std()
    sharpe = float(ret.mean() / std * np.sqrt(252.0)) if std > 0 else 0.0
    downside = ret[ret < 0]
    dstd = float(np.sqrt((downside ** 2).mean())) if len(downside) else 0.0
    sortino = float(ret.mean() / dstd * np.sqrt(252.0)) if dstd > 0 else 0.0
    calmar = float(ann_return / max_dd) if max_dd > 0 else 0.0
    gains = ret[ret > 0].sum()
    losses_sum = abs(ret[ret < 0].sum())
    omega = float(gains / losses_sum) if losses_sum > 0 else np.inf

    rows = {
        "Start": v.index[0] if n else pd.NaT,
        "End": v.index[-1] if n else pd.NaT,
        "Period": pd.Timedelta(v.index[-1] - v.index[0]) if n else pd.Timedelta(0),
        "Start Value": res.params["init_cash"],
        "End Value": float(v.iloc[-1]) if n else res.params["init_cash"],
        "Total Return [%]": total_return * 100.0,
        "Max Gross Exposure [%]": float(max_gross * 100.0),
        "Total Fees Paid": float(res.fees_paid.iloc[-1]) if n else 0.0,
        "Max Drawdown [%]": max_dd * 100.0,
        "Max Drawdown Duration": max_dd_duration,
        "Total Trades": int(res.n_trades),
        "Total Closed Trades": int(len(closed)),
        "Total Open Trades": int(len(opened)),
        "Open Trade PnL": open_pnl,
        "Win Rate [%]": win_rate,
        "Best Trade [%]": best,
        "Worst Trade [%]": worst,
        "Avg Winning Trade [%]": avg_win,
        "Avg Losing Trade [%]": avg_loss,
        "Avg Winning Trade Duration": win_dur,
        "Avg Losing Trade Duration": loss_dur,
        "Profit Factor": pf,
        "Expectancy": expectancy,
        "Sharpe Ratio": sharpe,
        "Calmar Ratio": calmar,
        "Omega Ratio": omega,
        "Sortino Ratio": sortino,
    }
    return pd.Series(rows)
