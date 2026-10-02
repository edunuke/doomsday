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
from statsmodels.tsa.stattools import coint

from modules import PipelineStage
from modules.optimization.optimizer import ConvexPortfolioOptimizer
from modules.processing.utils import compute_in_sample_residualization
from modules.processing.pair_processor import process_pair_history_fast
from modules.risk.progressive import check_floors


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

        engine_alloc = ConvexPortfolioOptimizer(params)

        active_pairs = {}
        cooldown_tracker = {}

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
        # Residual panel drives the spread/signal; real prices drive the hedge.
        spread_panel = trade_dag if trade_dag is not None else dag
        spread_vals = spread_panel.values
        col_map = {c: idx for idx, c in enumerate(spread_panel.columns)}
        real_vals = dag.values
        real_col_map = {c: idx for idx, c in enumerate(dag.columns)}
        m_ret_arr = ctx["market_ret"].values
        spy_series = prices["SPY"] if "SPY" in prices.columns else dag.mean(axis=1)
        sec_arr = sectors.reindex(valid).values
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
        for i in range(start_idx, len(prices)):
            current_date = prices.index[i]

            # ---- daily mark-to-market (drives the early-stop rule) ----------
            if _held_w is not None and len(_held_w):
                _dr = _daily_ret.loc[current_date]
                _pr = float((_held_w.reindex(_dr.index).fillna(0.0) * _dr).sum())
                _equity *= (1.0 + _pr)
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
                    master_w.loc[current_date, p[0]] = 0.0
                    master_w.loc[current_date, p[1]] = 0.0
                    if p in pair_w_mats:
                        pair_w_mats[p].loc[current_date, p[0]] = 0.0
                        pair_w_mats[p].loc[current_date, p[1]] = 0.0
                    if abs(e_t) >= params["stop_loss_z"]:
                        cooldown_tracker[p] = params["quarantine_days"]
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
                    with Parallel(n_jobs=-1, backend="loky") as parallel:
                        new_res = parallel(
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

                def _check(t1, t2):
                    p1 = lb[t1].values
                    p2 = lb[t2].values
                    if coint(p1, p2)[1] <= params["p_value_threshold"]:
                        return (t1, t2)
                    return None

                cached_keys = [
                    r for r in [_check(p[0], p[1]) for p in candidate_pairs]
                    if r is not None
                ]
                days_since_screening = 0
            days_since_screening += 1

            # ---- GHOST POOL -------------------------------------------------
            ghost_records = []
            for p in (
                list(active_pairs.keys())
                + [
                    k for k in cached_keys
                    if k not in active_pairs and k not in cooldown_tracker
                ]
            ):
                if p not in alpha:
                    continue
                st = alpha[p].iloc[i]
                if st.isna().any():
                    continue

                is_active = p in active_pairs
                if not is_active:
                    if st["Hurst"] >= params["max_hurst_exponent"]:
                        continue
                    if st["Half_Life"] == params["max_half_life"]:
                        continue
                    if abs(st["Slope_TStat"]) >= params.get("max_slope_tstat", 1.96):
                        continue

                rec = {
                    "Asset_A": p[0], "Asset_B": p[1],
                    "Beta": st["Beta"], "SPY_Beta": st["SPY_Beta"],
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

            ghost_df = pd.DataFrame(ghost_records)
            promoted_ghosts = pd.DataFrame()
            ghost_pool = pd.DataFrame()

            if not ghost_df.empty:
                ghost_pool = ghost_df[~ghost_df["Is_Active"]].copy()
                if not ghost_pool.empty:
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
                            promoted_ghosts["Entry_Type"] = np.where(
                                abs(promoted_ghosts["Z_Spread"])
                                >= params.get("entry_z_base", 0.95),
                                "BASE", "OVERRIDE",
                            )

                if len(ghost_pool) > 0:
                    g_alloc = 1.0 / len(ghost_pool)
                    for _, r in ghost_pool.iterrows():
                        sign = -1.0 if r["Z_Spread"] > 0 else 1.0
                        ghost_w.loc[current_date, r["Asset_A"]] = sign * g_alloc
                        ghost_w.loc[current_date, r["Asset_B"]] = -sign * g_alloc * r["Beta"]

                if len(promoted_ghosts) > 0:
                    p_alloc = 1.0 / len(promoted_ghosts)
                    for _, r in promoted_ghosts.iterrows():
                        sign = -1.0 if r["Z_Spread"] > 0 else 1.0
                        promoted_w.loc[current_date, r["Asset_A"]] = sign * p_alloc
                        promoted_w.loc[current_date, r["Asset_B"]] = -sign * p_alloc * r["Beta"]

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

            if ghost_df.empty:
                metrics_history.append({
                    "Date": current_date, "Ghost_Size": 0, "Promoted_Size": 0,
                    "Live_Size": len(active_pairs), "Live_Base": 0, "Live_Override": 0,
                    "Opt_Exp_Variance": 0.0, "Effective_Gross": 0.0,
                    "Mean_Innovation_Z": 0.0, "Mean_Parametric_Alpha": 0.0, "rebalanced": 1,
                })
                continue

            # ---- SLSQP ALLOCATION -------------------------------------------
            live_candidates = ghost_df[ghost_df["Is_Active"]]
            live_df = pd.concat([live_candidates, promoted_ghosts]).drop_duplicates(
                subset=["Asset_A", "Asset_B"]
            )

            allocs, updates, opt, w_spy = engine_alloc.allocate(
                live_df, sectors, current_sleeve_weights, alpha, i,
            )

            dropped = set(active_pairs.keys()) - set(allocs.keys())
            for p in dropped:
                master_w.loc[current_date, p[0]] = 0.0
                master_w.loc[current_date, p[1]] = 0.0
                if p in pair_w_mats:
                    pair_w_mats[p].loc[current_date, p[0]] = 0.0
                    pair_w_mats[p].loc[current_date, p[1]] = 0.0
                cooldown_tracker[p] = params["quarantine_days"]

            active_pairs = updates
            current_sleeve_weights = {p: v["Sleeve_Weight"] for p, v in allocs.items()}
            current_sleeve_weights["SPY_OVERLAY"] = w_spy
            target_sleeve_weights = current_sleeve_weights.copy()

            # Rebuild the FULL target row from scratch each rebalance.
            # Rationale: vectorbt holds the last written target, and legs were
            # previously accumulated with += while exits/drops patched single
            # cells with =. When a ticker is a leg of two pairs, or a pair is
            # dropped without an explicit exit, a stale non-zero target could
            # survive indefinitely ("orphaned leg"). Writing every column
            # explicitly (0 when not held) makes the book exactly equal to the
            # current allocations by construction.
            master_w.loc[current_date, :] = 0.0
            if "SPY" in master_w.columns:
                master_w.loc[current_date, "SPY"] = w_spy

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
            # (target weights x close-to-close) diverged from the actual
            # vectorbt P&L (T+1 open fills, fees, borrow) and spuriously killed
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
                    if "SPY" in master_w.columns:
                        master_w.loc[current_date, "SPY"] = 0.0
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
                    f"SPY Overlay={w_spy:.1%}", flush=True,
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

        # ---- POST-HOC EARLY-STOP (exact, on the REAL backtest P&L) -----------
        # The live kill's internal mark-to-market diverged from the engine's
        # actual P&L (T+1 open fills, fees, borrow). We therefore backtest the
        # FULL weight path once, derive the REAL per-rebalance returns, apply the
        # floors to them, and truncate (flatten) at the first breach.
        _es_info = None
        # NOTE: the walk-forward no longer performs the kill itself — it runs in
        # AttributionStage on the EXACT realized P&L (T+1 fills, fees, borrow).
        if False and _es_on and len(_rebal_dates) > 0:
            try:
                from modules.execution.backtest_engine import VectorbtBacktestEngine
                _open = ctx.get("open_full", prices)
                _cols = list(master_w.columns)
                _eng = VectorbtBacktestEngine(params)
                _pf, _nav, _ = _eng.run_backtest(
                    prices[_cols],
                    _open.reindex(prices.index)[_cols].ffill(),
                    master_w.shift(1).fillna(0.0),
                )
                _daily = _nav.pct_change().fillna(0.0)
                _pr, _last = [], _daily.index[0]
                for _d in _rebal_dates:
                    _seg = _daily.loc[(_daily.index > _last) & (_daily.index <= _d)]
                    if len(_seg):
                        _pr.append(float((1 + _seg).prod() - 1))
                        _last = _d
                _kill_date = None
                for _n in range(1, len(_pr) + 1):
                    _chk = check_floors(
                        _pr[:_n], _es_min_p, _es_max_dd, _es_max_loss,
                        _es_max_streak, _es_sig_win, _es_sig_min, _es_cum_k,
                        _es_min_sharpe, _es_min_sortino, _es_min_win,
                        periods_per_year=252.0 / max(1, _es_rebal_days),
                    )
                    if _chk and _chk.get("kill"):
                        _kill_date = _rebal_dates[_n - 1]
                        _es_info = _chk
                        break
                if _kill_date is not None:
                    _killed = True
                    for _df in (master_w, ghost_w, promoted_w):
                        _df.loc[_df.index > _kill_date, :] = 0.0
                    for _p in pair_w_mats:
                        pair_w_mats[_p].loc[pair_w_mats[_p].index > _kill_date, :] = 0.0
                    print(
                        f"\n  *** EARLY-STOP KILL (post-hoc, on actual P&L) @ "
                        f"{_kill_date.date()} after {_es_info['periods']} periods | "
                        f"cum={_es_info['cum_return_%']:.2f}% "
                        f"sharpe={_es_info['sharpe']:.2f} "
                        f"DD={_es_info['drawdown_%']:.1f}% "
                        f"| {', '.join(_es_info['reasons'])} ***",
                        flush=True,
                    )
                    print(f"  [flattened after {_kill_date.date()}]", flush=True)
                else:
                    print(f"[WalkForward] early-stop (post-hoc, actual P&L): "
                          f"SURVIVED all {len(_pr)} periods.", flush=True)
            except Exception as _exc:
                print(f"[WalkForward] post-hoc early-stop failed ({_exc})", flush=True)

        ctx["master_weights"] = master_w
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
