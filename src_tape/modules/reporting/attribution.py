"""
ATTRIBUTION STAGE (tape-driven)

Post-loop: rebuild the execution-lagged book from the tape, simulate it with our
own portfolio engine (T+1 open MOO fills, fees, short borrow), apply the
circuit-breaker / early-stop on the REAL realized P&L, run the CAPM neutrality
diagnostic, build per-pair tearsheets from PAIR_BOOK events, and assemble the
summary dictionary.

The decision engine's wide matrices are only a fallback; the primary source is
``tape.txt``.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from modules import PipelineStage
from modules.core.tape import (
    TapeReader, reconstruct_book, reconstruct_pair_books, reconstruct_stream,
)
from modules.execution.portfolio_engine import run_target_weights
from modules.execution.stats import compute_stats
from modules.execution.order_generator import LiveMOOOrderGenerator
from modules.processing.utils import compute_sortino_ratio
from modules.reporting.tearsheet import CommitteeTearsheetEngine
from modules.risk.benchmark import sofr_daily_returns, benchmark_summary
from modules.risk.neutrality import MarketNeutralityDiagnosticEngine


def _cap_gross(mat: pd.DataFrame, cap: float) -> pd.DataFrame:
    """Apply the end-of-run gross-exposure cap to non-empty rows."""
    abs_sum = mat.abs().sum(axis=1)
    exceed = abs_sum > cap
    if exceed.any():
        mat = mat.copy()
        mat.loc[exceed] = mat.loc[exceed].div(abs_sum[exceed], axis=0) * cap
    return mat


class AttributionStage(PipelineStage):
    """Stage 5: tape replay, simulation, attribution, MOO blotter."""

    def run(self, ctx: dict) -> None:
        params = self.params
        start = ctx["start_idx"]
        prices = ctx["prices_full"]
        open_full = ctx["open_full"]
        dag = ctx["dag_df"]
        market_ret = ctx["market_ret"]
        metrics_history = ctx.get("metrics_history", [])

        all_cols = prices.columns
        trade_cols = dag.columns
        tape_path = ctx.get("tape_path")
        use_tape = bool(tape_path and os.path.exists(tape_path))

        if use_tape:
            print(f"[Attribution] Replaying decision tape: {tape_path}", flush=True)
            master_w = _cap_gross(
                reconstruct_book(tape_path, prices.index, all_cols, "BOOK"),
                params["max_gross_exposure"])
            ghost_w = _cap_gross(
                reconstruct_book(tape_path, prices.index, trade_cols, "GHOST_BOOK"),
                params["max_gross_exposure"])
            promoted_w = _cap_gross(
                reconstruct_book(tape_path, prices.index, trade_cols, "PROMOTED_BOOK"),
                params["max_gross_exposure"])
            pair_w_mats = reconstruct_pair_books(tape_path, prices.index, all_cols)
            override_w = reconstruct_stream(
                tape_path, prices.index, all_cols, "OVERRIDE_BOOK")
            trade_entry_log = {}
            rebal_dates = []
            for ev in TapeReader(tape_path).events({"ENTRY", "REBALANCE"}):
                if ev["type"] == "REBALANCE":
                    rebal_dates.append(pd.Timestamp(ev["ts"]))
                elif ev["type"] == "ENTRY":
                    trade_entry_log[tuple(ev["pair"])] = ev.get("entry_type", "UNKNOWN")
        else:
            print("[Attribution] No tape — falling back to engine matrices.", flush=True)
            master_w = ctx["master_weights"]
            ghost_w = ctx["ghost_weights"]
            promoted_w = ctx["promoted_weights"]
            pair_w_mats = ctx["pair_weight_matrices"]
            override_w = ctx.get("override_weights",
                                 pd.DataFrame(0.0, index=prices.index, columns=all_cols))
            trade_entry_log = ctx["trade_entry_log"]
            rebal_dates = ctx["metrics_history"] and [
                r["Date"] for r in ctx["metrics_history"]] or []

        rebal_dates = pd.DatetimeIndex(sorted(set(rebal_dates)))

        # ---- Execution lag + per-pair continuous targets --------------------
        # BASE is sparse: NaN means "hold the previous target". Materialise the
        # held book BEFORE adding the (dense) override stream — DataFrame.add
        # with fill_value would turn a NaN "hold" into an explicit 0 "flatten"
        # and silently liquidate the whole book.
        exec_base = master_w.shift(1).iloc[start:].ffill().fillna(0.0)
        exec_ov = override_w.reindex(index=prices.index, columns=master_w.columns) \
            .ffill().fillna(0.0).shift(1).iloc[start:].fillna(0.0)
        exec_w = exec_base + exec_ov                            # BASE + OVERRIDE
        exec_ghost = ghost_w.shift(1).iloc[start:]
        exec_promoted = promoted_w.shift(1).iloc[start:]
        for p in pair_w_mats:
            pair_w_mats[p] = pair_w_mats[p].ffill().fillna(0.0).shift(1)

        rebal_metrics = pd.DataFrame(metrics_history).set_index("Date") if metrics_history else pd.DataFrame()
        exec_close = prices[master_w.columns].iloc[start:]
        exec_open = open_full[master_w.columns].reindex(prices.index).ffill().iloc[start:]

        metrics_df = pd.DataFrame(index=exec_close.index)
        if not rebal_metrics.empty:
            metrics_df = metrics_df.join(rebal_metrics, how="left")
            metrics_df["rebalanced"] = metrics_df["rebalanced"].fillna(0).astype(int)
            fill_cols = [
                "Ghost_Size", "Promoted_Size", "Live_Size", "Live_Base",
                "Live_Override", "Opt_Exp_Variance", "Effective_Gross",
                "Mean_Innovation_Z", "Mean_Parametric_Alpha",
            ]
            for c in fill_cols:
                if c in metrics_df.columns:
                    metrics_df[c] = metrics_df[c].ffill().fillna(0.0)
        else:
            metrics_df["rebalanced"] = 0

        # ---- Simulation (our engine) ----------------------------------------
        eng = dict(init_cash=params["initial_capital"], exec_fee=params["exec_fee"],
                   daily_borrow_rate=(params["borrow_bps"] / 10000.0) / 252.0)
        # Idle-cash yield: episodic books sit in T-bills most days; credit the
        # real-world cash yield so low-turnover strategies are not compared
        # against a benchmark that earns SOFR on capital the strategy holds as
        # zero-yield cash.
        if bool(params.get("cash_yield_idle", False)):
            from modules.risk.benchmark import sofr_daily_returns as _sdr
            eng["daily_cash_yield"] = _sdr(pd.DatetimeIndex(exec_close.index))
            print(f"[Attribution] idle-cash yield: SOFR credited on positive "
                  f"cash (cash_yield_idle=true).", flush=True)
        # ---- short-side delisting penalty (synthetic panel only) ----------
        delist_map, _sdh = {}, 0.0
        if "survivorship" in str(params.get("price_csv", "")) and \
                os.path.exists("data/survivorship_delist.csv"):
            _dm = pd.read_csv("data/survivorship_delist.csv")
            delist_map = {str(r.Symbol): pd.Timestamp(r.delist_date)
                          for r in _dm.itertuples()}
            _sdh = float(params.get("short_delist_haircut", 0.5))
            print(f"[Attribution] short-delist penalty: {len(delist_map)} names, "
                  f"haircut={_sdh}", flush=True)
            eng["delist_map"] = delist_map
            eng["short_delist_haircut"] = _sdh
        res_live = run_target_weights(exec_close, exec_open, exec_w, **eng)
        res_base = run_target_weights(exec_close, exec_open, exec_base, **eng)
        res_ghost = run_target_weights(
            exec_close[trade_cols], exec_open[trade_cols], exec_ghost, **eng)
        res_promoted = run_target_weights(
            exec_close[trade_cols], exec_open[trade_cols], exec_promoted, **eng)

        ret_live = res_live.value.pct_change().fillna(0.0)
        ret_base = res_base.value.pct_change().fillna(0.0)
        ret_ghost = res_ghost.value.pct_change().fillna(0.0)
        ret_promoted = res_promoted.value.pct_change().fillna(0.0)
        gross_exp = res_live.gross_exposure

        metrics_df["Live_Realized_Return"] = ret_live
        metrics_df["Base_Realized_Return"] = ret_base
        metrics_df["Ghost_Realized_Return"] = ret_ghost
        metrics_df["Promoted_Realized_Return"] = ret_promoted
        _ov_gross_days = float(exec_ov.abs().sum(axis=1).mean())
        # ---- Survivorship (SYN) attribution ---------------------------------
        _t = res_live.trades
        if len(_t):
            _is_syn = _t["Asset"].astype(str).str.startswith("SYN")
            if _is_syn.any():
                _s = _t[_is_syn]
                _pnl, _tot = float(_s["PnL"].sum()), float(_t["PnL"].sum())
                _lng = float(_s[_s["Direction"] == "Long"]["PnL"].sum())
                _sht = float(_s[_s["Direction"] == "Short"]["PnL"].sum())
                print(f"[Attribution] SYN names: {len(_s)}/{len(_t)} trades | "
                      f"PnL {_pnl:+,.0f} (Long {_lng:+,.0f} / Short {_sht:+,.0f}) | "
                      f"share {abs(_pnl)/max(1.0,abs(_tot))*100:.0f}% of |total PnL|",
                      flush=True)
        if _ov_gross_days > 1e-12:
            print(f"[Attribution] Override sleeve: mean gross {_ov_gross_days:.3f} | "
                  f"base-only total {(res_base.value.iloc[-1]/params['initial_capital']-1)*100:+.2f}% | "
                  f"with-override total {(res_live.value.iloc[-1]/params['initial_capital']-1)*100:+.2f}%",
                  flush=True)

        # ---- Post-hoc circuit breaker / early-stop on REAL P&L --------------
        _cb = bool(params.get("circuit_breaker_enabled", False))
        if _cb or bool(params.get("early_stop_enabled", False)):
            from modules.risk.progressive import check_floors
            if _cb:
                _p_minp = int(params.get("circuit_breaker_min_periods", 1))
                _p_dd = float(params.get("circuit_breaker_max_dd", 10.0))
                _p_loss = float(params.get("circuit_breaker_max_period_loss", 6.0))
                _p_strk = int(params.get("circuit_breaker_max_loss_streak", 8))
                _p_sigwin, _p_sigmin = 3, 10 ** 9
                _p_ck, _p_sh, _p_so = 10 ** 9, -10 ** 9, -10 ** 9
                _p_win = None
            else:
                _p_minp = int(params.get("early_stop_min_periods", 5))
                _p_dd = float(params.get("early_stop_max_drawdown", 8.0))
                _p_loss = float(params.get("early_stop_max_period_loss", 3.0))
                _p_strk = int(params.get("early_stop_max_loss_streak", 6))
                _p_sigwin = int(params.get("early_stop_signal_window", 20))
                _p_sigmin = int(params.get("early_stop_signal_min_periods", 15))
                _p_ck = float(params.get("early_stop_cum_sigma_k", 2.0))
                _p_sh = float(params.get("early_stop_min_sharpe", -1.0))
                _p_so = float(params.get("early_stop_min_sortino", -1.0))
                _p_win = params.get("early_stop_min_win_rate", None)

            _reb = rebal_dates.intersection(ret_live.index)
            _per, _per_dates, _last = [], [], ret_live.index[0]
            for _d in _reb:
                _seg = ret_live.loc[(ret_live.index > _last) & (ret_live.index <= _d)]
                if len(_seg):
                    _per.append(float((1 + _seg).prod() - 1))
                    _per_dates.append(_d)
                    _last = _d
            _kill_date, _es_info = None, None
            for _n in range(1, len(_per) + 1):
                _chk = check_floors(
                    _per[:_n], _p_minp, _p_dd, _p_loss, _p_strk,
                    _p_sigwin, _p_sigmin, _p_ck, _p_sh, _p_so, _p_win,
                    periods_per_year=252.0 / max(
                        1, int(params.get("early_stop_rebal_days", 5))))
                if _chk and _chk.get("kill"):
                    _kill_date = _per_dates[_n - 1]
                    _es_info = _chk
                    break
            _lbl = "CIRCUIT BREAKER" if _cb else "EARLY-STOP"
            if _kill_date is not None:
                exec_w2 = exec_w.copy()
                exec_w2.loc[exec_w2.index > _kill_date, :] = 0.0
                res_live = run_target_weights(exec_close, exec_open, exec_w2, **eng)
                ret_live = res_live.value.pct_change().fillna(0.0)
                gross_exp = res_live.gross_exposure
                metrics_df["Live_Realized_Return"] = ret_live
                master_w.loc[master_w.index > _kill_date, :] = 0.0
                ctx["early_stop"] = {"enabled": True, "killed": True,
                                     "info": _es_info, "periods_completed": _es_info["periods"]}
                print(
                    f"\n  *** {_lbl} (actual P&L) @ {_kill_date.date()} after "
                    f"{_es_info['periods']} periods | cum={_es_info['cum_return_%']:.2f}% "
                    f"sharpe={_es_info['sharpe']:.2f} DD={_es_info['drawdown_%']:.1f}% "
                    f"| {', '.join(_es_info['reasons'])} ***",
                    flush=True,
                )
            else:
                ctx["early_stop"] = {"enabled": True, "killed": False,
                                     "info": None, "periods_completed": len(_per)}
                print(f"[Attribution] {_lbl} (actual P&L): SURVIVED "
                      f"{len(_per)} periods.", flush=True)

        # ---- Override sleeve contribution (post kill, authoritative) --------
        if _ov_gross_days > 1e-12:
            _bt = (res_base.value.iloc[-1] / params["initial_capital"] - 1.0) * 100.0
            _ct = (res_live.value.iloc[-1] / params["initial_capital"] - 1.0) * 100.0
            print(f"[Attribution] OVERRIDE sleeve: mean gross {_ov_gross_days:.3f} | "
                  f"BASE-only {_bt:+.2f}% | with override {_ct:+.2f}% | "
                  f"delta {_ct - _bt:+.2f}pp", flush=True)

        # ---- Annualised returns / Sortinos (same formulas as before) --------
        ann_factor = 252.0 / max(1, len(ret_live))

        def _ann_ret(vs):
            return (
                ((1.0 + ((vs.iloc[-1] / params["initial_capital"]) - 1.0)) ** ann_factor) - 1.0
            ) * 100.0

        ar_ghost, ar_promoted, ar_live = (
            _ann_ret(res_ghost.value), _ann_ret(res_promoted.value), _ann_ret(res_live.value))
        sr_ghost = compute_sortino_ratio(ret_ghost)
        sr_promoted = compute_sortino_ratio(ret_promoted)
        sr_live = compute_sortino_ratio(ret_live)

        # ---- Neutrality ------------------------------------------------------
        nd = MarketNeutralityDiagnosticEngine(
            max_beta=params["max_net_beta_exposure"],
            max_r2=float(params.get("neutrality_max_r2", 0.005)),
            min_regime_obs=int(params.get("neutrality_min_regime_obs", 10)),
        )
        neut_df, verdict = nd.analyze(
            strategy_returns=ret_live, benchmark_returns=market_ret.iloc[start:])

        # ---- Tearsheets ------------------------------------------------------
        ts = CommitteeTearsheetEngine(params)
        committee_report = ts.generate_tearsheets(prices, pair_w_mats, trade_entry_log)

        # ---- Portfolio stats -------------------------------------------------
        stats_df = compute_stats(res_live, float(gross_exp.max()))

        # ---- Risk-free benchmark (SOFR) --------------------------------------
        try:
            _rf = sofr_daily_returns(ret_live.index)
            _bs = benchmark_summary(ret_live, _rf)
            if _bs:
                stats_df["Benchmark Return [%]"] = _bs["Benchmark Return [%]"]
                stats_df["Excess Return vs SOFR [%]"] = _bs["Excess Return [%]"]
                stats_df["Annualised Excess [%]"] = _bs["Annualised Excess [%]"]
                stats_df["Information Ratio"] = _bs["Information Ratio"]
        except Exception as _exc:
            print(f"[Attribution] SOFR benchmark failed ({_exc})", flush=True)

        # ---- Progressive diagnostics ----------------------------------------
        progressive_df = pd.DataFrame()
        try:
            from modules.risk.progressive import progressive_metrics, print_progress_table
            _rf_series = sofr_daily_returns(ret_live.index)
            progressive_df = progressive_metrics(ret_live, rebal_dates, _rf_series)
            print_progress_table(
                progressive_df, checkpoint_every=5,
                title="PROGRESSIVE (PER-REBALANCE) DIAGNOSTICS — PAIRWISE ENGINE",
            )
        except Exception as _exc:
            print(f"[Attribution] progressive metrics failed ({_exc})", flush=True)

        # ---- MOO blotter -----------------------------------------------------
        moo = LiveMOOOrderGenerator(
            min_order_usd=float(params.get("moo_min_order_usd", 500.0)),
            max_adv_part=params.get("max_adv_participation", 0.015),
        )
        moo_blotter = moo.generate_blotter(
            target_weights=master_w.ffill().fillna(0.0).iloc[-1],
            current_positions={},
            latest_prices=prices.iloc[-1],
            rolling_adv_shares=(
                prices.iloc[-int(params.get("adv_window_days", 20)):].mean()
                * float(params.get("adv_shares_mult", 50_000))
            ).fillna(float(params.get("adv_fill_value", 1_000_000))),
            portfolio_nav=params["initial_capital"],
        )

        summary = {
            "stats_df": stats_df,
            "progressive_metrics_df": progressive_df,
            "ann_ret_ghost": ar_ghost, "sortino_ghost": sr_ghost,
            "ann_ret_promoted": ar_promoted, "sortino_promoted": sr_promoted,
            "ann_ret_live": ar_live, "sortino_live": sr_live,
            "ret_delta_parametric": ar_promoted - ar_ghost,
            "sortino_delta_parametric": sr_promoted - sr_ghost,
            "ret_delta_optimizer": ar_live - ar_promoted,
            "sortino_delta_optimizer": sr_live - sr_promoted,
            "verdict": verdict,
            "neutrality_metrics_df": neut_df,
            "override_gross_mean": _ov_gross_days,
            "base_total_pct": (res_base.value.iloc[-1] / params["initial_capital"] - 1.0) * 100.0,
            "override_total_pct": (res_live.value.iloc[-1] / params["initial_capital"] - 1.0) * 100.0,
        }

        ctx["committee_report"] = committee_report
        ctx["portfolio_result"] = res_live
        ctx["metrics_df"] = metrics_df
        ctx["moo_blotter"] = moo_blotter
        ctx["summary_data"] = summary
