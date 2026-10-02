"""
REPORTING STAGE

Last stage of the pipeline: prints the execution summary to stdout,
generates diagnostic matplotlib tearsheets, and exports CSVs + config
to a results/{run}/ folder.

Consumes from ctx:
  - metrics_df, committee_report, portfolio_result, moo_blotter, summary_data
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


def _cohort_report(metrics_df: pd.DataFrame, config_params: dict):
    """Short-cohort robustness: split the live realized return series into
    regime cohorts (default pre/post 2022) and report Sharpe, Sortino, DD
    per cohort. An aggregate Sharpe can mask regime decay; this makes the
    split explicit in the committee output.

    Config: `cohort_splits` = [[start, end], ...] with "" meaning open-ended,
    e.g. [["", "2021-12-31"], ["2022-01-01", ""]].
    """
    col = "Live_Realized_Return"
    if col not in metrics_df.columns:
        return None
    r = metrics_df[col].dropna()
    if len(r) < 60:
        return None
    if not isinstance(r.index, pd.DatetimeIndex):
        r.index = pd.to_datetime(r.index)
    splits = config_params.get("cohort_splits") or [["", "2021-12-31"], ["2022-01-01", ""]]
    rows = []
    for start, end in splits:
        mask = pd.Series(True, index=r.index)
        if start:
            mask &= r.index >= pd.Timestamp(start)
        if end:
            mask &= r.index <= pd.Timestamp(end)
        rc = r[mask]
        if len(rc) < 20:
            continue
        cum = (1.0 + rc).prod() - 1.0
        ann = (1.0 + cum) ** (252.0 / len(rc)) - 1.0
        vol = rc.std() * np.sqrt(252.0)
        sharpe = (rc.mean() / rc.std() * np.sqrt(252.0)) if rc.std() > 0 else np.nan
        downside = rc[rc < 0]
        sortino = (rc.mean() * 252.0 / (downside.std() * np.sqrt(252.0))
                   if len(downside) > 1 and downside.std() > 0 else np.nan)
        equity = (1.0 + rc).cumprod()
        dd = float((equity / equity.cummax() - 1.0).min())
        rows.append({
            "Cohort": f"{start or 'start'} → {end or 'end'}",
            "Days": len(rc),
            "Total_Ret_%": round(100 * cum, 2),
            "Ann_Ret_%": round(100 * ann, 2),
            "Ann_Vol_%": round(100 * vol, 2),
            "Sharpe": round(sharpe, 3) if np.isfinite(sharpe) else np.nan,
            "Sortino": round(sortino, 3) if np.isfinite(sortino) else np.nan,
            "Max_DD_%": round(100 * dd, 2),
        })
    return pd.DataFrame(rows) if rows else None


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


def _write_env_stamp(output_dir: str, config_params: dict) -> None:
    """Reproducibility stamp: interpreter, numeric-stack versions, platform,
    a code-tree hash and a config hash, plus a pinned requirements.txt.

    Why: run results depend on the interpreter/numeric stack (verified — the
    same config produced different SLSQP tie-breaks under Python 3.11.5/numpy
    1.26 vs 3.14.6/numpy 2.4). Without this record a run is not reproducible.
    """
    import hashlib
    import json as _json
    import platform as _platform
    import sys as _sys
    import datetime as _dt

    def _ver(dist: str) -> str:
        try:
            import importlib.metadata as _md
            return _md.version(dist)
        except Exception:
            return "not-installed"

    # Code-tree hash: every .py under modules/ + main.py (order-independent).
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _h = hashlib.sha256()
    _files = []
    for _dp, _dn, _fn in os.walk(os.path.join(_root, "modules")):
        _dn[:] = [d for d in _dn if d != "__pycache__"]
        for _f in _fn:
            if _f.endswith(".py"):
                _files.append(os.path.join(_dp, _f))
    _main = os.path.join(_root, "main.py")
    if os.path.exists(_main):
        _files.append(_main)
    for _p in sorted(_files):
        _h.update(os.path.relpath(_p, _root).encode())
        _h.update(open(_p, "rb").read())
    tree_hash = _h.hexdigest()[:16]

    cfg_hash = hashlib.sha256(
        _json.dumps(config_params, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]

    env = {
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "python": {
            "version": _sys.version.split()[0],
            "executable": _sys.executable,
            "implementation": _platform.python_implementation(),
        },
        "packages": {d: _ver(d) for d in [
            "numpy", "scipy", "pandas", "statsmodels", "joblib",
            "matplotlib", "scikit-learn", "python-dateutil", "pytz",
            "numexpr", "bottleneck", "yfinance",
        ]},
        "platform": {
            "system": _platform.system(),
            "release": _platform.release(),
            "machine": _platform.machine(),
            "processor": _platform.processor(),
            "platform": _platform.platform(),
        },
        "code_tree_sha256_16": tree_hash,
        "config_sha256_16": cfg_hash,
    }
    with open(os.path.join(output_dir, "run_environment.json"), "w") as f:
        _json.dump(env, f, indent=2)

    # Pinned requirements.txt for this run (full installed distribution set).
    try:
        import importlib.metadata as _md
        dists = sorted({f"{d.metadata['Name']}=={d.version}"
                        for d in _md.distributions()
                        if d.metadata and d.metadata.get("Name")})
        with open(os.path.join(output_dir, "requirements.txt"), "w") as f:
            f.write("# Reproducibility pin for this run's interpreter\n")
            f.write(f"# python {_sys.version.split()[0]}  ({_sys.executable})\n")
            f.write(f"# config_sha256_16={cfg_hash}  code_tree_sha256_16={tree_hash}\n")
            f.write("\n".join(dists) + "\n")
    except Exception as e:
        print(f"[Reporting] requirements.txt snapshot failed: {e}", flush=True)


def _export_csvs_and_plots(
    output_dir: str,
    metrics_df: pd.DataFrame,
    config_params: dict,
    initial_capital: float = 250_000.0,
    committee_report=None,
    portfolio_result=None,
    moo_blotter=None,
    stats_df=None,
    progressive_df=None,
    effective_params=None,
    neutrality_metrics_df=None,
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
    # The orchestrator normally pre-creates these; make it defensive so a
    # missing/cleaned dir can't crash the final plot save (seen when runs are
    # launched outside the orchestrator's normal dir setup).
    os.makedirs(csv_dir, exist_ok=True)
    os.makedirs(imgs_dir, exist_ok=True)

    # Reproducibility stamp (§ env record + pinned requirements).
    _write_env_stamp(output_dir, config_params)
    print(f"\n[Reporting] Saving experiment to: '{output_dir}/'", flush=True)

    # ---- Config snapshot ----------------------------------------------------
    config_path = os.path.join(output_dir, "config.json")
    with open(config_path, "w") as f:
        json.dump(config_params, f, indent=2, default=str)
    print(f"  Saved config -> {config_path}", flush=True)

    # Deterministic-config audit trail: any param overridden at runtime by the
    # walk-forward re-selection is exported here, so config.json + this file
    # fully describe what executed.
    try:
        _eff = effective_params
        if _eff:
            ep = os.path.join(output_dir, "effective_params.json")
            with open(ep, "w") as f:
                json.dump(_eff, f, indent=2, default=str)
            print(f"  Saved effective params -> {ep} ({len(_eff)} overrides)", flush=True)
    except Exception as e:
        print(f"  (effective_params export skipped: {e})", flush=True)

    # ---- CSV exports --------------------------------------------------------
    metrics_df.to_csv(os.path.join(csv_dir, "metrics.csv"))
    print(f"  Saved metrics -> {os.path.join(csv_dir, 'metrics.csv')}", flush=True)

    if committee_report is not None and not committee_report.empty:
        cr_path = os.path.join(csv_dir, "committee_report.csv")
        committee_report.to_csv(cr_path, index=False)
        print(f"  Saved tearsheets -> {cr_path}", flush=True)

    # ---- Neutrality metrics export (alpha + betas) --------------------------
    try:
        if neutrality_metrics_df is not None and not neutrality_metrics_df.empty:
            nm_path = os.path.join(csv_dir, "neutrality_metrics.csv")
            neutrality_metrics_df.to_csv(nm_path, index=False)
            print(f"  Saved neutrality metrics -> {nm_path}", flush=True)
    except Exception as e:
        print(f"  (neutrality metrics export skipped: {e})", flush=True)

    if moo_blotter is not None:
        mb_path = os.path.join(csv_dir, "moo_blotter.csv")
        moo_blotter.to_csv(mb_path, index=False)
        print(f"  Saved blotter -> {mb_path}", flush=True)

    if progressive_df is not None and not getattr(progressive_df, "empty", True):
        pp = os.path.join(csv_dir, "progressive_metrics.csv")
        progressive_df.to_csv(pp)
        print(f"  Saved progressive metrics -> {pp}", flush=True)

    if portfolio_result is not None:
        try:
            tr_path = os.path.join(csv_dir, "trade_records.csv")
            portfolio_result.trades.to_csv(tr_path, index=False)
            ps_path = os.path.join(csv_dir, "portfolio_stats.csv")
            if stats_df is not None:
                stats_df.to_csv(ps_path)
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
        portfolio_result = ctx.get("portfolio_result")
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

        # Short-cohort robustness: pre/post-split Sharpe et al. saved and
        # printed so regime decay is explicit in the committee record.
        cohort_df = _cohort_report(metrics, self.params)
        if cohort_df is not None and not cohort_df.empty:
            csv_dir = os.path.join(output_dir, "csv")
            os.makedirs(csv_dir, exist_ok=True)
            cohort_df.to_csv(os.path.join(csv_dir, "cohort_report.csv"), index=False)
            print("\n" + "=" * 85, flush=True)
            print("SHORT-COHORT ROBUSTNESS (LIVE REALIZED, PER COHORT)", flush=True)
            print("=" * 85, flush=True)
            print(cohort_df.to_string(index=False), flush=True)
            print("=" * 85, flush=True)

        # ---- Empty-book guard -------------------------------------------------
        # A run that never trades reports 0.0 everywhere and looks like a
        # "flat, safe" strategy. In practice this is almost always a gate
        # misconfiguration (e.g. full-mode adaptive_min_alpha / Hurst gates
        # calibrated for another universe rejecting every candidate). Make the
        # failure mode loud instead of silent.
        _stats = summary.get("stats_df", pd.DataFrame())
        try:
            _ntrades = int(float(_stats.loc["Total Trades"])) if "Total Trades" in _stats.index else None
        except Exception:
            _ntrades = None
        if _ntrades == 0:
            print("\n" + "!" * 85, flush=True)
            print("!! EMPTY BOOK WARNING: the run made ZERO trades.", flush=True)
            print("!! This is NOT a valid strategy result — it means every candidate", flush=True)
            print("!! was rejected by the entry gates. Check funnel_mode-specific", flush=True)
            print("!! gates: adaptive_min_alpha (friction_clearance_multiplier),", flush=True)
            print("!! max_hurst_exponent, max_half_life, entry_ecdf_percentile,", flush=True)
            print("!! min_override_z. Re-calibrate for this universe before reading", flush=True)
            print("!! the 0.0 Sharpe/return as performance.", flush=True)
            print("!" * 85, flush=True)

        # Export CSVs + config + plots into the pre-created run dir
        _export_csvs_and_plots(
            output_dir=output_dir,
            metrics_df=metrics,
            config_params=self.params,
            initial_capital=initial_capital,
            committee_report=committee_report,
            portfolio_result=portfolio_result,
            moo_blotter=moo_blotter,
            stats_df=summary.get("stats_df"),
            progressive_df=summary.get("progressive_metrics_df"),
            effective_params=ctx.get("effective_params"),
            neutrality_metrics_df=summary.get("neutrality_metrics_df"),
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