"""
PORTFOLIO ENGINE — target-percent MOO simulation (no external backtester).

First-principles model of a daily long/short book driven by sparse target
weights:

  * ``weights[t]`` is the target fraction of portfolio value for each asset,
    decided *before* day ``t`` (the caller passes an already execution-lagged
    matrix). NaN means "no decision for this asset today" -> hold.
  * Orders fill at the day's OPEN (MOO) on a target-percent basis:
        nav_pre      = cash + shares . open_t
        target_sh    = (weights[t] * nav_pre) / open_t
        delta_sh     = target_sh - shares
        fees         = sum(|delta_sh| * open_t) * exec_fee
        cash        -= sum(delta_sh * open_t) + fees
        shares       = target_sh
  * Positions mark to the day's CLOSE; short market value accrues borrow at
    ``borrow_bps/10000/252`` per day, deducted from cash so it compounds.

Trade records: a trade is a maximal run of constant position direction. A fill
that flips direction is split into a *close* leg (flattening the old position)
and an *open* leg (establishing the new one), each priced at the fill price, so
per-trade cash flow and fees are exact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

_EPS = 1e-9


@dataclass
class PortfolioResult:
    value: pd.Series                 # net portfolio value (after borrow)
    gross_value: pd.Series           # value before borrow costs
    asset_value: pd.DataFrame        # per-asset market value at close
    gross_exposure: pd.Series        # |asset_value|.sum / net value
    fees_paid: pd.Series             # cumulative fees
    trades: pd.DataFrame             # per-trade records (may be empty)
    params: dict = field(default_factory=dict)

    @property
    def n_trades(self) -> int:
        return int(len(self.trades))

    @property
    def closed_trades(self) -> pd.DataFrame:
        return self.trades[self.trades["Status"] == "Closed"] if len(self.trades) else self.trades

    @property
    def open_trades(self) -> pd.DataFrame:
        return self.trades[self.trades["Status"] == "Open"] if len(self.trades) else self.trades


def run_target_weights(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    weights: pd.DataFrame,
    init_cash: float,
    exec_fee: float,
    daily_borrow_rate: float,
    delist_map: Optional[dict] = None,
    short_delist_haircut: float = 0.0,
    daily_cash_yield: Optional[object] = None,
) -> PortfolioResult:
    """Simulate a target-percent, long/short book over ``close.index``.

    ``delist_map`` maps ticker -> delisting Timestamp. On that date, any OPEN
    SHORT in the ticker is charged a buy-in penalty of
    ``short_delist_haircut * |shares| * pre_delist_price`` — modelling borrow
    recall / failure-to-deliver: a shorted name that implodes cannot be covered
    at ~0, so the crash gain is not fully realizable. Longs already take the
    full loss via the collapsed price. Default None/0.0 = no change.
    """
    close = close.ffill()
    open_ = open_.reindex(close.index).ffill()
    open_ = open_.where(open_.notna(), close)
    w = weights.reindex(index=close.index, columns=close.columns)

    cols = list(close.columns)
    c = close.values.astype(float)
    o = open_.values.astype(float)
    wv = w.values.astype(float)
    T, N = c.shape

    shares = np.zeros(N, dtype=float)
    cash = float(init_cash)
    fees_cum = 0.0

    value = np.zeros(T)
    gross_value = np.zeros(T)
    asset_val = np.zeros((T, N))
    fee_series = np.zeros(T)
    gross_exp = np.zeros(T)

    run: List[Optional[dict]] = [None] * N
    trades: List[dict] = []
    idx = close.index

    # delisting date -> column indices (for the short buy-in penalty)
    _delist_by_date: dict = {}
    if delist_map and short_delist_haircut > 0:
        _pos = {ts: k for k, ts in enumerate(idx)}
        for _tk, _dd in delist_map.items():
            if _tk in cols and _dd is not None and _dd in _pos:
                _delist_by_date.setdefault(_dd, []).append(cols.index(_tk))

    for t in range(T):
        ot = o[t]
        ct = c[t]
        wt = wv[t]

        nav_pre = cash + float(np.nansum(shares * ot))
        if nav_pre <= 0:
            nav_pre = float(np.nansum(shares * ot))

        active = ~np.isnan(wt) & np.isfinite(ot) & (ot > 0)
        target_sh = shares.copy()
        if active.any():
            tgt = (np.nan_to_num(wt) * nav_pre) / ot
            target_sh = np.where(active & np.isfinite(tgt), tgt, shares)

        delta = target_sh - shares
        fee_day = 0.0

        # ---- per-asset fills + trade-run bookkeeping ---------------------
        # Only touch assets whose target actually moved (on non-rebalance days
        # `active` is all-False so this loop is skipped entirely).
        for i in np.flatnonzero(delta):
            d = delta[i]
            if not np.isfinite(d):
                continue
            px = ot[i]
            old_sh = shares[i]
            new_sh = target_sh[i]
            old_dir = 1 if old_sh > _EPS else (-1 if old_sh < -_EPS else 0)
            new_dir = 1 if new_sh > _EPS else (-1 if new_sh < -_EPS else 0)
            fee = abs(d) * px * exec_fee
            fee_day += fee
            fee_open = fee_close = 0.0

            if new_dir == old_dir:
                # resize within the same direction
                r = run[i]
                if r is None and new_dir != 0:  # cannot happen, defensive
                    r = _open_run(cols[i], new_dir, idx[t], px, d, fee)
                    run[i] = r
                elif r is not None:
                    r["cashflow"] += -d * px
                    r["fees"] += fee
                    if (new_dir > 0 and d > 0) or (new_dir < 0 and d < 0):
                        r["entry_notional"] += abs(d) * px
                    r["last_ts"] = idx[t]
                continue

            # direction change (to flat or opposite): split the fill
            d_close = -old_sh                 # flattens old position
            d_open = d - d_close              # establishes new position
            notional = abs(d) * px
            if notional > 0:
                fee_close = abs(d_close) * px * exec_fee
                fee_open = abs(d_open) * px * exec_fee

            if old_dir != 0 and run[i] is not None:
                r = run[i]
                r["cashflow"] += -d_close * px
                r["fees"] += fee_close
                r["last_ts"] = idx[t]
                _finalise(trades, r, px, idx[t], "Closed")
            run[i] = None

            if new_dir != 0:
                run[i] = _open_run(cols[i], new_dir, idx[t], px, d_open, fee_open)

        shares = target_sh
        # ---- short-side delisting buy-in penalty -------------------------
        if _delist_by_date:
            for _i in _delist_by_date.get(idx[t], []):
                if shares[_i] < 0:
                    _px_prev = c[t - 1, _i] if t > 0 else c[t, _i]
                    _pen = short_delist_haircut * abs(shares[_i]) * float(_px_prev)
                    cash -= _pen
                    if run[_i] is not None:
                        run[_i]["penalty"] += _pen
        av = shares * ct
        asset_val[t] = av
        gv = float(np.nansum(av))
        borrow = float(np.nansum(np.where(av < 0, -av, 0.0))) * daily_borrow_rate
        cash += -float(np.nansum(delta * ot)) - fee_day - borrow
        # Interest credit on POSITIVE idle cash (T-bill/SOFR on uninvested
        # capital). Without this, low-turnover books are charged SOFR in the
        # benchmark comparison while their idle cash earns 0 in the sim — a
        # systematic understatement of any episodic strategy. Default None
        # keeps historical behaviour.
        if daily_cash_yield is not None and cash > 0:
            _rate = (daily_cash_yield.iloc[t] if isinstance(daily_cash_yield, pd.Series)
                     else daily_cash_yield)
            cash += cash * float(_rate)
        fees_cum += fee_day
        fee_series[t] = fee_day
        gross_value[t] = gv
        value[t] = cash + gv
        gross_exp[t] = float(np.nansum(np.abs(av))) / value[t] if value[t] != 0 else 0.0

    # Mark-to-market any open runs at the final close.
    last_px = c[-1]
    for i in range(N):
        r = run[i]
        if r is not None:
            av = float(shares[i]) * float(last_px[i])
            r["cashflow"] += av
            r["last_ts"] = idx[-1]
            _finalise(trades, r, float(last_px[i]), idx[-1], "Open")

    trades_df = pd.DataFrame(trades)
    if len(trades_df):
        trades_df = trades_df.sort_values(["Exit Timestamp", "Asset"]).reset_index(drop=True)
        trades_df["Trade Id"] = np.arange(len(trades_df))

    return PortfolioResult(
        value=pd.Series(value, index=idx, name="value"),
        gross_value=pd.Series(gross_value, index=idx, name="gross_value"),
        asset_value=pd.DataFrame(asset_val, index=idx, columns=cols),
        gross_exposure=pd.Series(gross_exp, index=idx, name="gross_exposure"),
        fees_paid=pd.Series(fee_series, index=idx).cumsum(),
        trades=trades_df,
        params={"init_cash": init_cash, "exec_fee": exec_fee,
                "daily_borrow_rate": daily_borrow_rate},
    )


def _open_run(asset: str, direction: int, ts, px: float, d_open: float, fee: float) -> dict:
    return {
        "asset": asset, "dir": direction, "entry_ts": ts, "entry_px": px,
        "entry_notional": abs(d_open) * px, "cashflow": -d_open * px,
        "fees": fee, "penalty": 0.0, "last_ts": ts,
    }


def _finalise(trades: List[dict], r: dict, exit_px: float, exit_ts, status: str) -> None:
    pnl = r["cashflow"] - r["fees"] - r.get("penalty", 0.0)
    notional = r["entry_notional"] or abs(r["entry_px"])
    trades.append({
        "Asset": r["asset"],
        "Direction": "Long" if r["dir"] > 0 else "Short",
        "Entry Timestamp": r["entry_ts"],
        "Exit Timestamp": exit_ts,
        "Entry Price": r["entry_px"],
        "Exit Price": exit_px,
        "Entry Notional": notional,
        "Fees": r["fees"], "Penalty": r.get("penalty", 0.0),
        "PnL": pnl,
        "Return": pnl / notional if notional else 0.0,
        "Status": status,
    })
