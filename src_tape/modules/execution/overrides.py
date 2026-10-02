"""
OVERRIDE ENGINE — an independent, non-neutral, transient sleeve.

An "override" is a deliberately NON market-neutral, short-horizon position that
exploits a transient opportunity which the neutral pair book cannot express. It
is completely separate from the BASE pair flow:

  * its own registry (``override_bank``), each entry individually toggled;
  * its own budget/caps (exempt from the BASE net-dollar/net-beta limits and
    from the endogenous market-reference beta hedge, which would otherwise cancel it);
  * its own lifecycle (mechanical trigger + hard time stop, shorter than BASE);
  * its own tape events, so BASE and OVERRIDE P&L are auditable separately.

A spec (bank entry), keyed by id::

    "earnings_gap_AAPL": {
      "enabled": true,
      "legs": {"AAPL": 0.02},                 # signed target weights (net may be != 0)
      "max_weight": 0.02,                      # gross budget for this override
      "trigger": {"kind": "signal", "source": "ret", "lookback": 5,
                  "mode": "reversal", "threshold": 0.03},
      "min_hold_days": 1, "max_hold_days": 2,
      "target_ret": null, "stop_ret": null
    }

Trigger kinds
-------------
"always"  : active while enabled (use with an external enable/disable).
"date"    : active within [start, end]; mark ``"manual": true`` for discretionary
            windows (never fit them to the backtest — that is look-ahead).
"signal"  : price-derived. ``source="ret"`` uses the trailing return over
            ``lookback`` days; ``mode="reversal"`` fires when the move is
            against the intended side by ``threshold``; ``mode="momentum"``
            fires when the move is with it.

All decisions use information up to and including the decision day's close; the
caller applies the T+1 execution lag, so there is no look-ahead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_DEFAULT_BUDGET = {
    "max_gross": 0.05,
    "max_single_name": 0.02,
    "max_net_dollar": 0.02,
    "max_net_beta": 0.02,
    "beta_window": 60,
}


@dataclass
class OverrideSpec:
    id: str
    enabled: bool
    legs: Dict[str, float]
    max_weight: float
    trigger: dict
    min_hold_days: int = 1
    max_hold_days: int = 2
    target_ret: Optional[float] = None
    stop_ret: Optional[float] = None

    @property
    def side(self) -> int:
        net = sum(self.legs.values())
        return 1 if net > 0 else (-1 if net < 0 else 0)


@dataclass
class OpenOverride:
    spec_id: str
    entry_date: pd.Timestamp
    entry_px: Dict[str, float]
    hold_days: int = 0


class OverrideBank:
    """Registry + lifecycle for non-neutral transient overrides."""

    def __init__(self, params: dict, prices: pd.DataFrame):
        self.params = params
        self.enabled = bool(params.get("enable_override", False))
        budget = dict(_DEFAULT_BUDGET)
        budget.update(params.get("override_budget", {}) or {})
        self.budget = budget
        self.prices = prices
        self.specs = self._load_specs(params)
        self.open: Dict[str, OpenOverride] = {}
        self.returns = prices.pct_change().fillna(0.0)
        self._beta_cache: Optional[pd.DataFrame] = None
        self._regime_states: Optional[pd.Series] = None
        self._regime_chg: Optional[pd.Series] = None
        # Lagged relative-volume panel for dislocation triggers (optional).
        self._relvol: Optional[pd.DataFrame] = None
        _vcsv = params.get("volume_csv")
        if _vcsv and os.path.exists(_vcsv):
            _v = pd.read_csv(_vcsv, index_col=0, parse_dates=True)
            _v = _v[~_v.index.duplicated()].sort_index().reindex(prices.index)
            _aw = int(params.get("adv_window_days", 20))
            self._relvol = (_v / _v.rolling(_aw, min_periods=5).mean()).shift(1)

    def _betas(self) -> pd.DataFrame:
        """Per-name market-reference beta, built lazily (only when overrides are live)."""
        if self._beta_cache is None:
            win = int(self.budget["beta_window"])
            _mkt = str(self.params.get("market_ticker", "SPY"))
            if _mkt in self.returns.columns:
                var = self.returns[_mkt].rolling(win, min_periods=max(20, win // 3)).var()
                self._beta_cache = self.returns.rolling(
                    win, min_periods=max(20, win // 3)
                ).cov(self.returns[_mkt]).div(var, axis=0).fillna(0.0)
            else:
                self._beta_cache = pd.DataFrame(0.0, index=self.prices.index,
                                                columns=self.prices.columns)
        return self._beta_cache

    # ------------------------------------------------------------------ setup
    def _load_specs(self, params: dict) -> List[OverrideSpec]:
        bank = params.get("override_bank")
        path = params.get("override_bank_path")
        if bank is None and path:
            p = path if os.path.isabs(path) else path
            if os.path.exists(p):
                bank = json.load(open(p))
        if not bank:
            return []
        specs = []
        for sid, raw in bank.items():
            specs.append(OverrideSpec(
                id=sid,
                enabled=bool(raw.get("enabled", True)),
                legs={k: float(v) for k, v in (raw.get("legs") or {}).items()},
                max_weight=float(raw.get("max_weight", 0.02)),
                trigger=raw.get("trigger", {"kind": "always"}),
                min_hold_days=int(raw.get("min_hold_days", 1)),
                max_hold_days=int(raw.get("max_hold_days", 2)),
                target_ret=raw.get("target_ret"),
                stop_ret=raw.get("stop_ret"),
            ))
        return specs

    def active_specs(self) -> List[OverrideSpec]:
        return [s for s in self.specs if s.enabled and s.legs]

    # ---------------------------------------------------------------- triggers
    def _triggered(self, spec: OverrideSpec, d: pd.Timestamp) -> bool:
        t = spec.trigger or {"kind": "always"}
        kind = t.get("kind", "always")
        if kind == "always":
            return True
        if kind == "date":
            s = pd.Timestamp(t["start"]) if t.get("start") else None
            e = pd.Timestamp(t["end"]) if t.get("end") else None
            return (s is None or d >= s) and (e is None or d <= e)
        if kind == "regime_change":
            if not hasattr(self, '_regime_chg') or self._regime_chg is None:
                return False
            return bool(self._regime_chg.get(d, False))
        if kind == "regime_state":
            if not hasattr(self, '_regime_states') or self._regime_states is None:
                return False
            want = int(t.get("state", 1))
            return int(self._regime_states.get(d, 0)) == want
        if kind == "signal":
            look = int(t.get("lookback", 5))
            i = self.prices.index.get_loc(d)
            if i < look:
                return False
            side = spec.side or 1
            # trailing return of the max-|leg| name (the opportunity instrument)
            name = max(spec.legs, key=lambda k: abs(spec.legs[k]))
            if name not in self.prices.columns:
                return False
            r = float(self.prices[name].iloc[i] / self.prices[name].iloc[i - look] - 1.0)
            thr = float(t.get("threshold", 0.03))
            mode = t.get("mode", "reversal")
            if mode == "reversal":
                return side * r <= -thr
            return side * r >= thr
        if kind == "market_shock":
            # Loud market dislocation with volume confirmation: fires when the
            # market reference's trailing |return| exceeds threshold AND its
            # lagged relative volume (vs trailing ADV) exceeds relvol_min.
            # Direction follows the move when mode == "momentum".
            look = int(t.get("lookback", 5))
            i = self.prices.index.get_loc(d)
            if i < look:
                return False
            name = str(self.params.get("market_ticker", "SPY"))
            if name not in self.prices.columns:
                return False
            side = spec.side or 1
            r = float(self.prices[name].iloc[i] / self.prices[name].iloc[i - look] - 1.0)
            thr = float(t.get("threshold", 0.04))
            if abs(r) < thr:
                return False
            _rvmin = float(t.get("relvol_min", 1.0))
            if _rvmin > 0 and self._relvol is not None:
                rv = self._relvol.at[d, name] if name in self._relvol.columns else np.nan
                if not np.isfinite(rv) or rv < _rvmin:
                    return False
            if t.get("mode", "momentum") == "momentum":
                return side * r > 0
            return True
        return False

    # ---------------------------------------------------------------- lifecycle
    def _spec_return(self, spec: OverrideSpec, op: OpenOverride, d: pd.Timestamp) -> float:
        """Gross return of the override since entry (leg-weighted)."""
        tot_w = sum(abs(v) for v in spec.legs.values()) or 1.0
        ret = 0.0
        for name, w in spec.legs.items():
            if name not in self.prices.columns:
                continue
            px0 = op.entry_px.get(name)
            if not px0:
                continue
            ret += (w / tot_w) * (float(self.prices.at[d, name]) / px0 - 1.0)
        return ret

    def step(self, d: pd.Timestamp) -> Tuple[Dict[str, float], List[dict]]:
        """Advance one day; returns (target legs, tape events)."""
        events: List[dict] = []
        if not self.enabled or not self.specs:
            return {}, events

        # exits
        just_exited: set = set()
        for sid in list(self.open):
            spec = next(s for s in self.specs if s.id == sid)
            op = self.open[sid]
            ret = self._spec_return(spec, op, d)
            reason = None
            if op.hold_days >= spec.max_hold_days:
                reason = "TIME_STOP"
            elif op.hold_days >= spec.min_hold_days:
                if spec.stop_ret is not None and ret <= float(spec.stop_ret):
                    reason = "STOP"
                elif spec.target_ret is not None and ret >= float(spec.target_ret):
                    reason = "TARGET"
                elif spec.trigger.get("kind", "always") != "always" and (
                        not self._triggered(spec, d)):
                    # Gated sleeves exit when their gate closes (e.g. 12m
                    # momentum flips, regime turns stressed). Without this, a
                    # gated tilt would persist with the gate off forever.
                    reason = "TRIGGER_OFF"
            if reason:
                events.append({"type": "OVERRIDE_EXIT", "ts": d.strftime("%Y-%m-%d"),
                               "id": sid, "reason": reason,
                               "hold_days": op.hold_days, "ret": round(ret, 6)})
                del self.open[sid]
                just_exited.add(sid)

        # entries (never re-enter on the same day an override just closed)
        for spec in self.active_specs():
            if spec.id in self.open or spec.id in just_exited:
                continue
            if self._triggered(spec, d):
                self.open[spec.id] = OpenOverride(
                    spec_id=spec.id, entry_date=d,
                    entry_px={n: float(self.prices.at[d, n])
                              for n in spec.legs if n in self.prices.columns})
                events.append({"type": "OVERRIDE_ENTRY", "ts": d.strftime("%Y-%m-%d"),
                               "id": spec.id, "legs": dict(spec.legs),
                               "max_hold_days": spec.max_hold_days})

        # age open overrides (after today's decisions)
        for op in self.open.values():
            op.hold_days += 1

        raw = self._raw_legs()
        capped, notes = self._apply_budget(raw, d)
        return capped, events

    # ------------------------------------------------------------------ sizing
    def _raw_legs(self) -> Dict[str, float]:
        raw: Dict[str, float] = {}
        for sid, op in self.open.items():
            spec = next(s for s in self.specs if s.id == sid)
            tot = sum(abs(v) for v in spec.legs.values()) or 1.0
            for name, w in spec.legs.items():
                raw[name] = raw.get(name, 0.0) + (w / tot) * spec.max_weight
        return raw

    def _apply_budget(self, raw: Dict[str, float], d: pd.Timestamp):
        if not raw:
            return {}, []
        notes = []
        legs = dict(raw)
        # single-name cap
        single = self.budget["max_single_name"]
        for k in list(legs):
            if abs(legs[k]) > single:
                legs[k] = np.sign(legs[k]) * single
        # gross cap
        gross = sum(abs(v) for v in legs.values())
        if gross > self.budget["max_gross"]:
            f = self.budget["max_gross"] / gross
            legs = {k: v * f for k, v in legs.items()}
            notes.append(f"gross scaled {gross:.3f}->{self.budget['max_gross']:.3f}")
        # net dollar cap
        net = sum(legs.values())
        if abs(net) > self.budget["max_net_dollar"]:
            f = self.budget["max_net_dollar"] / abs(net)
            legs = {k: v * f for k, v in legs.items()}
            notes.append(f"net$ scaled {net:+.3f}")
        # net beta cap (per-name SPY beta)
        if str(self.params.get("market_ticker", "SPY")) in self.prices.columns:
            b = self._betas().loc[d] if d in self._betas().index else None
            if b is not None:
                net_beta = sum(legs[k] * float(b.get(k, 0.0) or 0.0) for k in legs)
                if abs(net_beta) > self.budget["max_net_beta"]:
                    f = self.budget["max_net_beta"] / abs(net_beta)
                    legs = {k: v * f for k, v in legs.items()}
                    notes.append(f"net beta scaled {net_beta:+.3f}")
        legs = {k: v for k, v in legs.items() if abs(v) > 1e-12}
        return legs, notes

    def summary(self) -> dict:
        return {"enabled": self.enabled, "n_specs": len(self.specs),
                "n_open": len(self.open), "budget": self.budget}

    def set_regime(self, states: pd.Series, chg: pd.Series) -> None:
        """Inject a causal regime state/change series for regime-driven triggers."""
        self._regime_states = states
        self._regime_chg = chg