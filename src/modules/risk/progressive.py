"""PROGRESSIVE (PER-REBALANCE) PERFORMANCE DIAGNOSTICS

Tracks cumulative performance *as the strategy rebalances* so an experiment can
be killed early if it is not "stacking wins" — i.e. if the result depends on a
single outlier period rather than consistent accumulation.

For each rebalancing period we compute:
  period_return, cumulative_return, win_rate (periods), sharpe, sortino,
  max_dd, excess_vs_rf, information_ratio, profit_factor, expectancy,
  best/worst period  and a "concentration" flag (how much of the total PnL
  comes from the single best period).

A kill rule flags the run when, after `min_periods`, the period win-rate and
Sharpe have not met their floors.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _periodize(returns: pd.Series, boundaries: pd.DatetimeIndex) -> pd.Series:
    """Compound daily returns into per-period returns at `boundaries`.

    Uses HALF-OPEN intervals (prev_edge, edge] so each trading day belongs to
    exactly one period. Closed intervals would double-count boundary
    (rebalance) days — which carry costs — materially deflating the total.
    """
    bounds = pd.DatetimeIndex(sorted(set(boundaries))).intersection(returns.index)
    if len(bounds) < 2:
        return pd.Series(dtype=float)
    # anchor the first period at the first available return day
    edges = pd.DatetimeIndex(sorted(set([returns.index[0]] + list(bounds))))
    out = {}
    for i in range(len(edges) - 1):
        mask = (returns.index > edges[i]) & (returns.index <= edges[i + 1])
        seg = returns[mask]
        if len(seg):
            out[edges[i + 1]] = float((1 + seg).prod() - 1)
    return pd.Series(out).sort_index()


def _annualised_sharpe(r: pd.Series, periods_per_year: float) -> float:
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(periods_per_year))


def progressive_metrics(
    strat_returns: pd.Series,
    rebalance_dates: pd.DatetimeIndex,
    rf_returns: Optional[pd.Series] = None,
    checkpoint_every: int = 5,
) -> pd.DataFrame:
    """Return a per-period, progressively-cumulative metrics table.

    The last column `concentration` is the share of total PnL attributable to
    the single best period — high values mean the run depends on one trade.
    """
    per = _periodize(strat_returns, rebalance_dates)
    if per.empty:
        return pd.DataFrame()
    # periods per year implied by the median spacing
    idx = per.index
    med_days = max(int(np.median(np.diff(idx.values).astype("timedelta64[D]").astype(int))), 1)
    ppy = 252.0 / med_days

    rf_per = None
    if rf_returns is not None:
        rf_per = _periodize(rf_returns, rebalance_dates)

    rows = []
    cum = 1.0
    wins = 0
    best = worst = None
    for i, (d, r) in enumerate(per.items(), start=1):
        cum *= (1 + r)
        if r > 0:
            wins += 1
        best = r if best is None else max(best, r)
        worst = r if worst is None else min(worst, r)
        so_far = per.iloc[:i]
        rf_cum = None
        ir = np.nan
        excess = np.nan
        if rf_per is not None and len(rf_per) >= i:
            rf_cum = float((1 + rf_per.iloc[:i]).prod() - 1)
            excess = (cum - 1) - rf_cum
            ex = so_far.values - rf_per.iloc[:i].values
            te = ex.std() * np.sqrt(ppy) if len(ex) > 1 else 0.0
            ir = float((ex.mean() * ppy) / te) if te > 0 else np.nan
        wins_arr = so_far[so_far > 0]
        loss_arr = so_far[so_far < 0]
        pf = float(wins_arr.sum() / abs(loss_arr.sum())) if len(loss_arr) and loss_arr.sum() != 0 else np.inf
        eq = np.cumprod(1 + so_far.values)
        dd = float((1 - eq / np.maximum.accumulate(eq)).max())
        # concentration: best period's share of sum of positive PnL
        conc = float(best / so_far[so_far > 0].sum()) if len(wins_arr) else np.nan
        rows.append({
            "period": i, "date": d, "period_return_%": r * 100,
            "cum_return_%": (cum - 1) * 100,
            "win_rate_%": wins / i * 100,
            "sharpe": _annualised_sharpe(so_far, ppy),
            "sortino": _annualised_sharpe(so_far[so_far < 0].pow(0).add(so_far).dropna(), ppy) if False else
                       (float(so_far.mean() / (np.sqrt((so_far[so_far < 0] ** 2).mean())) * np.sqrt(ppy))
                        if (so_far < 0).any() else np.nan),
            "max_dd_%": dd * 100,
            "profit_factor": pf,
            "expectancy_%": so_far.mean() * 100,
            "best_period_%": best * 100,
            "worst_period_%": worst * 100,
            "concentration": conc,
            "excess_vs_rf_%": excess * 100 if excess is not None else np.nan,
            "information_ratio": ir,
        })
    return pd.DataFrame(rows).set_index("period")


def kill_rule(
    prog: pd.DataFrame,
    min_periods: int = 20,
    min_win_rate: float = 50.0,
    min_sharpe: float = 0.0,
    max_concentration: float = 0.5,
) -> dict:
    """Evaluate an early-stop rule on the progressive table.

    Kills if, after `min_periods`, ANY of:
      * period win-rate < min_win_rate
      * Sharpe < min_sharpe
      * single best period explains > max_concentration of positive PnL
    """
    if prog is None or prog.empty or len(prog) < min_periods:
        return {"status": "TOO_EARLY", "periods": 0 if prog is None or prog.empty else len(prog),
                "reasons": []}
    last = prog.iloc[-1]
    n_periods = int(prog.index[-1])
    reasons = []
    if last["win_rate_%"] < min_win_rate:
        reasons.append(f"win_rate {last['win_rate_%']:.1f}% < {min_win_rate}%")
    if last["sharpe"] < min_sharpe:
        reasons.append(f"sharpe {last['sharpe']:.2f} < {min_sharpe}")
    if np.isfinite(last["concentration"]) and last["concentration"] > max_concentration:
        reasons.append(f"concentration {last['concentration']:.2f} > {max_concentration}")
    return {"status": "KILL" if reasons else "OK", "periods": n_periods, "reasons": reasons}


def print_progress_table(prog: pd.DataFrame, checkpoint_every: int = 5,
                         title: str = "PROGRESSIVE (PER-REBALANCE) DIAGNOSTICS") -> None:
    if prog is None or prog.empty:
        return
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)
    hdr = (f"{'per':>4} {'date':>11} {'perRet%':>9} {'cumRet%':>9} {'win%':>6} "
           f"{'Sharpe':>7} {'maxDD%':>7} {'PF':>5} {'conc':>5} {'excSOFR%':>9} {'IR':>6}")
    print(hdr)
    print("-" * 100)
    for p, row in prog.iterrows():
        if p % checkpoint_every == 0 or p == len(prog):
            print(f"{int(p):>4} {str(row['date'])[:10]:>11} {row['period_return_%']:>9.2f} "
                  f"{row['cum_return_%']:>9.2f} {row['win_rate_%']:>6.1f} {row['sharpe']:>7.2f} "
                  f"{row['max_dd_%']:>7.2f} {row['profit_factor']:>5.2f} "
                  f"{row['concentration']:>5.2f} {row['excess_vs_rf_%']:>9.2f} "
                  f"{(row['information_ratio'] if np.isfinite(row['information_ratio']) else float('nan')):>6.2f}")
    print("-" * 100)
    kr = kill_rule(prog)
    print(f"  EARLY-STOP: {kr['status']} after {kr['periods']} periods"
          + (f" | {', '.join(kr['reasons'])}" if kr["reasons"] else ""))
    print("=" * 100)


def first_kill(prog: pd.DataFrame, min_periods: int = 20, min_win_rate: float = 50.0,
               min_sharpe: float = 0.0, max_concentration: float = 0.5) -> dict:
    """Return the FIRST period at which the kill rule would have triggered.

    This is the actionable early-stop: the checkpoint to cut the experiment,
    with the cumulative return realised up to that point.
    """
    if prog is None or prog.empty:
        return {"triggered": False}
    for i in range(min_periods, len(prog) + 1):
        sub = prog.iloc[:i]
        kr = kill_rule(sub, min_periods=min_periods, min_win_rate=min_win_rate,
                       min_sharpe=min_sharpe, max_concentration=max_concentration)
        if kr["status"] == "KILL":
            row = sub.iloc[-1]
            return {"triggered": True, "period": int(sub.index[-1]),
                    "date": str(row["date"])[:10], "cum_return_%": float(row["cum_return_%"]),
                    "win_rate_%": float(row["win_rate_%"]), "sharpe": float(row["sharpe"]),
                    "reasons": kr["reasons"]}
    return {"triggered": False}


def check_floors(period_returns,
                 min_periods: int = 5,
                 max_drawdown: float = 8.0,
                 max_period_loss: float = 3.0,
                 max_loss_streak: int = 6,
                 signal_window: int = 20,
                 signal_min_periods: int = 15,
                 cum_sigma_k: float = 2.0,
                 min_sharpe: float = -1.0,
                 min_sortino: float = -1.0,
                 min_win_rate: float = None,
                 periods_per_year: float = 84.0) -> dict:
    """Noise-calibrated hybrid early-stop.

    Thresholds are expressed in units of the strategy's OWN volatility, never in
    absolute zeros. A -2-sigma loss is evidence; a -0.09% loss is noise.

      RISK (cumulative, fires after `min_periods`, any):
        * drawdown            > max_drawdown
        * single worst period > max_period_loss
        * consecutive losses  >= max_loss_streak

      SIGNAL (rolling over last `signal_window`, after `signal_min_periods`):
        * cumulative return   < -cum_sigma_k * sigma_p * sqrt(N)
        * rolling Sharpe      < min_sharpe
        * rolling Sortino     < min_sortino
        * rolling win rate    < min_win_rate   (only if set; mean-reversion
                                                legitimately runs < 50%)

    Returns None until `min_periods` have elapsed, else a metrics dict.
    """
    n = len(period_returns)
    if n < min_periods:
        return None
    r = np.asarray(period_returns, dtype=float)
    ppy = float(periods_per_year)
    K = int(min(signal_window, n))
    rw = r[-K:]
    cum = float(np.prod(1.0 + r) - 1.0)
    sigma_p = float(r.std(ddof=1)) if n > 1 else 0.0
    sigma_cum = sigma_p * np.sqrt(n)
    cum_floor = -cum_sigma_k * sigma_cum            # noise-scaled floor
    win = 100.0 * float((rw > 0).mean())
    sharpe = float(rw.mean() / rw.std() * np.sqrt(ppy)) if rw.std() > 0 else 0.0
    dn = rw[rw < 0]
    sortino = (float(rw.mean() / np.sqrt((dn ** 2).mean()) * np.sqrt(ppy))
               if len(dn) else float("inf"))
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    dd = float((1.0 - eq / peak).max()) * 100.0
    worst = float(r.min()) * 100.0
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        streak = max(streak, cur)
    _pos = float(rw[rw > 0].sum())
    conc = float(rw.max() / _pos) if _pos > 0 else 0.0

    risk, signal = [], []
    if dd > max_drawdown:
        risk.append(f"drawdown {dd:.2f}% > {max_drawdown}%")
    if -worst > max_period_loss:
        risk.append(f"worst period {worst:.2f}% beyond -{max_period_loss}%")
    if streak >= max_loss_streak:
        risk.append(f"loss streak {streak} >= {max_loss_streak}")
    if n >= signal_min_periods:
        if cum < cum_floor:
            signal.append(f"cum {cum*100:.2f}% < -{cum_sigma_k}sigma "
                          f"({cum_floor*100:.2f}%)")
        if sharpe < min_sharpe:
            signal.append(f"roll{K} Sharpe {sharpe:.2f} < {min_sharpe}")
        if sortino < min_sortino:
            signal.append(f"roll{K} Sortino {sortino:.2f} < {min_sortino}")
        if min_win_rate is not None and win < min_win_rate:
            signal.append(f"roll{K} win {win:.1f}% < {min_win_rate}%")

    reasons = risk + signal
    layer = "RISK" if risk else ("SIGNAL" if signal else None)
    return {"periods": n, "cum_return_%": cum * 100, "win_rate": win,
            "sharpe": sharpe, "sortino": sortino, "window": K,
            "cum_floor_%": cum_floor * 100, "sigma_p_%": sigma_p * 100,
            "drawdown_%": dd, "worst_period_%": worst, "loss_streak": streak,
            "concentration": conc, "kill": len(reasons) > 0,
            "layer": layer, "reasons": reasons}
