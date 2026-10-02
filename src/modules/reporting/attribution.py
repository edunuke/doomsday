"""
ATTRIBUTION STAGE

Post-loop: shift weights for execution lag, run vectorbt backtests
(live / ghost / promoted), compute annualised returns and Sortino ratios,
run CAPM neutrality diagnostic, generate pair tearsheets,
build the MOO order blotter, and assemble the summary dictionary.

Context produced (final)
------------------------
committee_report : pd.DataFrame  — per-pair tearsheets
net_portfolio    : vbt.Portfolio — live portfolio object
metrics_df       : pd.DataFrame  — merged rebalance metrics + daily returns
moo_blotter      : pd.DataFrame  — actionable MOO orders
summary_data     : dict          — attribution & neutrality summary
"""

import numpy as np
import pandas as pd

from modules import PipelineStage
from modules.execution.backtest_engine import VectorbtBacktestEngine
from modules.reporting.tearsheet import CommitteeTearsheetEngine
from modules.risk.neutrality import MarketNeutralityDiagnosticEngine
from modules.execution.order_generator import LiveMOOOrderGenerator
from modules.processing.utils import compute_sortino_ratio


class AttributionStage(PipelineStage):
    """Stage 5: backtests, attribution, MOO blotter."""

    def run(self, ctx: dict) -> None:
        params = self.params
        start = ctx["start_idx"]
        prices = ctx["prices_full"]
        open_full = ctx["open_full"]
        dag = ctx["dag_df"]
        market_ret = ctx["market_ret"]

        master_w = ctx["master_weights"]
        ghost_w = ctx["ghost_weights"]
        promoted_w = ctx["promoted_weights"]
        pair_w_mats = ctx["pair_weight_matrices"]
        metrics_history = ctx["metrics_history"]
        trade_entry_log = ctx["trade_entry_log"]

        # ---- Shift weights for execution lag --------------------------------
        exec_w = master_w.shift(1).iloc[start:]
        exec_ghost = ghost_w.shift(1).iloc[start:]
        exec_promoted = promoted_w.shift(1).iloc[start:]

        for p in pair_w_mats:
            pair_w_mats[p] = pair_w_mats[p].ffill().fillna(0.0).shift(1)

        rebal_metrics = pd.DataFrame(metrics_history).set_index("Date")
        trade_cols = dag.columns
        exec_close = prices[master_w.columns].iloc[start:]
        exec_open = open_full[prices.columns].iloc[start:]

        # Merge metrics
        metrics_df = pd.DataFrame(index=exec_close.index).join(rebal_metrics, how="left")
        metrics_df["rebalanced"] = metrics_df["rebalanced"].fillna(0).astype(int)
        fill_cols = [
            "Ghost_Size", "Promoted_Size", "Live_Size", "Live_Base",
            "Live_Override", "Opt_Exp_Variance", "Effective_Gross",
            "Mean_Innovation_Z", "Mean_Parametric_Alpha",
        ]
        for c in fill_cols:
            if c in metrics_df.columns:
                metrics_df[c] = metrics_df[c].ffill().fillna(0.0)

        # ---- Backtests -------------------------------------------------------
        bt = VectorbtBacktestEngine(params)
        pf_live, val_live, gross_exp = bt.run_backtest(exec_close, exec_open, exec_w)
        _, val_ghost, _ = bt.run_backtest(exec_close[trade_cols], exec_open[trade_cols], exec_ghost)
        _, val_promoted, _ = bt.run_backtest(
            exec_close[trade_cols], exec_open[trade_cols], exec_promoted,
        )

        ret_live = val_live.pct_change().fillna(0.0)
        ret_ghost = val_ghost.pct_change().fillna(0.0)
        ret_promoted = val_promoted.pct_change().fillna(0.0)

        metrics_df["Live_Realized_Return"] = ret_live
        metrics_df["Ghost_Realized_Return"] = ret_ghost
        metrics_df["Promoted_Realized_Return"] = ret_promoted

        # ---- POST-HOC EARLY-STOP on the ACTUAL realized P&L ------------------
        # Applied here (not inside the walk-forward) so the kill metrics use the
        # EXACT engine P&L — T+1 open fills, fees, borrow included — instead of
        # an internal mark-to-market approximation.
        _cb = bool(params.get("circuit_breaker_enabled", False))
        if _cb or bool(params.get("early_stop_enabled", False)):
            from modules.risk.progressive import check_floors
            # CIRCUIT BREAKER (risk overlay): drawdown/tail limits ONLY.
            # It does NOT judge edge — a rolling-Sharpe kill is pro-cyclical and
            # exits late-arriving edges at their worst point. Edge floors are a
            # research-screening heuristic, not a live risk control.
            if _cb:
                _p_minp = int(params.get("circuit_breaker_min_periods", 1))
                _p_dd   = float(params.get("circuit_breaker_max_dd", 10.0))
                _p_loss = float(params.get("circuit_breaker_max_period_loss", 6.0))
                _p_strk = int(params.get("circuit_breaker_max_loss_streak", 8))
                _p_sigwin, _p_sigmin = 3, 10**9      # window 3, never reached -> SIGNAL off
                _p_ck, _p_sh, _p_so = 10**9, -10**9, -10**9
                _p_win = None
            else:
                _p_minp = int(params.get("early_stop_min_periods", 5))
                _p_dd   = float(params.get("early_stop_max_drawdown", 8.0))
                _p_loss = float(params.get("early_stop_max_period_loss", 3.0))
                _p_strk = int(params.get("early_stop_max_loss_streak", 6))
                _p_sigwin = int(params.get("early_stop_signal_window", 20))
                _p_sigmin = int(params.get("early_stop_signal_min_periods", 15))
                _p_ck = float(params.get("early_stop_cum_sigma_k", 2.0))
                _p_sh = float(params.get("early_stop_min_sharpe", -1.0))
                _p_so = float(params.get("early_stop_min_sortino", -1.0))
                _p_win = params.get("early_stop_min_win_rate", None)
            _reb = metrics_df.index[metrics_df["rebalanced"] == 1]
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
                        1, int(params.get("early_stop_rebal_days", 5))),
                )
                if _chk and _chk.get("kill"):
                    _kill_date = _per_dates[_n - 1]
                    _es_info = _chk
                    break
            _lbl = "CIRCUIT BREAKER" if _cb else "EARLY-STOP"
            if _kill_date is not None:
                exec_w2 = exec_w.copy()
                exec_w2.loc[exec_w2.index > _kill_date, :] = 0.0
                pf_live, val_live, gross_exp = bt.run_backtest(exec_close, exec_open, exec_w2)
                ret_live = val_live.pct_change().fillna(0.0)
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
                print(f"  [flattened after {_kill_date.date()}]", flush=True)
            else:
                ctx["early_stop"] = {"enabled": True, "killed": False,
                                     "info": None, "periods_completed": len(_per)}
                print(f"[Attribution] {_lbl} (actual P&L): SURVIVED "
                      f"{len(_per)} periods.", flush=True)

        ann_factor = 252.0 / max(1, len(ret_live))

        def _ann_ret(vs):
            return (
                ((1.0 + ((vs.iloc[-1] / params["initial_capital"]) - 1.0)) ** ann_factor) - 1.0
            ) * 100.0

        ar_ghost = _ann_ret(val_ghost)
        ar_promoted = _ann_ret(val_promoted)
        ar_live = _ann_ret(val_live)

        sr_ghost = compute_sortino_ratio(ret_ghost)
        sr_promoted = compute_sortino_ratio(ret_promoted)
        sr_live = compute_sortino_ratio(ret_live)

        # ---- Neutrality diagnostic -------------------------------------------
        nd = MarketNeutralityDiagnosticEngine(
            max_beta=params["max_net_beta_exposure"],
            max_r2=float(params.get("neutrality_max_r2", 0.005)),
            min_regime_obs=int(params.get("neutrality_min_regime_obs", 10)),
        )
        neut_df, verdict = nd.analyze(
            strategy_returns=ret_live, benchmark_returns=market_ret.iloc[start:],
        )

        # ---- Committee tearsheets --------------------------------------------
        ts = CommitteeTearsheetEngine(params)
        committee_report = ts.generate_tearsheets(prices, pair_w_mats, trade_entry_log)

        # ---- Portfolio stats -------------------------------------------------
        # vectorbt .stats() can raise on short/irregular windows (e.g. pandas
        # freq inference quirks in vbt 1.0 + pandas 2.2). Fall back to a minimal
        # hand-rolled summary so the run never dies at reporting time.
        try:
            stats_df = pf_live.stats()
        except Exception as exc:
            print(f"[Attribution] vectorbt stats failed, using minimal stats ({exc})", flush=True)
            idx = val_live.index
            stats_df = pd.Series(
                {
                    "Start": idx[0],
                    "End": idx[-1],
                    "Period": len(idx),
                    "Start Value": params["initial_capital"],
                    "End Value": val_live.iloc[-1],
                    "Total Return [%]": float(ret_live.sum() * 100.0),
                    "Max Drawdown [%]": float(
                        ((1 - val_live / val_live.cummax()).max()) * 100.0
                    ),
                    "Total Trades": int(pf_live.trades.count()),
                    "Expectancy": float(
                        pf_live.trades.expectancy()
                        if pf_live.trades.count() > 0
                        else 0.0
                    ),
                    "Win Rate [%]": float(
                        (pf_live.trades.winning.count() / pf_live.trades.count() * 100.0)
                        if pf_live.trades.count() > 0
                        else 0.0
                    ),
                }
            )
        stats_df["Max Gross Exposure [%]"] = float(gross_exp.max() * 100.0)

        # ---- Risk-free benchmark (SOFR) --------------------------------------
        # A market-neutral book has no net market exposure, so the only valid
        # benchmark is the risk-free rate. Override vbt's default equity
        # (equal-weight) benchmark and add risk-free-relative interpretation.
        try:
            from modules.risk.benchmark import sofr_daily_returns, benchmark_summary
            _rf = sofr_daily_returns(ret_live.index)
            _bs = benchmark_summary(ret_live, _rf)
            if _bs:
                stats_df["Benchmark Return [%]"] = _bs["Benchmark Return [%]"]
                stats_df["Excess Return vs SOFR [%]"] = _bs["Excess Return [%]"]
                stats_df["Annualised Excess [%]"] = _bs["Annualised Excess [%]"]
                stats_df["Information Ratio"] = _bs["Information Ratio"]
        except Exception as _exc:
            print(f"[Attribution] SOFR benchmark failed ({_exc})", flush=True)

        # ---- Progressive (per-rebalance) diagnostics + early-stop rule --------
        progressive_df = pd.DataFrame()
        try:
            from modules.risk.progressive import progressive_metrics, print_progress_table
            _rebal = metrics_df.index[metrics_df["rebalanced"] == 1]
            _rf_series = sofr_daily_returns(ret_live.index)
            progressive_df = progressive_metrics(ret_live, _rebal, _rf_series)
            print_progress_table(
                progressive_df, checkpoint_every=5,
                title="PROGRESSIVE (PER-REBALANCE) DIAGNOSTICS — PAIRWISE ENGINE",
            )
        except Exception as _exc:
            print(f"[Attribution] progressive metrics failed ({_exc})", flush=True)

        # ---- Early-stop status (enforced in the walk-forward) ----------------
        _es = ctx.get("early_stop", {}) if isinstance(ctx, dict) else {}
        if _es.get("enabled"):
            print("\n" + "=" * 80, flush=True)
            print("LIVE EARLY-STOP STATUS", flush=True)
            print("=" * 80, flush=True)
            if _es.get("killed") and _es.get("info"):
                i = _es["info"]
                print(f"  RESULT     : KILLED — trading terminated after "
                      f"{i['periods']} periods", flush=True)
                print(f"  win_rate   : {i['win_rate']:.1f}%", flush=True)
                print(f"  sharpe     : {i['sharpe']:.2f}", flush=True)
                print(f"  concentration: {i['concentration']:.2f}", flush=True)
                print(f"  reasons    : {', '.join(i['reasons'])}", flush=True)
            else:
                print(f"  RESULT     : SURVIVED — floors held over "
                      f"{_es.get('periods_completed', 0)} periods", flush=True)
            print("=" * 80, flush=True)

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

        # ---- Summary dict ----------------------------------------------------
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
        }

        ctx["committee_report"] = committee_report
        ctx["net_portfolio"] = pf_live
        ctx["metrics_df"] = metrics_df
        ctx["moo_blotter"] = moo_blotter
        ctx["summary_data"] = summary