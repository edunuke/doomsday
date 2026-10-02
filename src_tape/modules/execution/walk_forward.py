"""
WALK-FORWARD STAGE

The core execution loop: exit management → cointegration re-screening →
ghost pool → promotion → SLSQP rebalance.

Loops day-by-day over the out-of-sample period, managing active pair
positions, applying stops / holding-period limits, maintaining a
cooldown quarantine, and periodically running the ConvexPortfolioOptimizer.

Context produced
----------------
master_weights    : pd.DataFrame — live portfolio weights (all cols)
ghost_weights     : pd.DataFrame — equal-weight ghost benchmark
promoted_weights  : pd.DataFrame — equal-weight promoted benchmark
pair_weight_matrices : dict      — per-pair weight DataFrames
trade_entry_log   : dict         — entry regime per pair
metrics_history   : list[dict]   — rebalance-event metrics
active_pairs      : dict         — current live pair memory
cooldown_tracker  : dict         — quarantine counters
"""

import json
import os

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from modules import PipelineStage
from modules.core.tape import TapeWriter, config_sha
from modules.optimization.optimizer import ConvexPortfolioOptimizer
from modules.processing.utils import compute_in_sample_residualization
from modules.processing.pair_processor import process_pair_history_fast
from modules.risk.progressive import check_floors


def _emit_ghost(writer, etype, ts, frame, key="Z_Spread"):
    """Emit a sparse ghost/promoted book from a records DataFrame."""
    targets = {}
    if frame is not None and len(frame):
        alloc = 1.0 / len(frame)
        for _, r in frame.iterrows():
            sign = -1.0 if r[key] > 0 else 1.0
            a, b = r["Asset_A"], r["Asset_B"]
            targets[a] = targets.get(a, 0.0) + sign * alloc
            targets[b] = targets.get(b, 0.0) - sign * alloc * r["Beta"]
    targets = {k: v for k, v in targets.items() if abs(v) > 1e-12}
    writer.event({"type": etype, "ts": ts, "targets": targets})


class WalkForwardStage(PipelineStage):
    """Stage 4: walk-forward execution loop with exit mgmt, screening, rebalance."""

    def run(self, ctx: dict) -> None:
        params = self.params
        prices = ctx["prices_full"]
        dag = ctx["dag_df"]
        res_dag = ctx["res_dag_df"]
        # In multifactor mode the traded spread is computed on the residual
        # log-price panel (already log-level); otherwise on raw log-prices.
        trade_dag = (
            ctx.get("trade_dag_df")
            if params.get("residualization_mode", "spy") == "multifactor"
            else None
        )
        alpha = ctx["alpha_cube"]
        sectors = ctx["sectors"]
        start_idx = ctx["start_idx"]
        candidate_pairs = ctx["candidate_pairs"]

        # ---- COMPONENT-SELECTED ALLOCATOR -----------------------------------
        _comp = params.get("components", {}) or {}
        _alloc_ver = _comp.get("allocator", "v1")
        _regime_ver = _comp.get("regime", "")
        _sizing_ver = _comp.get("sizing", "v1")
        _screener_ver = _comp.get("screener", "v1")
        from modules.screener import SCREENERS as _SCREENERS
        _screen_fn = _SCREENERS.get(_screener_ver, _SCREENERS["v1"])
        if _screener_ver != "v1":
            print(f"[Screener] {_screener_ver}", flush=True)

        from modules.allocation import ALLOCATORS as _ALLOCATORS
        from modules.regime import REGIMES as _REGIMES, changes as _regime_changes
        if _alloc_ver == "v2":
            from modules.factors import FactorModel
            _factor_warn = []
            _factor_model = FactorModel(params, prices,
                                        ctx.get("sectors_gics", sectors), _factor_warn)
            for _w in _factor_warn:
                print(f"[Factors] {_w}", flush=True)
            engine_alloc = _ALLOCATORS["v2"](params, _factor_model)
        else:
            engine_alloc = _ALLOCATORS["v1"](params)
            _factor_model = None

        # Regime detector (independent, optional)
        _regime_states = None
        _regime_chg = None
        if _regime_ver and _regime_ver in _REGIMES:
            _regime_states = _REGIMES[_regime_ver](params).states(
                ctx.get("market_ret", pd.Series(0, index=prices.index)))
            _regime_chg = _regime_changes(_regime_states)
            print(f"[Regime] {_regime_ver}: stressed="
                  f"{int(_regime_states.sum())}/{len(_regime_states)} days "
                  f"({int(_regime_chg.sum())} transitions)", flush=True)

        # Sizing component (independent leverage / exposure scaling)
        from modules.sizing import SIZERS as _SIZERS
        _sizer = _SIZERS.get(_sizing_ver, _SIZERS["v1"])(params)
        if _sizing_ver != "v1":
            print(f"[Sizing] {_sizing_ver}: target_vol={params.get('sizing_target_vol')} "
                  f"max_lev={params.get('sizing_max_leverage')} mode={params.get('sizing_mode')}",
                  flush=True)

        # ---- DECISION TAPE ------------------------------------------------
        # The tape is the decision engine's ONLY durable output. Downstream
        # consumers never touch the weight matrices; they replay these events.
        out_dir = ctx.get("_output_dir") or "."
        tape_path = os.path.join(out_dir, "tape.txt")
        _tape_on = bool(params.get("tape_enabled", True))
        writer = TapeWriter(tape_path) if _tape_on else None
        if writer is not None:
            writer.header({
                "run": os.path.basename(os.path.abspath(out_dir)),
                "ts": str(prices.index[start_idx].strftime("%Y-%m-%d")),
                "config_sha": config_sha(params),
                "universe": params.get("price_csv", "") + "+" + params.get("oos_price_csv", ""),
                "n_tickers": int(prices.shape[1]),
                "start_idx": int(start_idx),
                "initial_capital": float(params["initial_capital"]),
                "exec": "T+1", "fill": "MOO",
                "fees": {"exec_fee": float(params["exec_fee"]),
                         "borrow_bps": float(params["borrow_bps"])},
            })
            ctx["tape_path"] = tape_path
            print(f"[WalkForward] Decision tape -> {tape_path}", flush=True)

        active_pairs = {}
        cooldown_tracker = {}
        _stop_strikes = {}               # consecutive STOP_LOSS count per pair

        total_steps = len(prices) - start_idx
        all_cols = prices.columns
        trade_cols = dag.columns

        # Weight matrices
        master_w = pd.DataFrame(
            np.nan, index=prices.index, columns=all_cols, dtype=np.float64
        )
        ghost_w = pd.DataFrame(
            np.nan, index=prices.index, columns=trade_cols, dtype=np.float64
        )
        promoted_w = pd.DataFrame(
            np.nan, index=prices.index, columns=trade_cols, dtype=np.float64
        )
        pair_w_mats = {}
        current_sleeve_weights = {}
        target_sleeve_weights = {}
        metrics_history = []
        cached_keys = []
        trade_entry_log = {}
        days_since_screening = 0

        # ---- RETRAIN STATE (rolling universe refresh) ----------------------
        # Every `retrain_freq_days` the candidate universe is re-derived from a
        # trailing `train_lookback_days` window: residualisation betas are
        # re-fit and the sector/correlation screen is recomputed, so newly
        # correlated pairs enter and stale ones drop out. The Kalman alpha cube
        # is extended (online, past-only) for any new pairs. Hyperparameters are
        # intentionally NOT re-optimised here — that is overfit-prone and
        # research-fixed by design.
        retrain_freq = int(params.get("retrain_freq_days", 0) or 0)
        train_lb = int(params.get("train_lookback_days", params["lookback_days"]))
        last_retrain = start_idx
        last_reset = start_idx           # hard book-reset schedule (path-dependence)
        _kill_until = -1                 # regime-change tail de-risk window end
        # Preselection set (predictive-quality filter) must also apply to every
        # RETRAIN refresh, not just the initial residualizer pass — otherwise
        # the quarterly universe rebuild re-introduces unscored pairs.
        presel_file = params.get("preselect_pairs_file")
        presel_set = None
        if presel_file and os.path.exists(presel_file):
            try:
                _sel = json.load(open(presel_file))["pairs"]
                presel_set = {tuple(p) for p in _sel}
                print(
                    f"[WalkForward] Preselection active: {len(presel_set)} pairs "
                    f"({presel_file}) applied at init + every retrain.",
                    flush=True,
                )
            except Exception as e:
                print(f"[WalkForward] Preselection load failed ({e}).", flush=True)
        sectors = ctx["sectors"]
        valid = ctx["valid_tickers"]

        # ADR-local lead-lag returns (config-gated modulation input).
        _leadlag_ret, _leadlag_country = None, {}
        if params.get("leadlag_tilt_enabled"):
            _ll = str(params.get("leadlag_local_file", "data/ILF_local_ret.csv"))
            if os.path.exists(_ll):
                _leadlag_ret = pd.read_csv(_ll, index_col=0, parse_dates=True).reindex(prices.index)
            _lmap = str(params.get("leadlag_country_file",
                                   "data/ILF_country_map.csv"))
            if os.path.exists(_lmap):
                _leadlag_country = (
                    pd.read_csv(_lmap).set_index("Symbol")["Country"].astype(str)
                    .str.strip().to_dict())

        # Volume-confirmation overlay: lagged relative volume (t-1 vs trailing
        # ADV) for pair legs. Built when the binary gate OR continuous weighting
        # is configured; None when neither is (and no panel is loaded).
        _relvol_lag = None
        if (
            (params.get("volume_gate_enabled")
             or params.get("volume_weight_mode") == "continuous")
            and "volume_df" in ctx
        ):
            _vol = ctx["volume_df"].reindex(prices.index)
            _awin = int(params.get("adv_window_days", 20))
            _relvol_lag = (_vol / _vol.rolling(_awin, min_periods=5).mean()).shift(1)

        # Residual panel drives the spread/signal; real prices drive the hedge.
        spread_panel = trade_dag if trade_dag is not None else dag
        spread_vals = spread_panel.values
        col_map = {c: idx for idx, c in enumerate(spread_panel.columns)}
        real_vals = dag.values
        real_col_map = {c: idx for idx, c in enumerate(dag.columns)}
        m_ret_arr = ctx["market_ret"].values
        spy_series = (prices[ctx["market_col"]]
                      if ctx.get("market_col") and ctx["market_col"] in prices.columns
                      else dag.mean(axis=1))
        # Config-driven reference column name (SPY for US, ILF for LatAm, ...)
        _mkt_col = ctx.get("market_col")
        sec_arr = sectors.reindex(valid).to_numpy(dtype=object)
        sec_match = sec_arr[:, None] == sec_arr[None, :]
        dates_idx = dag.index
        if retrain_freq > 0:
            print(
                f"[WalkForward] Rolling retrain every {retrain_freq}d "
                f"(train window {train_lb}d).",
                flush=True,
            )

        # Friction calibration
        exec_bps = params["exec_fee"] * 2.0 * 10000.0
        borrow_10d = params["borrow_bps"] * (10.0 / 365.0)
        friction = (exec_bps + borrow_10d) / 100.0
        adaptive_min_alpha = friction * params.get("friction_clearance_multiplier", 8.0)
        print(f"[WalkForward] Min alpha floor = {adaptive_min_alpha:.2f}%", flush=True)

        # ---- LIVE EARLY-STOP STATE ------------------------------------------
        # Mark the book to market each day so the kill rule can be evaluated at
        # every rebalance. If the rolling floors (win-rate / Sharpe /
        # concentration) are breached after `early_stop_min_periods`, the
        # strategy is flattened and stops trading for the rest of the run.
        _es_on = bool(params.get("early_stop_enabled", True))
        _es_min_p = int(params.get("early_stop_min_periods", 20))
        _es_min_cum = params.get("early_stop_min_cum_return", None)
        _es_t_threshold = float(params.get("early_stop_t_threshold", -1.0))
        _es_max_dd = float(params.get("early_stop_max_drawdown", 8.0))
        _es_max_loss = float(params.get("early_stop_max_period_loss", 5.0))
        _es_max_streak = int(params.get("early_stop_max_loss_streak", 6))
        _es_sig_min = int(params.get("early_stop_signal_min_periods", 10))
        _es_sig_win = int(params.get("early_stop_signal_window", 20))
        _es_min_sharpe = float(params.get("early_stop_min_sharpe", -1.0))
        _es_min_sortino = float(params.get("early_stop_min_sortino", -1.0))
        _es_min_win = params.get("early_stop_min_win_rate", None)
        _es_cum_k = float(params.get("early_stop_cum_sigma_k", 2.0))
        _es_max_conc = float(params.get("early_stop_max_concentration", 0.5))
        _es_rebal_days = int(params.get("early_stop_rebal_days", 5))
        _daily_ret = prices.pct_change().fillna(0.0)
        _held_w = None                 # executed weights (previous target row)
        _equity = float(params["initial_capital"])
        _equity_history = []  # for trailing drawdown override
        _period_rets = []
        _rebal_dates = []
        _last_rebal_eq = _equity
        _killed = False
        _kill_info = None
        if _es_on:
            print(
                f"[EarlyStop] ENABLED | RISK(burn-in {_es_min_p}): DD>{_es_max_dd}%, "
                f"loss>{_es_max_loss}%, streak>={_es_max_streak} | "
                f"SIGNAL(burn-in {_es_sig_min}, roll {_es_sig_win}): "
                f"cum<-{_es_cum_k}sigma, Sharpe<{_es_min_sharpe}, "
                f"Sortino<{_es_min_sortino}"
                + (f", win<{_es_min_win}%" if _es_min_win is not None else ""),
                flush=True,
            )

        # ---- REGIME ENTRY GATE (WalkForwardStageV2) -------------------------
        # Suspend NEW entries when trailing cross-sectional dispersion is below
        # a rolling percentile (unfavourable regime); exits stay live.
        _regime_on = None
        if bool(params.get("regime_gate_entries", False)):
            _dret = prices.pct_change().replace([np.inf, -np.inf], np.nan)
            _disp = _dret.std(axis=1).rolling(
                int(params.get("regime_disp_window", 21)), min_periods=10
            ).mean()
            _win = int(params.get("regime_gate_window", 252))
            _q = float(params.get("regime_gate_pctile", 0.3))
            _thr = _disp.rolling(_win, min_periods=60).quantile(_q)
            _regime_on = (_disp >= _thr).fillna(True)
            print(f"[WalkForwardV2] regime entry gate ON "
                  f"(gate opens {(1-_q)*100:.0f}% of regimes; "
                  f"trailing {win if False else _win}d q={_q})", flush=True)

        # =====================================================================
        # MAIN LOOP
        # =====================================================================
        # One persistent worker pool for both alpha-cube extension (retrain) and
        # the cointegration screen. Reusing it avoids paying loky worker startup
        # on every screen (~25x over a run).
        _pool = Parallel(n_jobs=-1, backend="loky", max_nbytes=256 * 1024)

        # ---- INDEPENDENT OVERRIDE SLEEVE ------------------------------------
        # Non-neutral, transient, mechanically-triggered. Own registry, budget
        # and lifecycle; exempt from the neutral caps and the market-reference hedge.
        from modules.execution.overrides import OverrideBank
        bank = OverrideBank(params, prices)
        override_w = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
        _prev_ov: dict = {}
        _ov_t: dict = {}
        if bank.enabled:
            print(f"[WalkForward] Override sleeve ENABLED: {len(bank.active_specs())} "
                  f"specs | budget={bank.budget}", flush=True)

        # Hook regime detector into override bank
        if _regime_chg is not None and bank.enabled:
            bank.set_regime(_regime_states, _regime_chg)

        for i in range(start_idx, len(prices)):
            current_date = prices.index[i]

            # ---- INDEPENDENT OVERRIDES (evaluated every day) ----------------
            if bank.enabled:
                _ov_t, _ov_ev = bank.step(current_date)
                if writer is not None:
                    for _e in _ov_ev:
                        writer.event(_e)
                if _ov_t != _prev_ov:
                    override_w.loc[current_date, :] = 0.0
                    for _n, _v in _ov_t.items():
                        if _n in override_w.columns:
                            override_w.loc[current_date, _n] = float(_v)
                    if writer is not None:
                        writer.event({
                            "type": "OVERRIDE_BOOK",
                            "ts": current_date.strftime("%Y-%m-%d"),
                            "targets": {k: float(v) for k, v in _ov_t.items()},
                            "gross": round(float(sum(abs(v) for v in _ov_t.values())), 10),
                            "n": len(_ov_t),
                        })
                    _prev_ov = dict(_ov_t)

            # ---- daily mark-to-market (drives the early-stop rule) ----------
            if _held_w is not None and len(_held_w):
                _dr = _daily_ret.loc[current_date]
                _pr = float((_held_w.reindex(_dr.index).fillna(0.0) * _dr).sum())
                _equity *= (1.0 + _pr)
            _equity_history.append(_equity)

            # ---- HARD BOOK RESET (path-dependence control) -------------------
            # Every `book_reset_days`, flatten all open pairs and clear carried
            # state (active book, sleeve weights, cooldowns) so the strategy's
            # outcome does not depend on trades accumulated months earlier. This
            # is the direct test of start-invariance: a low reset interval makes
            # each window behave like a fresh start.
            _reset_days = int(params.get("book_reset_days", 0))
            if _reset_days and (i - last_reset) >= _reset_days:
                for _p in list(active_pairs.keys()):
                    master_w.loc[current_date, _p[0]] = 0.0
                    master_w.loc[current_date, _p[1]] = 0.0
                    if _p in pair_w_mats:
                        pair_w_mats[_p].loc[current_date, _p[0]] = 0.0
                        pair_w_mats[_p].loc[current_date, _p[1]] = 0.0
                if _mkt_col and _mkt_col in master_w.columns:
                    master_w.loc[current_date, _mkt_col] = 0.0
                active_pairs = {}
                current_sleeve_weights = {}
                cooldown_tracker = {}
                _held_w = None
                last_reset = i
                # Emit the flattened effective book so tape replay matches the
                # engine (otherwise replay would hold stale positions).
                if writer is not None:
                    _eff = master_w.ffill().loc[current_date]
                    _t = {k: float(v) for k, v in _eff.items()
                          if np.isfinite(v) and abs(v) > 1e-12}
                    writer.event({
                        "type": "BOOK", "ts": current_date.strftime("%Y-%m-%d"),
                        "targets": _t, "market_overlay": 0.0,
                        "gross": round(float(sum(abs(v) for v in _t.values())), 10),
                        "n_pairs": 0, "n_long": 0, "n_short": 0, "reset": True,
                    })
                print(f"  [RESET @ {current_date.date()}] book flattened "
                      f"(every {_reset_days}d)", flush=True)
            step = i - start_idx + 1
            pct = (step / total_steps) * 100.0
            has_exit = False

            # ---- EXIT MANAGEMENT ---------------------------------------------
            for p in list(active_pairs.keys()):
                mem = active_pairs[p]
                cs = alpha[p].iloc[i]
                mem["Days_Held"] = mem["Days_Held"] + 1

                log_a = np.log(prices.at[current_date, p[0]])
                log_b = np.log(prices.at[current_date, p[1]])
                if trade_dag is not None:
                    # trade_dag is a price-scale residual panel; log() recovers
                    # the same factor-neutral log-level the Kalman was fit on.
                    log_a = float(np.log(trade_dag.at[current_date, p[0]]))
                    log_b = float(np.log(trade_dag.at[current_date, p[1]]))
                spread_mtm = log_a - (mem["Beta_Entry"] * log_b)
                e_t = (spread_mtm - mem["Intercept_Entry"]) / max(
                    mem["Spread_Vol_Entry"], 1e-4
                )

                exp_yield = max(0.0, abs(e_t) - params["exit_z_spread"]) * mem[
                    "Spread_Vol_Entry"
                ]
                frozen_alpha = (
                    exp_yield
                    * (252.0 / cs["Half_Life"])
                    * (1.0 - np.exp(-cs["Half_Life"] / params["tau_hl_penalty"]))
                )
                mem["Parametric_Alpha"] = frozen_alpha

                # OU-implied time to revert from ENTRY |Z| to the exit threshold:
                #   t = h * ln(|Z_entry| / z_exit) / ln(2)
                # times a buffer, clipped to [min_hold, max_holding_days].
                # (Previously max_days = int(hl * 1.25), which collapsed to the
                # minimum hold for short-half-life pairs and cut trades before
                # the reversion thesis had time to play out.)
                _hl = float(cs["Half_Life"])
                _z_exit = float(params["exit_z_spread"])
                _z_entry = abs(float(mem.get("Entry_Z", max(_z_exit * 2.0, 1.0))))
                _buf = float(params.get("max_holding_buffer",
                                        params.get("max_holding_mult", 3.0)))
                if _z_entry > _z_exit:
                    _ou_days = _hl * (np.log(_z_entry / _z_exit) / np.log(2.0))
                else:
                    _ou_days = _hl
                max_days = int(np.clip(
                    _ou_days * _buf,
                    params["min_holding_days"],
                    params["max_holding_days"],
                ))
                stale = (mem["Days_Held"] >= int(params.get("stale_days", 8))) and (
                    mem["Parametric_Alpha"] < float(params.get("stale_alpha", 0.35))
                )

                should_close = (
                    abs(e_t) >= params["stop_loss_z"]
                    or (
                        mem["Days_Held"] >= params["min_holding_days"]
                        and (
                            abs(e_t) <= params["exit_z_spread"]
                            or mem["Days_Held"] >= max_days
                            or stale
                        )
                    )
                )

                if should_close:
                    has_exit = True
                    if abs(e_t) >= params["stop_loss_z"]:
                        _reason = "STOP_LOSS"
                    elif abs(e_t) <= params["exit_z_spread"]:
                        _reason = "REVERTED"
                    elif mem["Days_Held"] >= max_days:
                        _reason = "MAX_HOLD"
                    elif stale:
                        _reason = "STALE"
                    else:
                        _reason = "CLOSE"
                    print(
                        f"  [EXIT] {p[0]}/{p[1]} | reason={_reason} | "
                        f"Z={e_t:+.2f} | days={mem['Days_Held']} | "
                        f"alpha={mem['Parametric_Alpha']:.4f} | "
                        f"entry_type={mem.get('Entry_Type', 'BASE')}",
                        flush=True,
                    )
                    if writer is not None:
                        writer.event({
                            "type": "EXIT", "ts": current_date.strftime("%Y-%m-%d"),
                            "pair": [p[0], p[1]], "reason": _reason,
                            "exit_z": float(e_t), "days_held": int(mem["Days_Held"]),
                            "alpha": float(mem["Parametric_Alpha"]),
                            "entry_type": mem.get("Entry_Type", "BASE"),
                            "px_a": float(prices.at[current_date, p[0]]),
                            "px_b": float(prices.at[current_date, p[1]]),
                        })
                        writer.event({
                            "type": "PAIR_BOOK", "ts": current_date.strftime("%Y-%m-%d"),
                            "pair": [p[0], p[1]],
                            "weights": {p[0]: 0.0, p[1]: 0.0}, "sleeve": 0.0,
                        })
                    master_w.loc[current_date, p[0]] = 0.0
                    master_w.loc[current_date, p[1]] = 0.0
                    if p in pair_w_mats:
                        pair_w_mats[p].loc[current_date, p[0]] = 0.0
                        pair_w_mats[p].loc[current_date, p[1]] = 0.0
                    # ---- EXIT-AWARE QUARANTINE --------------------------------
                    # STOP_LOSS: escalating strikes (base * 2^n, capped).
                    # STALE: short quarantine (decayed alpha persists).
                    # REVERTED: tiny cooldown (kills exit->re-enter ping-pong).
                    # MAX_HOLD: none (calendar exit, thesis not invalidated).
                    if _reason == "STOP_LOSS":
                        _strikes = _stop_strikes.get(p, 0) + 1
                        _stop_strikes[p] = _strikes
                        _cd = min(
                            float(params.get("quarantine_max_days", 90.0)),
                            float(params.get("quarantine_days", 30.0))
                            * (2.0 ** (_strikes - 1)),
                        )
                        cooldown_tracker[p] = _cd
                    elif _reason == "STALE":
                        cooldown_tracker[p] = float(
                            params.get("stale_quarantine_days", 10.0))
                    elif _reason == "REVERTED":
                        cooldown_tracker[p] = float(
                            params.get("reverted_cooldown_days", 2.0))
                    else:
                        _stop_strikes.pop(p, None)
                    del active_pairs[p]
                    current_sleeve_weights.pop(p, None)

            if not active_pairs:
                current_sleeve_weights.clear()
                target_sleeve_weights.clear()

            for p in list(cooldown_tracker.keys()):
                cooldown_tracker[p] -= 1
                if cooldown_tracker[p] <= 0:
                    del cooldown_tracker[p]

            # ---- ROLLING RETRAIN: refresh candidate universe ----------------
            if retrain_freq > 0 and (i - last_retrain) >= retrain_freq:
                lo = max(0, i - train_lb)
                train_slice = dag.iloc[lo:i]
                spy_slice = spy_series.iloc[lo:i]
                # Re-fit residualisation betas on the trailing training window
                res_train = compute_in_sample_residualization(
                    train_slice, spy_slice, len(train_slice)
                )
                corr_tr = np.corrcoef(np.diff(res_train.values, axis=0), rowvar=False)
                mask_tr = np.triu(sec_match & (corr_tr >= params["min_correlation"]), k=1)
                candidate_pairs = [
                    (valid[a], valid[b]) for a, b in zip(*np.where(mask_tr))
                ]
                # Honor the predictive-quality preselection on every retrain
                if presel_set is not None:
                    candidate_pairs = [
                        c for c in candidate_pairs
                        if c in presel_set or (c[1], c[0]) in presel_set
                    ]
                # Extend alpha cube for pairs not seen before (Kalman is past-only)
                new_pairs = [p for p in candidate_pairs if p not in alpha]
                if new_pairs:
                    new_res = _pool(
                        delayed(process_pair_history_fast)(
                            t1, t2,
                            spread_vals[:, col_map[t1]],
                            spread_vals[:, col_map[t2]],
                            m_ret_arr,
                            dates_idx,
                            params,
                            real_vals[:, real_col_map[t1]],
                            real_vals[:, real_col_map[t2]],
                        )
                        for t1, t2 in new_pairs
                    )
                    alpha.update({pair: df for pair, df in new_res})
                # Force an immediate cointegration rescreen on the new universe
                days_since_screening = int(params["screening_freq_days"])
                last_retrain = i

                # ---- WALK-FORWARD PARAM RE-SELECTION ------------------------
                # Deterministic-config rule: the walk-forward selector's picks
                # are NEVER auto-applied to the execution path. If a run needs
                # a specific param set over a given regime, bake those values
                # into the config file itself. The pick is logged here (and
                # exported via ctx["effective_params"]) purely for
                # post-run analysis/auditing.
                _sched = ctx.get("param_schedule") or []
                _pick = None
                for _s in _sched:
                    if _s.get("idx", 0) <= i:
                        _pick = _s
                    else:
                        break
                if _pick:
                    _applied = {}
                    ctx.setdefault("effective_params", []).append(
                        {"date": current_date, **_applied})
                    print(f"  [Selection] logged @ {current_date.date()} "
                          f"(NOT applied — deterministic config): {_pick}",
                          flush=True)


                print(
                    f"[Retrain @ {current_date.date()} | Progress: {pct:5.1f}%] "
                    f"Universe refreshed → {len(candidate_pairs)} candidates, "
                    f"+{len(new_pairs)} new pairs ({len(alpha)} total).",
                    flush=True,
                )

            # ---- COINTEGRATION RE-SCREEN ------------------------------------
            if days_since_screening >= params["screening_freq_days"] or not cached_keys:
                # Use a short screening lookback (distress-test lesson): the
                # coint test on the full warmup window is the per-screen cost
                # driver. Default to lookback_days unless a dedicated, shorter
                # `screen_lookback_days` is configured.
                screen_lb = params.get("screen_lookback_days", params["lookback_days"])
                lb = res_dag.iloc[i - screen_lb : i]
                # ---- Screen-time eligibility (late-listed names) ----------
                # A name listed after the panel start has leading NaNs that
                # crash the cointegration lstsq. Only screen pairs whose both
                # legs have (near-)full coverage over THIS screen window; such
                # names become eligible once their history covers it.
                _elig = lb.notna().mean() >= 0.98
                candidate_pairs = [
                    p for p in candidate_pairs
                    if bool(_elig.get(p[0], False)) and bool(_elig.get(p[1], False))
                ]

                # Parallel Engle-Granger screen, chunked so the windowed matrix
                # is shipped to each worker once per chunk rather than per pair.
                # Masks are recombined in original candidate order, so the
                # accepted set/order (and therefore the decisions) is unchanged.
                n_cand = len(candidate_pairs)
                _min_pass = int(params.get("screen_min_pairs", 0))
                if n_cand:
                    cmap = {c: j for j, c in enumerate(lb.columns)}
                    vals = np.ascontiguousarray(lb.values, dtype=float)
                    n_chunks = min(os.cpu_count() or 4, n_cand)
                    chunks = [c for c in np.array_split(np.arange(n_cand), n_chunks)
                              if len(c)]
                    masks = _pool(
                        delayed(_screen_fn)(
                            [candidate_pairs[j] for j in ch], vals, cmap, params)
                        for ch in chunks
                    )
                    mask = np.concatenate(masks) if masks else np.empty(0, dtype=bool)
                    n_pass = int(mask.sum())
                    # ---- LIVENESS FLOOR -------------------------------------
                    # A strict screen (v2) can admit ~0 pairs in short/narrow
                    # windows and silently bench the strategy. If fewer than
                    # `screen_min_pairs` survive, fall back to the EG screen for
                    # THIS screen only, so the book always has a minimum
                    # opportunity set. Logged so benching is never silent.
                    if (_screener_ver != "v1" and _min_pass > 0 and n_pass < _min_pass):
                        _fb = _SCREENERS["v1"]
                        masks2 = _pool(
                            delayed(_fb)([candidate_pairs[j] for j in ch], vals, cmap, params)
                            for ch in chunks)
                        mask2 = np.concatenate(masks2) if masks2 else np.empty(0, dtype=bool)
                        cached_keys = [p for p, ok in zip(candidate_pairs, mask2) if ok]
                        print(f"[Screen] {current_date.date()}: {_screener_ver} passed "
                              f"{n_pass} < min {_min_pass} -> fallback v1 "
                              f"({len(cached_keys)} pairs)", flush=True)
                    else:
                        cached_keys = [p for p, ok in zip(candidate_pairs, mask) if ok]
                    print(f"[Screen] {current_date.date()}: {n_cand} candidates -> "
                          f"{n_pass} pass ({_screener_ver}) -> {len(cached_keys)} live",
                          flush=True)
                else:
                    cached_keys = []
                days_since_screening = 0
            days_since_screening += 1

            # ---- GHOST POOL -------------------------------------------------
            ghost_records = []
            _dbg = (i == start_idx)
            _cnt = {"in": 0, "noalpha": 0, "nan": 0, "hurst": 0, "hl": 0, "slope": 0}
            for p in (
                list(active_pairs.keys())
                + [
                    k for k in cached_keys
                    if k not in active_pairs and k not in cooldown_tracker
                ]
            ):
                _cnt["in"] += 1
                if p not in alpha:
                    _cnt["noalpha"] += 1
                    continue
                st = alpha[p].iloc[i]
                if st.isna().any():
                    _cnt["nan"] += 1
                    continue

                is_active = p in active_pairs
                # Mean-reversion gate (single authority = the alpha-cube Hurst).
                # Applied ONLY under funnel_mode="full" (7-step legacy). Under
                # "simple" the ghost loop is the single filter — the screener
                # tests cointegration only, so this is the one Hurst check.
                # Removing it under full mode admitted trending spreads and the
                # book lost -25% 10y; the bug was the DOUBLE Hurst, not this gate.
                if not is_active and params.get("funnel_mode", "full") == "full":
                    if st["Hurst"] >= params["max_hurst_exponent"]:
                        _cnt["hurst"] += 1
                        continue
                    if st["Half_Life"] == params["max_half_life"]:
                        _cnt["hl"] += 1
                        continue
                    if abs(st["Slope_TStat"]) >= params.get("max_slope_tstat", 1.96):
                        _cnt["slope"] += 1
                        continue

                rec = {
                    "Asset_A": p[0], "Asset_B": p[1],
                    "Beta": st["Beta"], "Market_Beta": st["Market_Beta"],
                    "Intercept": st["Intercept"],
                    "Half_Life_Days": st["Half_Life"],
                    "Z_Spread": active_pairs[p]["Entry_Z"] if is_active else st["Z_Spread"],
                    "ECDF_Rank": st["ECDF_Rank"],
                    "Parametric_Alpha": active_pairs[p]["Parametric_Alpha"] if is_active else st["Parametric_Alpha"],
                    "Spread_Vol": st["Spread_Vol"],
                    "Days_Held": active_pairs[p]["Days_Held"] if is_active else 0,
                    "Is_Active": is_active,
                    "Entry_Type": active_pairs[p].get("Entry_Type", "BASE") if is_active else "PENDING",
                    "Beta_Entry": active_pairs[p]["Beta_Entry"] if is_active else st["Beta"],
                    "Intercept_Entry": active_pairs[p]["Intercept_Entry"] if is_active else st["Intercept"],
                    "Spread_Vol_Entry": active_pairs[p]["Spread_Vol_Entry"] if is_active else st["Spread_Vol"],
                    "Entry_Z": active_pairs[p]["Entry_Z"] if is_active else st["Z_Spread"],
                }
                ghost_records.append(rec)

            if _dbg:
                print(f"[GhostDBG] {current_date.date()}: in={_cnt['in']} "
                      f"noalpha={_cnt['noalpha']} nan={_cnt['nan']} "
                      f"hurst={_cnt['hurst']} hl={_cnt['hl']} slope={_cnt['slope']} "
                      f"kept={len(ghost_records)}", flush=True)

            ghost_df = pd.DataFrame(ghost_records)
            promoted_ghosts = pd.DataFrame()
            ghost_pool = pd.DataFrame()

            if not ghost_df.empty:
                ghost_pool = ghost_df[~ghost_df["Is_Active"]].copy()
                if not ghost_pool.empty:
                    # ---- PROMOTION -------------------------------------------------
                    # full mode (7-step): ECDF/alpha/z eligibility + alpha-quantile
                    # truncation + top-N cap before the optimizer.
                    # simple mode (3-step): no pre-selection — every ghost-qualified
                    # pair competes in the optimizer (its bounds/constraints are the
                    # selector). Subset of the full funnel, flag-gated.
                    if params.get("funnel_mode", "full") == "simple":
                        # No pre-selection cutoffs — every ghost-qualified pair
                        # competes. But SLSQP is O(n^3)-ish: cap the optimizer
                        # input by alpha (top-K) so the solve stays tractable.
                        _k = int(params.get("simple_max_pairs", 20))
                        promoted_ghosts = (
                            ghost_pool.sort_values("Parametric_Alpha", ascending=False)
                            .head(_k).copy()
                        )
                        promoted_ghosts["Entry_Type"] = "BASE"
                    else:
                        eligible = ghost_pool[
                            (ghost_pool["ECDF_Rank"] >= params["entry_ecdf_percentile"])
                            & (ghost_pool["Parametric_Alpha"] >= adaptive_min_alpha)
                            & (abs(ghost_pool["Z_Spread"]) >= params["min_override_z"])
                        ]
                        if not eligible.empty:
                            min_score = max(
                                1e-6,
                                eligible["Parametric_Alpha"].quantile(
                                    params["ghost_truncation_quantile"]
                                ),
                            )
                            promoted_ghosts = (
                                eligible[eligible["Parametric_Alpha"] >= min_score]
                                .sort_values("Parametric_Alpha", ascending=False)
                                .head(params["promote_top_n"])
                                .copy()
                            )
                            if _regime_on is not None:
                                try:
                                    if not bool(_regime_on.iloc[i]):
                                        promoted_ghosts = pd.DataFrame()
                                except Exception:
                                    pass
                            if not promoted_ghosts.empty:
                                if not bool(params.get("enable_promotion", True)):
                                    promoted_ghosts = pd.DataFrame()
                                else:
                                    promoted_ghosts = promoted_ghosts[
                                        abs(promoted_ghosts["Z_Spread"])
                                        >= params.get("entry_z_base", 0.95)
                                    ].copy()
                                    promoted_ghosts["Entry_Type"] = "BASE"

                if len(ghost_pool) > 0:
                    g_alloc = 1.0 / len(ghost_pool)
                    for _, r in ghost_pool.iterrows():
                        sign = -1.0 if r["Z_Spread"] > 0 else 1.0
                        ghost_w.loc[current_date, r["Asset_A"]] = sign * g_alloc
                        ghost_w.loc[current_date, r["Asset_B"]] = -sign * g_alloc * r["Beta"]

                # ---- COST-AWARE EV GATE (config-gated) ----------------------
                # Enter only when the OU-implied expected reversion over the
                # horizon clears the round-trip cost:
                #   T      = min(ev_max_hold_days, HL)  (horizon)
                #   frac   = 1 - exp(-ln2 * T / HL)     (fraction of the gap
                #          expected to close by T under the AR(1)/OU fit)
                #   EV     = (|z| - exit_z) * frac * Spread_Vol   (ret / unit w)
                #   Cost   = 2*exec_fee + borrow_bps/1e4 * T/365
                #            + turnover_extra_bps
                # Admit iff EV >= ev_gate_mult * Cost and EV > 0. The ghost
                # record carries Z_Spread / Half_Life_Days / Spread_Vol, so no
                # extra estimation is needed at decision time.
                if (
                    len(promoted_ghosts) > 0
                    and bool(params.get("ev_gate_enabled", False))
                ):
                    import math as _math
                    _exit_z = float(params.get("ev_exit_z",
                                               params.get("exit_z_spread", 0.2)))
                    _maxT = float(params.get("ev_max_hold_days", 60))
                    _mult = float(params.get("ev_gate_mult", 1.0))
                    _rt = 2.0 * float(params.get("exec_fee", 0.00015))
                    _tbps = float(params.get("turnover_extra_bps", 0.0005))
                    _bbps = float(params.get("borrow_bps", 50.0)) / 1e4
                    _keep, _dropped = [], 0
                    for _, r in promoted_ghosts.iterrows():
                        hl = float(r.get("Half_Life_Days",
                                         r.get("Half_Life", np.nan)))
                        z = abs(float(r["Z_Spread"]))
                        sv = float(r.get("Spread_Vol_Entry",
                                         r.get("Spread_Vol", np.nan)))
                        if not (np.isfinite(hl) and np.isfinite(sv)) or hl <= 0:
                            _dropped += 1
                            continue
                        T = min(_maxT, max(hl, 1.0))
                        frac = 1.0 - _math.exp(-_math.log(2.0) * T / hl)
                        gap = max(0.0, z - _exit_z)
                        ev = gap * frac * sv
                        cost = _rt + _tbps + _bbps * (T / 365.0)
                        if ev > 0.0 and ev >= _mult * cost:
                            _keep.append(r)
                        else:
                            _dropped += 1
                    _n0 = len(promoted_ghosts)
                    promoted_ghosts = pd.DataFrame(_keep) if _keep else pd.DataFrame()
                    if _n0 and len(promoted_ghosts) != _n0:
                        print(f"    [EVGate] {_n0} -> {len(promoted_ghosts)} "
                              f"promotions (EV >= {_mult}x cost "
                              f"{_rt*1e4:.1f}+{_tbps*1e4:.1f}bps rt, "
                              f"exit_z {_exit_z}).", flush=True)

                # ---- REVERSAL DISCRIMINATOR (config-gated) ------------------
                # Liquidity-driven dislocations are sharp counter-trend spikes;
                # information-driven ones are confirmed by the slower trend.
                # Require the MOVER leg's fast move to OPPOSE its slow trend
                # (sign disagreement), else the move is likely informational.
                if (
                    len(promoted_ghosts) > 0
                    and bool(params.get("rev_disc_enabled", False))
                ):
                    _f = int(params.get("rev_disc_fast", 5))
                    _s = int(params.get("rev_disc_slow", 20))
                    _fast = dag.iloc[i] / dag.iloc[max(0, i - _f)] - 1.0
                    _slow = dag.iloc[i] / dag.iloc[max(0, i - _s)] - 1.0
                    _keep, _dropped = [], 0
                    for _, r in promoted_ghosts.iterrows():
                        ra, rb = _fast.get(r["Asset_A"], 0.0), _fast.get(r["Asset_B"], 0.0)
                        sa, sb = _slow.get(r["Asset_A"], 0.0), _slow.get(r["Asset_B"], 0.0)
                        # mover = leg with the larger absolute fast move
                        if abs(ra) >= abs(rb):
                            opp = (ra > 0) != (sa > 0)
                        else:
                            opp = (rb > 0) != (sb > 0)
                        if opp:
                            _keep.append(r)
                        else:
                            _dropped += 1
                    _n0 = len(promoted_ghosts)
                    promoted_ghosts = pd.DataFrame(_keep) if _keep else pd.DataFrame()
                    if _n0 and len(promoted_ghosts) != _n0:
                        print(f"    [RevDisc] {_n0} -> {len(promoted_ghosts)} "
                              f"promotions (mover fast/slow sign opposition, "
                              f"{_f}d vs {_s}d).", flush=True)

                # ---- VOLUME-CONFIRMATION GATE (config-gated) ----------------
                # Hypothesis: noise-driven dislocations (high relative volume on
                # the dislocated leg) mean-revert; information/macro-trend moves
                # (thin volume) drift. Gate entries on the MOVER leg's lagged
                # relative volume (t-1 relvol vs its trailing ADV). Missing
                # volume data fails the gate (strict) — a pair without volume
                # confirmation simply doesn't enter.
                if (
                    len(promoted_ghosts) > 0
                    and bool(params.get("volume_gate_enabled", False))
                    and _relvol_lag is not None
                ):
                    _w = int(params.get("volume_gate_mover_window", 5))
                    _minrv = float(params.get("volume_gate_min_relvol", 0.0))
                    _maxrv = params.get("volume_gate_max_relvol")
                    _maxrv = float(_maxrv) if _maxrv is not None else None
                    _ret5 = dag.iloc[i] / dag.iloc[max(0, i - _w)] - 1.0
                    _keep = []
                    for _, r in promoted_ghosts.iterrows():
                        ra, rb = _ret5.get(r["Asset_A"], 0.0), _ret5.get(r["Asset_B"], 0.0)
                        mover = r["Asset_A"] if abs(ra) >= abs(rb) else r["Asset_B"]
                        rv = _relvol_lag.at[current_date, mover] \
                            if mover in _relvol_lag.columns else np.nan
                        if not np.isfinite(rv):
                            continue
                        if rv < _minrv:
                            continue
                        if _maxrv is not None and rv > _maxrv:
                            continue
                        _keep.append(r)
                    _n0 = len(promoted_ghosts)
                    promoted_ghosts = pd.DataFrame(_keep) if _keep else pd.DataFrame()
                    if _n0 and len(promoted_ghosts) != _n0:
                        _bound = (f"[{_minrv}, {_maxrv}]" if _maxrv is not None
                                  else f">= {_minrv}")
                        print(f"    [VolGate] {_n0} -> {len(promoted_ghosts)} "
                              f"promotions (relvol {_bound} on mover leg, "
                              f"lagged, {_w}d window).", flush=True)

                if len(promoted_ghosts) > 0:
                    # Continuous volume weighting (config-gated): quieter
                    # dislocations get larger weight. multiplier m = clip(
                    # 2 - relvol_mover, vol_w_floor, 1.0); equal weights when
                    # the mode is off. Weights are renormalised to sum to 1.
                    _mults = np.ones(len(promoted_ghosts))
                    _lag_m = np.ones(len(promoted_ghosts))
                    # ---- ADR-LOCAL LEAD-LAG MODULATION (config-gated) ------
                    # The local close (BRL/MXN/CLP session) predicts the ADR's
                    # NEXT-day move (verified in the lead-lag battery: Brazil
                    # reverses phi=-1; Chile/Mexico continue phi=+1). Per pair:
                    #   EV  = s_A*phi_A*local_A[t] + s_B*phi_B*local_B[t]
                    #   EVs = EV / Spread_Vol   (pair-vol sigma units)
                    #   m   = clip(1 + leadlag_tilt * EVs, 0.5, 1.5)
                    # Legs without a local mapping contribute zero (no tilt).
                    # Anti-placebo: config leadlag_flip reverses phi signs; the
                    # true-sign version is kept only if it beats the flip.
                    if bool(params.get("leadlag_tilt_enabled", False)) and _leadlag_ret is not None:
                        _tilt = float(params.get("leadlag_tilt", 0.5))
                        _phis = {"Brazil": -1.0, "Chile": 1.0, "Mexico": 1.0}
                        if bool(params.get("leadlag_flip", False)):
                            _phis = {k: -v for k, v in _phis.items()}
                        for j, (_, r) in enumerate(promoted_ghosts.iterrows()):
                            z = float(r["Z_Spread"])
                            sA = -1.0 if z > 0 else 1.0
                            ev, sv = 0.0, np.nan
                            for leg, sgn in ((r["Asset_A"], sA),
                                             (r["Asset_B"], sA * r["Beta"])):
                                lr = (_leadlag_ret.at[current_date, leg]
                                      if (_leadlag_ret is not None
                                          and leg in _leadlag_ret.columns) else np.nan)
                                if not np.isfinite(lr):
                                    continue
                                phi = _phis.get(_leadlag_country.get(leg, ""), 0.0)
                                ev += sgn * phi * float(lr)
                            sv = float(r.get("Spread_Vol", np.nan))
                            evs = ev / max(sv, 1e-4) if np.isfinite(sv) else 0.0
                            _lag_m[j] = float(np.clip(1.0 + _tilt * evs, 0.5, 1.5))
                    if bool(params.get("leadlag_tilt_enabled", False)) and \
                            (_lag_m != 1.0).any():
                        print(f"    [LagTilt] multipliers: "
                              f"{[round(float(x),2) for x in _lag_m]}", flush=True)
                    if (
                        params.get("volume_weight_mode") == "continuous"
                        and _relvol_lag is not None
                    ):
                        _floor = float(params.get("vol_w_floor", 0.5))
                        _w5 = int(params.get("volume_gate_mover_window", 5))
                        _r5 = dag.iloc[i] / dag.iloc[max(0, i - _w5)] - 1.0
                        for j, (_, r) in enumerate(promoted_ghosts.iterrows()):
                            ra = _r5.get(r["Asset_A"], 0.0)
                            rb = _r5.get(r["Asset_B"], 0.0)
                            mover = (r["Asset_A"] if abs(ra) >= abs(rb)
                                     else r["Asset_B"])
                            rv = (_relvol_lag.at[current_date, mover]
                                  if mover in _relvol_lag.columns else np.nan)
                            _mults[j] = (np.clip(2.0 - float(rv), _floor, 1.0)
                                         if np.isfinite(rv) else _floor)
                    _wts = _mults / _mults.sum()
                    for j, (_, r) in enumerate(promoted_ghosts.iterrows()):
                        sign = -1.0 if r["Z_Spread"] > 0 else 1.0
                        # lead-lag tilt scales the episode notional AFTER
                        # normalization (single-pair episodes would otherwise
                        # normalize the tilt away entirely)
                        _sc = _wts[j] * _lag_m[j]
                        promoted_w.loc[current_date, r["Asset_A"]] = sign * _sc
                        promoted_w.loc[current_date, r["Asset_B"]] = (
                            -sign * _sc * r["Beta"])

            # ---- REBALANCE TRIGGER ------------------------------------------
            drift = ConvexPortfolioOptimizer.calculate_max_weight_drift(
                current_sleeve_weights, target_sleeve_weights,
            )
            should_rebalance = (
                has_exit
                or len(promoted_ghosts) > 0
                or drift >= params["weight_drift_threshold"]
                or (i == start_idx)
            )

            if not should_rebalance:
                continue

            cause = (
                "INITIAL_ENTRY" if i == start_idx else (
                    "SIGNAL_EXIT" if has_exit else (
                        "SIGNAL_ENTRY" if not promoted_ghosts.empty
                        else f"WEIGHT_DRIFT ({drift:.1%})"
                    )
                )
            )
            print(
                f"\n[Rebalance @ {current_date.date()} | "
                f"Progress: {pct:5.1f}% ({step}/{total_steps})] Cause: {cause}",
                flush=True,
            )
            if writer is not None:
                writer.event({
                    "type": "REBALANCE", "ts": current_date.strftime("%Y-%m-%d"),
                    "cause": cause, "step": int(step),
                    "progress_pct": round(float(pct), 4),
                    "ghost_size": int(len(ghost_pool)),
                    "promoted_size": int(len(promoted_ghosts)),
                    "live_size": int(len(active_pairs)),
                })

            if ghost_df.empty:
                metrics_history.append({
                    "Date": current_date, "Ghost_Size": 0, "Promoted_Size": 0,
                    "Live_Size": len(active_pairs), "Live_Base": 0, "Live_Override": 0,
                    "Opt_Exp_Variance": 0.0, "Effective_Gross": 0.0,
                    "Mean_Innovation_Z": 0.0, "Mean_Parametric_Alpha": 0.0, "rebalanced": 1,
                })
                # Exits (and drops) above may have zeroed legs in master_w even
                # though this rebalance is skipped. Without a BOOK event the tape
                # would replay the stale prior book and hold exited legs for the
                # whole gap. Emit the EFFECTIVE held row (forward-filled book with
                # the just-zeroed legs removed) so BOOK stays the source of truth.
                if writer is not None:
                    _eff = master_w.ffill().loc[current_date]
                    _t = {k: float(v) for k, v in _eff.items()
                          if np.isfinite(v) and abs(v) > 1e-12}
                    writer.event({
                        "type": "BOOK", "ts": current_date.strftime("%Y-%m-%d"),
                        "targets": _t, "market_overlay": float(_eff.get(_mkt_col, 0.0) or 0.0)
                          if _mkt_col else 0.0,
                        "gross": round(float(sum(abs(v) for v in _t.values())), 10),
                        "n_pairs": 0,
                        "n_long": int(sum(1 for v in _t.values() if v > 0)),
                        "n_short": int(sum(1 for v in _t.values() if v < 0)),
                        "carry": True,
                    })
                continue

            # ---- SLSQP ALLOCATION -------------------------------------------
            live_candidates = ghost_df[ghost_df["Is_Active"]]
            live_df = pd.concat([live_candidates, promoted_ghosts]).drop_duplicates(
                subset=["Asset_A", "Asset_B"]
            )

            allocs, updates, opt, w_spy, *rest = engine_alloc.allocate(
                live_df, sectors, current_sleeve_weights, alpha, i,
                override_legs=_ov_t if _alloc_ver == "v2" else None,
            )
            _factor_metrics = rest[0] if rest else {}

            # ---- REGIME / DRAWDOWN EXPOSURE SCALING (pre-allocation override)
            # Dampen the book's gross when either (a) the regime detector flags
            # stress, or (b) the equity curve is in a trailing drawdown. ONE
            # scale is computed and applied ONCE (no compounding), so the floor
            # is min(regime_scale, dd_scale). Applied to every numeric field
            # (leg weights AND Sleeve_Weight) so book and state stay consistent.
            _scale = 1.0
            if _regime_states is not None and int(_regime_states.get(current_date, 0)) == 1:
                _scale = min(_scale, float(params.get("regime_exposure_scale", 0.3)))

            # ---- LEADING TAIL CONTROL (regime-change hard de-risk) ----------
            # On a TRANSITION into stressed, hard de-risk for `regime_kill_days`
            # at `regime_kill_scale` (0.0 = flat), then resume normal scaling.
            # Leading (regime change), unlike the lagging P&L breaker; targets
            # the stat-arb failure mode (correlation/vol regime shift), not beta.
            _kill_days = int(params.get("regime_kill_days", 0))
            if (_kill_days > 0 and _regime_chg is not None
                    and bool(_regime_chg.get(current_date, False))
                    and int((_regime_states or {}).get(current_date, 0)) == 1):
                _kill_until = i + _kill_days
                print(f"  [TAIL KILL @ {current_date.date()}] regime->stressed; "
                      f"de-risk {_kill_days}d", flush=True)
                if writer is not None:
                    writer.event({"type": "GATE", "ts": current_date.strftime("%Y-%m-%d"),
                                  "event": "TAIL_KILL", "days": _kill_days})
            if _kill_days > 0 and i < _kill_until:
                _scale = min(_scale, float(params.get("regime_kill_scale", 0.0)))

            _dd_window = int(params.get("trailing_dd_lookback", 126))
            _dd_thresh = float(params.get("trailing_dd_threshold", -0.08))
            _dd_scale = float(params.get("trailing_dd_scale", 0.3))
            _pos = i - start_idx  # position in _equity_history
            if _pos >= _dd_window and _equity > 0 and _equity_history[_pos - _dd_window] > 0:
                if (_equity / _equity_history[_pos - _dd_window] - 1.0) < _dd_thresh:
                    _scale = min(_scale, _dd_scale)
            # Leverage/exposure sizing (independent component)
            if _sizing_ver != "v1":
                _scale *= _sizer.scale(_equity_history, _pos, params)
            if _scale < 1.0 or (_sizing_ver != "v1"):
                for p in allocs:
                    for k in list(allocs[p]):
                        allocs[p][k] *= _scale
                w_spy *= _scale

            # ---- ADR-LOCAL LEAD-LAG MODULATION (config-gated) ---------------
            # Applied to the ALLOCATOR'S post-solve weights — this is where the
            # book's actual exposure comes from (the promoted_w path is
            # superseded by the optimizer on the same rebalance, so entry-time
            # tilts are inert). Mechanism per pair:
            #   EV  = s_A*phi_A*local_A[t] + s_B*phi_B*local_B[t]
            #   m   = clip(1 + leadlag_tilt * EV / Spread_Vol, 0.5, 1.5)
            # phi signs from the lead-lag battery (Brazil reversal -1, Chile/
            # Mexico continuation +1); anti-placebo via leadlag_flip.
            if bool(params.get("leadlag_tilt_enabled", False)) and \
                    _leadlag_ret is not None and allocs:
                _tilt = float(params.get("leadlag_tilt", 0.5))
                _phis = {"Brazil": -1.0, "Chile": 1.0, "Mexico": 1.0}
                if bool(params.get("leadlag_flip", False)):
                    _phis = {k: -v for k, v in _phis.items()}
                for p, wd in allocs.items():
                    # state from the allocator's memory (`updates`): Entry_Z /
                    # Spread_Vol_Entry live there, not in the leg-weight dict.
                    _st = updates.get(p, {})
                    z = float(_st.get("Entry_Z", _st.get("Z_Spread", 0.0)) or 0.0)
                    sA = -1.0 if z > 0 else 1.0
                    sv = float(_st.get("Spread_Vol_Entry",
                                       _st.get("Spread_Vol", np.nan)))
                    ev = 0.0
                    for leg, sgn in ((p[0], sA), (p[1], sA * wd.get("Beta", 0.0))):
                        lr = (_leadlag_ret.at[current_date, leg]
                              if leg in _leadlag_ret.columns else np.nan)
                        if not np.isfinite(lr):
                            continue
                        phi = _phis.get(_leadlag_country.get(leg, ""), 0.0)
                        ev += sgn * phi * float(lr)
                    evs = ev / max(sv, 1e-4) if np.isfinite(sv) else 0.0
                    m = float(np.clip(1.0 + _tilt * evs, 0.5, 1.5))
                    print(f"    [LagDBG] {current_date.date()} {p} m={m:.2f} "
                          f"ev={ev:+.4f} evs={evs:+.2f} "
                          f"locA={(_leadlag_ret.at[current_date, p[0]] if p[0] in _leadlag_ret.columns else float('nan')):+.4f} "
                          f"locB={(_leadlag_ret.at[current_date, p[1]] if p[1] in _leadlag_ret.columns else float('nan')):+.4f} "
                          f"EntryZ={z:+.2f}", flush=True)
                    if abs(m - 1.0) > 1e-9:
                        wd[p[0]] *= m
                        wd[p[1]] *= m
                        wd["Sleeve_Weight"] = float(wd.get("Sleeve_Weight", 0.0)) * m
                if writer is not None:
                    _ms = [round(float(wd.get("Sleeve_Weight", 0.0)), 3)
                           for wd in allocs.values()]
                    print(f"    [LagTilt] sleeve weights post-tilt: {_ms} "
                          f"(tilt={_tilt}, {'FLIPPED' if bool(params.get('leadlag_flip', False)) else 'true-sign'})",
                          flush=True)

            dropped = set(active_pairs.keys()) - set(allocs.keys())
            for p in dropped:
                master_w.loc[current_date, p[0]] = 0.0
                master_w.loc[current_date, p[1]] = 0.0
                if p in pair_w_mats:
                    pair_w_mats[p].loc[current_date, p[0]] = 0.0
                    pair_w_mats[p].loc[current_date, p[1]] = 0.0
                cooldown_tracker[p] = params["quarantine_days"]
                if writer is not None:
                    writer.event({
                        "type": "PAIR_BOOK", "ts": current_date.strftime("%Y-%m-%d"),
                        "pair": [p[0], p[1]],
                        "weights": {p[0]: 0.0, p[1]: 0.0}, "sleeve": 0.0,
                    })

            _prev_active = set(active_pairs.keys())
            active_pairs = updates
            current_sleeve_weights = {p: v["Sleeve_Weight"] for p, v in allocs.items()}
            current_sleeve_weights["OVERLAY"] = w_spy
            target_sleeve_weights = current_sleeve_weights.copy()

            # Rebuild the FULL target row from scratch each rebalance.
            # Rationale: the book holds the last written target, and legs were
            # previously accumulated with += while exits/drops patched single
            # cells with =. When a ticker is a leg of two pairs, or a pair is
            # dropped without an explicit exit, a stale non-zero target could
            # survive indefinitely ("orphaned leg"). Writing every column
            # explicitly (0 when not held) makes the book exactly equal to the
            # current allocations by construction.
            master_w.loc[current_date, :] = 0.0
            if _mkt_col and _mkt_col in master_w.columns:
                master_w.loc[current_date, _mkt_col] = w_spy

            for p, w in allocs.items():
                if p not in pair_w_mats:
                    pair_w_mats[p] = pd.DataFrame(
                        np.nan, index=prices.index, columns=[p[0], p[1]],
                    )
                pair_w_mats[p].loc[current_date, p[0]] = w[p[0]]
                pair_w_mats[p].loc[current_date, p[1]] = w[p[1]]

                master_w.at[current_date, p[0]] += w[p[0]]
                master_w.at[current_date, p[1]] += w[p[1]]

                trade_entry_log[p] = active_pairs[p].get("Entry_Type", "BASE")

            if writer is not None:
                _d = current_date.strftime("%Y-%m-%d")
                _sparse = {}
                for p, w in allocs.items():
                    for leg in (p[0], p[1]):
                        _sparse[leg] = _sparse.get(leg, 0.0) + float(w[leg])
                _sparse = {k: v for k, v in _sparse.items() if abs(v) > 1e-12}
                if _mkt_col and _mkt_col in prices.columns and abs(w_spy) > 1e-12:
                    _sparse[_mkt_col] = float(w_spy)
                writer.event({
                    "type": "BOOK", "ts": _d, "targets": _sparse,
                    "market_overlay": float(w_spy),
                    "gross": round(float(sum(abs(v) for v in _sparse.values())), 10),
                    "n_pairs": int(len(allocs)),
                    "n_long": int(sum(1 for v in _sparse.values() if v > 0)),
                    "n_short": int(sum(1 for v in _sparse.values() if v < 0)),
                })
                _emit_ghost(writer, "GHOST_BOOK", _d, ghost_pool)
                _emit_ghost(writer, "PROMOTED_BOOK", _d, promoted_ghosts)
                for p, w in allocs.items():
                    writer.event({
                        "type": "PAIR_BOOK", "ts": _d, "pair": [p[0], p[1]],
                        "weights": {p[0]: float(w[p[0]]), p[1]: float(w[p[1]])},
                        "sleeve": float(w["Sleeve_Weight"]),
                    })
                for p in active_pairs:
                    if p in _prev_active:
                        continue
                    mem, wd = active_pairs[p], allocs.get(p)
                    writer.event({
                        "type": "ENTRY", "ts": _d, "pair": [p[0], p[1]],
                        "legs": [p[0], p[1]],
                        "beta": float(mem.get("Beta_Entry", np.nan)),
                        "entry_z": float(mem.get("Entry_Z", np.nan)),
                        "alpha": float(mem.get("Parametric_Alpha", np.nan)),
                        "entry_type": mem.get("Entry_Type", "BASE"),
                        "sleeve_w": float(wd["Sleeve_Weight"]) if wd else 0.0,
                        "w_a": float(wd[p[0]]) if wd else 0.0,
                        "w_b": float(wd[p[1]]) if wd else 0.0,
                        "px_a": float(prices.at[current_date, p[0]]),
                        "px_b": float(prices.at[current_date, p[1]]),
                    })

            # ---- LIVE EARLY-STOP: update book + evaluate floors ------------
            _held_w = master_w.loc[current_date].copy()
            _rebal_dates.append(current_date)
            _period_rets.append(_equity / max(_last_rebal_eq, 1e-9) - 1.0)
            _last_rebal_eq = _equity

            # ---- ACCUMULATED performance (for debugging + kill decision) ----
            if _period_rets:
                _rr = np.asarray(_period_rets, dtype=float)
                _ppy = 252.0 / max(1, _es_rebal_days)
                _cum = float(np.prod(1.0 + _rr) - 1.0) * 100.0
                _win = 100.0 * float((_rr > 0).mean())
                _sh = (float(_rr.mean() / _rr.std() * np.sqrt(_ppy))
                       if _rr.std() > 0 else 0.0)
                _dn = _rr[_rr < 0]
                _so = (float(_rr.mean() / np.sqrt((_dn ** 2).mean()) * np.sqrt(_ppy))
                       if len(_dn) else float("inf"))
                _pos = float(_rr[_rr > 0].sum())
                _cc = float(_rr.max() / _pos) if _pos > 0 else 0.0
                _eq = np.cumprod(1.0 + _rr)
                _dd = float((1.0 - _eq / np.maximum.accumulate(_eq)).max()) * 100.0
                _stk = _mx = 0
                for _x in _rr:
                    _stk = _stk + 1 if _x < 0 else 0
                    _mx = max(_mx, _stk)
                print(
                    f"  --> CUMULATIVE: cumRet={_cum:+.2f}% | DD={_dd:.2f}% | "
                    f"win={_win:.1f}% | Sharpe={_sh:.2f} | Sortino={_so:.2f} | "
                    f"streak={_mx} | conc={_cc:.2f} | periods={len(_rr)}",
                    flush=True,
                )

            # NOTE: the live kill was removed. Its internal mark-to-market
            # (target weights x close-to-close) diverged from the engine's
            # actual P&L (T+1 open fills, fees, borrow) and spuriously killed
            # good runs. The kill is now applied POST-HOC on the real backtest
            # P&L after the loop (see below).
            if False and _es_on and not _killed:
                _chk = check_floors(
                    _period_rets, _es_min_p, _es_max_dd, _es_max_loss,
                    _es_max_streak, _es_sig_win, _es_sig_min, _es_cum_k,
                    _es_min_sharpe, _es_min_sortino, _es_min_win,
                    periods_per_year=252.0 / max(1, _es_rebal_days),
                )
                if _chk and _chk["kill"]:
                    _killed = True
                    _kill_info = _chk
                    print(
                        f"\n  *** EARLY-STOP KILL @ {current_date.date()} after "
                        f"{_chk.get('periods', 0)} periods | "
                        f"cum={_chk.get('cum_return_%', 0):.2f}% "
                        f"win={_chk.get('win_rate', 0):.1f}% "
                        f"sharpe={_chk.get('sharpe', 0):.2f} "
                        f"DD={_chk.get('drawdown_%', 0):.1f}% "
                        f"conc={_chk.get('concentration', 0):.2f} "
                        f"| {', '.join(_chk['reasons'])} ***",
                        flush=True,
                    )
                    # Flatten every open leg at the kill date, then BREAK OUT
                    # of the walk-forward loop — trading ceases permanently and
                    # the backtest covers only the period up to this point.
                    master_w.loc[current_date, :] = 0.0
                    if _mkt_col and _mkt_col in master_w.columns:
                        master_w.loc[current_date, _mkt_col] = 0.0
                    active_pairs = {}
                    current_sleeve_weights = {}
                    target_sleeve_weights = {}
                    _held_w = pd.Series(0.0, index=prices.columns)
                    metrics_history.append({
                        "Date": current_date, "Ghost_Size": 0, "Promoted_Size": 0,
                        "Live_Size": 0, "Live_Base": 0, "Live_Override": 0,
                        "Opt_Exp_Variance": 0.0, "Effective_Gross": 0.0,
                        "Mean_Innovation_Z": 0.0, "Mean_Parametric_Alpha": 0.0,
                        "rebalanced": 1,
                    })
                    print(f"  [flattened — walk-forward terminated at "
                          f"{current_date.date()}]", flush=True)
                    break

            avg_z = np.mean([abs(m["Entry_Z"]) for m in active_pairs.values()]) if active_pairs else 0.0
            avg_alpha = live_df["Parametric_Alpha"].mean() if not live_df.empty else 0.0
            base_cnt = sum(1 for v in active_pairs.values() if v.get("Entry_Type") == "BASE")
            over_cnt = sum(1 for v in active_pairs.values() if v.get("Entry_Type") == "OVERRIDE")

            metrics_history.append({
                "Date": current_date,
                "Ghost_Size": len(ghost_pool), "Promoted_Size": len(promoted_ghosts),
                "Live_Size": len(active_pairs), "Live_Base": base_cnt,
                "Live_Override": over_cnt,
                "Opt_Exp_Variance": opt.get("Portfolio_Variance", 0.0),
                "Effective_Gross": opt.get("Effective_Gross", 0.0),
                "Mean_Innovation_Z": avg_z, "Mean_Parametric_Alpha": avg_alpha,
                "rebalanced": 1,
            })

            print(
                f"  --> Ghost={len(ghost_pool)} | Promoted={len(promoted_ghosts)} | "
                f"Live={len(active_pairs)}", flush=True,
            )
            if active_pairs:
                print(
                    f"  --> Eff Gross={opt.get('Effective_Gross', 0):.2f} | "
                    f"Port Vol={np.sqrt(max(0, opt.get('Portfolio_Variance', 0))):.4f} | "
                    f"{(ctx.get('market_ticker') or 'MKT')} Overlay={w_spy:.1%}", flush=True,
                )
                top = sorted(
                    active_pairs.keys(),
                    key=lambda x: abs(allocs[x]["Sleeve_Weight"]), reverse=True,
                )[:3]
                for p in top:
                    wd = allocs[p]
                    mem = active_pairs[p]
                    ecdf_val = 1.0
                    match = live_df[(live_df["Asset_A"] == p[0]) & (live_df["Asset_B"] == p[1])]
                    if not match.empty:
                        ecdf_val = match["ECDF_Rank"].values[0]
                    print(
                        f"      [{mem.get('Entry_Type', 'BASE')}] {p[0]}/{p[1]} | "
                        f"W_A: {wd[p[0]]:.1%} | W_B: {wd[p[1]]:.1%} | "
                        f"Inno Z: {mem['Entry_Z']:.2f} | ECDF: {ecdf_val:.2f} | "
                        f"Alpha: {mem['Parametric_Alpha']:.4f}", flush=True,
                    )
            print("-" * 60, flush=True)

        # ---- END LOOP — cap gross exposure -----------------------------------
        cap = params["max_gross_exposure"]
        for df_w in [master_w, ghost_w, promoted_w]:
            abs_sum = df_w.abs().sum(axis=1)
            exceed = abs_sum > cap
            if exceed.any():
                df_w.loc[exceed] = df_w.loc[exceed].div(abs_sum[exceed], axis=0) * cap

        # ---- POST-HOC EARLY-STOP is enforced downstream on the tape's REAL ---
        # realized P&L (see AttributionStage). The walk-forward only decides;
        # it does not run a second backtest here.
        _es_info = None

        if writer is not None:
            writer.close()

        ctx["master_weights"] = master_w
        ctx["override_weights"] = override_w
        ctx["override_bank_summary"] = bank.summary()
        ctx["ghost_weights"] = ghost_w
        ctx["promoted_weights"] = promoted_w
        ctx["pair_weight_matrices"] = pair_w_mats
        ctx["trade_entry_log"] = trade_entry_log
        ctx["metrics_history"] = metrics_history
        ctx["early_stop"] = {
            "enabled": _es_on,
            "killed": _killed,
            "info": _es_info if _es_info is not None else _kill_info,
            "periods_completed": len(_rebal_dates),
        }
        ctx["active_pairs"] = active_pairs
        ctx["cooldown_tracker"] = cooldown_tracker
        # Deterministic-config audit trail: every param the selector overrode
        # at runtime (empty unless selection_apply=true).
        ctx["effective_params"] = ctx.get("effective_params", [])

class WalkForwardStageV2(WalkForwardStage):
    """V2: adds a REGIME ENTRY GATE.

    New entries are suspended when trailing cross-sectional dispersion is below
    a rolling percentile (unfavourable regime); exits stay live so the book can
    de-risk. Targets the observed pattern: dead in low-dispersion tape, alive in
    high-dispersion tape.
    """

    def run(self, ctx: dict) -> None:
        self.params["regime_gate_entries"] = bool(
            self.params.get("regime_gate_entries", True)
        )
        super().run(ctx)
