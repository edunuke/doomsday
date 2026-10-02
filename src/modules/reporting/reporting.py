"""
REPORTING STAGE

Last stage of the pipeline: prints the execution summary to stdout,
generates diagnostic matplotlib tearsheets, and exports CSVs + config
to a results/{run}/ folder.

Consumes from ctx:
  - metrics_df, committee_report, net_portfolio, moo_blotter, summary_data
"""

import json
import os
from datetime import datetime
from typing import Any, Dict

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from modules import PipelineStage


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_independent_axis(ax, rebalance_dates):
    for r_date in rebalance_dates:
        ax.axvline(x=r_date, color="red", linestyle=":", alpha=0.4, linewidth=1.2)
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")


def _print_summary(
    metrics_df, stats_df, committee_report, moo_blotter,
    ann_ret_ghost, sortino_ghost,
    ann_ret_promoted, sortino_promoted,
    ann_ret_live, sortino_live,
    ret_delta_parametric, sortino_delta_parametric,
    ret_delta_optimizer, sortino_delta_optimizer,
    verdict, neutrality_metrics_df,
):
    print("\n" + "=" * 85, flush=True)
    print("v3.2 RELAXED BETA — FUNNEL CONVERSION ATTRIBUTION", flush=True)
    print("=" * 85, flush=True)
    print(
        f"  {'Funnel Tier / Portfolio Stage':<38} | {'Ann. Return [%]':<18} | {'Sortino Ratio':<18}",
        flush=True,
    )
    print("-" * 85, flush=True)
    print(f"  {'Ghost Portfolio (Raw Universe)':<38} | {ann_ret_ghost:17.2f}% | {sortino_ghost:18.4f}", flush=True)
    print(f"  {'Promoted Portfolio (Parametric Gated)':<38} | {ann_ret_promoted:17.2f}% | {sortino_promoted:18.4f}", flush=True)
    print(f"  {'Live Portfolio (SLSQP Optimized)':<38} | {ann_ret_live:17.2f}% | {sortino_live:18.4f}", flush=True)
    print("-" * 85, flush=True)
    print(f"  {'--> Parametric Selection Delta':<38} | {ret_delta_parametric:+17.2f}% | {sortino_delta_parametric:+18.4f}", flush=True)
    print(f"  {'--> Optimizer Allocation Delta':<38} | {ret_delta_optimizer:+17.2f}% | {sortino_delta_optimizer:+18.4f}", flush=True)
    print("=" * 85, flush=True)

    print("\n" + "=" * 85, flush=True)
    print("MARKET NEUTRALITY DIAGNOSTIC (RELAXED: 5% BETA CAP)", flush=True)
    print("=" * 85, flush=True)
    print(neutrality_metrics_df.to_string(index=False), flush=True)
    print("-" * 85, flush=True)
    print(f"  VERDICT: Market Neutral = {verdict['Market_Neutral']} | Failures: {verdict['Core_Failure_Reason']}", flush=True)
    print("=" * 85, flush=True)

    # ---- Risk-free (SOFR) benchmark interpretation -----------------------
    # A market-neutral book must be judged against cash, not an equity index.
    _bkeys = [
        "Benchmark Return [%]", "Excess Return vs SOFR [%]",
        "Annualised Excess [%]", "Information Ratio",
    ]
    _present = [k for k in _bkeys if k in getattr(stats_df, "index", [])]
    if _present:
        print("\n" + "=" * 85, flush=True)
        print("RISK-FREE (SOFR) BENCHMARK — MARKET-NEUTRAL INTERPRETATION", flush=True)
        print("=" * 85, flush=True)
        print(f"  {'Metric':<32} {'Value':>12}", flush=True)
        print("-" * 85, flush=True)
        for k in _present:
            v = stats_df[k]
            try:
                print(f"  {k:<32} {float(v):>12.3f}", flush=True)
            except (TypeError, ValueError):
                print(f"  {k:<32} {v:>12}", flush=True)
        print("=" * 85, flush=True)

    if committee_report is not None and not committee_report.empty:
        print("\n" + "=" * 85, flush=True)
        print("INVESTMENT COMMITTEE PAIR REVIEW TEARSHEET (ATTRIBUTED BY REGIME)", flush=True)
        print("=" * 85, flush=True)
        print(committee_report.to_string(index=False), flush=True)

    print("\n" + "=" * 85, flush=True)
    print("WALK-FORWARD NET PORTFOLIO STATS (POST-FEES & EXECUTION LAG)", flush=True)
    print("=" * 85, flush=True)
    print(stats_df.to_string(), flush=True)

    print("\n" + "=" * 85, flush=True)
    print("ACTIONABLE MARKET-ON-OPEN (MOO) ORDER BLOTTER FOR NEXT SESSION", flush=True)
    print("=" * 85, flush=True)
    print(moo_blotter.to_string(index=False), flush=True)


def _export_csvs_and_plots(
    output_dir: str,
    metrics_df: pd.DataFrame,
    config_params: dict,
    initial_capital: float = 250_000.0,
    committee_report=None,
    net_portfolio=None,
    moo_blotter=None,
    stats_df=None,
    progressive_df=None,
):
    """Export CSVs, config, and PNG figures to output_dir.

    Structure::
        output_dir/
            config.json
            csv/  metrics.csv, committee_report.csv, ...
            imgs/ funnel_sizing.png, cumulative_performance.png
    """
    csv_dir = os.path.join(output_dir, "csv")
    imgs_dir = os.path.join(output_dir, "imgs")
    print(f"\n[Reporting] Saving experiment to: '{output_dir}/'", flush=True)

    # ---- Config snapshot ----------------------------------------------------
    config_path = os.path.join(output_dir, "config.json")
    with open(config_path, "w") as f:
        json.dump(config_params, f, indent=2, default=str)
    print(f"  Saved config -> {config_path}", flush=True)

    # ---- CSV exports --------------------------------------------------------
    metrics_df.to_csv(os.path.join(csv_dir, "metrics.csv"))
    print(f"  Saved metrics -> {os.path.join(csv_dir, 'metrics.csv')}", flush=True)

    if committee_report is not None and not committee_report.empty:
        cr_path = os.path.join(csv_dir, "committee_report.csv")
        committee_report.to_csv(cr_path, index=False)
        print(f"  Saved tearsheets -> {cr_path}", flush=True)

    if moo_blotter is not None:
        mb_path = os.path.join(csv_dir, "moo_blotter.csv")
        moo_blotter.to_csv(mb_path, index=False)
        print(f"  Saved blotter -> {mb_path}", flush=True)

    if progressive_df is not None and not getattr(progressive_df, "empty", True):
        pp = os.path.join(csv_dir, "progressive_metrics.csv")
        progressive_df.to_csv(pp)
        print(f"  Saved progressive metrics -> {pp}", flush=True)

    if net_portfolio is not None:
        try:
            tr_path = os.path.join(csv_dir, "trade_records.csv")
            net_portfolio.trades.records_readable.to_csv(tr_path, index=False)
            ps_path = os.path.join(csv_dir, "portfolio_stats.csv")
            # Prefer the patched stats (risk-free/SOFR benchmark) so the CSV
            # matches the log; fall back to raw vbt stats otherwise.
            if stats_df is not None:
                stats_df.to_csv(ps_path)
            else:
                net_portfolio.stats().to_csv(ps_path)
            print(f"  Saved trade records -> {tr_path}", flush=True)
            print(f"  Saved portfolio stats -> {ps_path}", flush=True)
        except Exception:
            pass

    # ---- Diagnostic plots ---------------------------------------------------
    df = metrics_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    rebalance_dates = df[df["rebalanced"] == 1].index if "rebalanced" in df.columns else []

    # Sleeve allocation counts
    fig1, ax1 = plt.subplots(figsize=(16, 4))
    if "Live_Base" in df.columns:
        ax1.step(df.index, df["Live_Base"], label="Live Base Sleeve", color="#2a9d8f", where="post", linewidth=2.0)
    if "Live_Override" in df.columns:
        ax1.step(df.index, df["Live_Override"], label="Live Regime Override Sleeve", color="#e76f51", where="post", linewidth=2.0)
    ax1.set_title("v3.2 Active Sleeve Allocation Sizing (Attributed Counts)", fontsize=11, fontweight="bold", loc="left")
    ax1.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0.0, frameon=True)
    _format_independent_axis(ax1, rebalance_dates)
    plt.tight_layout()
    fig1.savefig(os.path.join(imgs_dir, "funnel_sizing.png"), dpi=300, bbox_inches="tight")
    plt.close(fig1)
    print(f"  Saved plot -> {os.path.join(imgs_dir, 'funnel_sizing.png')}", flush=True)

    # Cumulative performance
    fig2, ax2 = plt.subplots(figsize=(16, 5))
    live_cum = (1.0 + df.get("Live_Realized_Return", pd.Series(0.0, index=df.index)).fillna(0.0)).cumprod() * initial_capital
    promoted_cum = (1.0 + df.get("Promoted_Realized_Return", pd.Series(0.0, index=df.index)).fillna(0.0)).cumprod() * initial_capital
    ghost_cum = (1.0 + df.get("Ghost_Realized_Return", pd.Series(0.0, index=df.index)).fillna(0.0)).cumprod() * initial_capital
    ax2.plot(df.index, live_cum, label="Live Realized Value ($)", color="#2a9d8f", linewidth=2.5)
    ax2.plot(df.index, promoted_cum, label="Promoted Shadow Value ($)", color="#e07a5f", linestyle="-.", linewidth=1.5)
    ax2.plot(df.index, ghost_cum, label="Ghost Shadow Value ($)", color="#2b5c8f", linestyle="--", linewidth=1.5)
    ax2.axhline(initial_capital, color="black", linewidth=0.8)
    ax2.set_title("v3.2 Cumulative Performance Horizon & Funnel Attribution ($)", fontsize=11, fontweight="bold", loc="left")
    ax2.get_yaxis().set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:,}".format(int(x))))
    ax2.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0.0, frameon=True)
    _format_independent_axis(ax2, rebalance_dates)
    plt.tight_layout()
    fig2.savefig(os.path.join(imgs_dir, "cumulative_performance.png"), dpi=300, bbox_inches="tight")
    plt.close(fig2)
    print(f"  Saved plot -> {os.path.join(imgs_dir, 'cumulative_performance.png')}", flush=True)


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------

class ReportingStage(PipelineStage):
    """Final stage: prints summary, exports CSVs + config + plots + logs."""

    def run(self, ctx: Dict[str, Any]) -> None:
        # Run dir was created at pipeline START by the orchestrator.
        output_dir = ctx.get("_output_dir") or _get_next_run_dir_fallback()

        metrics = ctx.get("metrics_df")
        if metrics is None or metrics.empty:
            print("[ReportingStage] No metrics — nothing to report.", flush=True)
            self._close_log(ctx)
            return

        summary = ctx.get("summary_data", {})
        committee_report = ctx.get("committee_report")
        net_portfolio = ctx.get("net_portfolio")
        moo_blotter = ctx.get("moo_blotter")
        initial_capital = self.params.get("initial_capital", 250_000.0)

        # Console summary
        _print_summary(
            metrics_df=metrics,
            stats_df=summary.get("stats_df", pd.DataFrame()),
            committee_report=committee_report,
            moo_blotter=moo_blotter,
            ann_ret_ghost=summary.get("ann_ret_ghost", 0.0),
            sortino_ghost=summary.get("sortino_ghost", 0.0),
            ann_ret_promoted=summary.get("ann_ret_promoted", 0.0),
            sortino_promoted=summary.get("sortino_promoted", 0.0),
            ann_ret_live=summary.get("ann_ret_live", 0.0),
            sortino_live=summary.get("sortino_live", 0.0),
            ret_delta_parametric=summary.get("ret_delta_parametric", 0.0),
            sortino_delta_parametric=summary.get("sortino_delta_parametric", 0.0),
            ret_delta_optimizer=summary.get("ret_delta_optimizer", 0.0),
            sortino_delta_optimizer=summary.get("sortino_delta_optimizer", 0.0),
            verdict=summary.get("verdict", {}),
            neutrality_metrics_df=summary.get("neutrality_metrics_df", pd.DataFrame()),
        )

        # Export CSVs + config + plots into the pre-created run dir
        _export_csvs_and_plots(
            output_dir=output_dir,
            metrics_df=metrics,
            config_params=self.params,
            initial_capital=initial_capital,
            committee_report=committee_report,
            net_portfolio=net_portfolio,
            moo_blotter=moo_blotter,
            stats_df=summary.get("stats_df"),
            progressive_df=summary.get("progressive_metrics_df"),
        )

        print(f"[Reporting] Runtime log -> {os.path.join(output_dir, 'logs.txt')}", flush=True)
        print("[ReportingStage] Complete.", flush=True)

        # Live log is already being appended incrementally — just close
        # the file handle and hand stdout back to the terminal.
        self._close_log(ctx)

    # ------------------------------------------------------------------
    def _close_log(self, ctx: Dict[str, Any]) -> None:
        """Close the live log file and restore sys.stdout."""
        capture = ctx.get("_log_capture")
        if capture is not None:
            capture.close_log()
            capture.restore()


def _get_next_run_dir_fallback() -> str:
    """Standalone fallback if the orchestrator did not pre-create a run dir.

    Normally the orchestrator creates results/{N}/ at pipeline start; this
    only triggers if ReportingStage is run standalone (e.g. unit tests).
    """
    from modules.core.runs import get_next_run_dir
    return get_next_run_dir("results")