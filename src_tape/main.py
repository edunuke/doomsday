"""
STAT-ARB v3.2 — MAIN ENTRY POINT

Declares the pipeline as an ordered execution list (flow) and
delegates execution to PipelineOrchestrator.

Usage:
    python main.py                                                      # defaults (quiet)
    python main.py --verbose                                            # show runtime output
    python main.py --download                                           # download data first
    python main.py --download --verbose                                 # download + show output
    python main.py --max_net_beta_exposure 0.03                       # + override params
    python main.py --config path/to/config.json                         # custom config

Adding / removing stages:
    To add a stage, create a class in modules/<subfolder>/ that extends PipelineStage,
    import it here, and insert it into the flow list at the desired position.

Logging:
    All runtime output is ALWAYS captured to results/{run}/logs.txt.
    --verbose additionally mirrors it to the terminal as it runs.
"""

import os

# Pin BLAS/OMP to a single thread BEFORE numpy is imported. The parallel stages
# (alpha cube, cointegration screen) already saturate all cores via joblib; if
# each worker also spawns a multithreaded BLAS pool they oversubscribe the CPU
# and throughput collapses. Must run before any numeric import.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import sys

from modules.core import PipelineOrchestrator
from modules.data import Config, DataLoaderStage, DownloadDataStage
from modules.processing import (
    AlphaCubeStage, AlphaCubeStageV2, AlphaCubeStageV3, AlphaCubeStageV4,
    ResidualizerStage, ResidualizerStageV2, ResidualizerStageV3, ResidualizerStageV4,
)
from modules.execution import WalkForwardStage, WalkForwardStageV2, WalkForwardStageV3
from modules.alpha import AlphaCubeStageV5
from modules.selection import SelectionStage
from modules.reporting import AttributionStage, ReportingStage

# ==============================================================================
# DECLARATIVE PIPELINE FLOW
# ==============================================================================
# Each stage is a class that extends PipelineStage and implements run(ctx).
# The orchestrator instantiates them in order and calls stage.run() sequentially.
#
# DownloadDataStage is conditional: it only executes when --download is passed.
# All other stages always run.
# ==============================================================================
# Component registries — the flow is built from config so versions can be
# swapped without editing code:
#   config["components"] = {"residualizer":"v1|v2|v3",
#                           "alphacube":"v1|v2|v3",
#                           "walkforward":"v1|v2"}
COMPONENTS = {
    "residualizer": {
        "v1": ResidualizerStage,     # SPY-residualise + correlation screen
        "v2": ResidualizerStageV2,   # + variance-ratio reversion screen
        "v3": ResidualizerStageV3,   # + regime-conditional reversion screen
        "v4": ResidualizerStageV4,   # + both-regime reversion screen
    },
    "alphacube": {
        "v1": AlphaCubeStage,        # Kalman innovation z-score
        "v2": AlphaCubeStageV2,      # multi-scale ensemble z
        "v3": AlphaCubeStageV3,      # regime-scaled signal
        "v4": AlphaCubeStageV4,      # OU-implied expected-reversion signal
        "v5": AlphaCubeStageV5,      # + orthogonal sources (dispersion/OU/sector)
    },
    "selection": {
        "v1": SelectionStage,        # walk-forward (trailing-window) param re-selection
    },
    "sizing": {
        "v1": "NoSizing",            # no leverage
        "v2": "VolTargetSizing",     # vol-targeted exposure, capped leverage
    },
    "screener": {
        "v1": "coint_screen",        # Engle-Granger p-value (baseline)
        "v2": "stat_screen",         # Johansen rank + Hurst + half-life
    },
    "stattests": {                    # spread-quality tests (modules/stattests.STAT_TESTS)
        "hurst": "hurst_exponent",           # variance-of-differences Hurst
        "half_life": "half_life_ar1",        # AR(1)/OU half-life
        "variance_ratio": "variance_ratio",  # Lo-MacKinlay VR
        "johansen": "johansen_test",         # trace / max-eigenvalue cointegration
    },
    "walkforward": {
        "v1": WalkForwardStage,      # exits → screening → SLSQP
        "v2": WalkForwardStageV2,    # + regime entry gate
        "v3": WalkForwardStageV3,    # + dynamic book overlay (crowding/quality)
    },
    "allocator": {
        "v1": "ConvexPortfolioOptimizer",   # heuristic caps + SPY overlay
        "v2": "FactorNeutralAllocator",     # + ex-ante factor constraints
    },
    "regime": {
        "v1": "QuantileRegimeDetector",     # double-quantile tail ratio (broken for equities)
        "v2": "MarkovRegimeDetector",       # 2-state Markov switching
        "v3": "VolatilityRegimeDetector",   # realised-vol percentile (recommended)
    },
}


def build_flow(components: dict) -> list:
    """Assemble the pipeline flow from a component-version mapping."""
    c = components or {}
    flow = [
        DownloadDataStage,       # 0. (Conditional) download S&P 500 data
        DataLoaderStage,         # 1. Load & prepare price data
        COMPONENTS["residualizer"][c.get("residualizer", "v1")],
        COMPONENTS["alphacube"][c.get("alphacube", "v1")],
    ]
    if c.get("selection"):
        flow.append(COMPONENTS["selection"][c["selection"]])
    flow += [
        COMPONENTS["walkforward"][c.get("walkforward", "v1")],
        AttributionStage,        # Backtests, neutrality, tearsheets, MOO blotter
        ReportingStage,          # Print summary, export CSVs, plot tearsheets
    ]
    return flow


# Default flow (V1 components) — used only if a config omits `components`.
flow = build_flow({"residualizer": "v1", "alphacube": "v1", "walkforward": "v1"})


def parse_cli_args():
    """Parse --config, --download, --verbose, and extra key=value overrides."""
    parser = argparse.ArgumentParser(description="StatArb v3.2 Pipeline")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to custom config.json (default: configs/config.json)")
    parser.add_argument("--download", action="store_true", default=False,
                        help="Download S&P 500 data from Yahoo Finance before running")
    parser.add_argument("--verbose", action="store_true", default=False,
                        help="Mirror runtime output to the terminal (always saved to logs.txt)")
    parser.add_argument("--use-survivorship-model", dest="use_survivorship",
                        action="store_true", default=False,
                        help="Inject the synthetic delisting panel (survivorship compensation)")
    parser.add_argument("--from-tape", dest="from_tape", type=str, default=None,
                        help="Replay consumers (metrics/execute/report) on an existing "
                             "tape.txt without re-running the decision engine")
    known, unknown = parser.parse_known_args()

    # Extra overrides (everything after known args)
    # Note: a bare '--' may appear as the argparse end-of-options separator;
    # it carries no information for us, so drop it.
    overrides = {}
    i = 0
    while i < len(unknown):
        if unknown[i] == "--":
            i += 1
            continue
        if unknown[i].startswith("--"):
            key = unknown[i].lstrip("-")
            if i + 1 < len(unknown) and not unknown[i + 1].startswith("--"):
                val = unknown[i + 1]
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
                overrides[key] = val
                i += 2
            else:
                overrides[key] = True
                i += 1
        else:
            i += 1

    return known.config, known.download, known.verbose, overrides, known.from_tape, known.use_survivorship


def run_from_tape(tape_path: str, config_path: str) -> int:
    """Run the tape consumers without re-deciding."""
    from scripts import tape_metrics, tape_execute, tape_report, tape_audit

    print(f"[from-tape] Replaying {tape_path} (config: {config_path or 'default'})", flush=True)
    stats = tape_metrics.compute_metrics(tape_path, config_path or "configs/config_best.json")
    print("\n" + "=" * 78, flush=True)
    print("TAPE METRICS", flush=True)
    print("=" * 78, flush=True)
    for k, v in stats.items():
        print(f"  {k:<28}: {v}", flush=True)

    tape_report.render(tape_path)
    tape_audit.audit(tape_path)
    return 0


def main():
    config_path, do_download, verbose, overrides, from_tape, use_synth = parse_cli_args()

    # --- Survivorship compensation: inject synthetic delisting panel --------
    # Activation: CLI flag/override OR the config key `use_survivorship_model`
    # (the config key was previously ignored — only overrides were checked).
    _want_synth = bool(use_synth) or bool(overrides.get("use_survivorship_model"))
    if not _want_synth:
        try:
            _want_synth = bool(
                Config(config_path=config_path, overrides=overrides)
                .get("use_survivorship_model"))
        except Exception:
            _want_synth = False
    if _want_synth:
        import os as _os, json as _json, hashlib as _hashlib
        import datetime as _dt, pandas as _pd
        from modules.survivorship import SurvivorshipModel
        _cfg0 = Config(config_path=config_path, overrides=overrides)
        _px_path = _cfg0.get("price_csv")
        _base = _os.path.basename(_px_path)

        # Guard: the config already points at a compensated panel. Generating
        # again would derive a bogus name (…_survivorship_survivorship.csv) and
        # needlessly rewrite artifacts, so leave the configured paths as-is.
        if _base.endswith("_survivorship.csv"):
            print(f"[survivorship] price_csv already references a compensated "
                  f"panel ({_base}); skipping generation.", flush=True)
        else:
          # ---- Universe-aware output names ----------------------------------
          # New universes set `universe` (e.g. "ILF") and get distinct files:
          #   data/<UNIVERSE>_survivorship.csv
          #   data/<UNIVERSE>_constituents_survivorship.csv
          # Legacy configs (no `universe`) keep the historical naming exactly, so
          # existing US artifacts are untouched.
          _uni = _cfg0.get("universe")
          if _uni:
              _scsv = _os.path.join("data", f"{_uni}_survivorship.csv")
              _scons = _os.path.join("data", f"{_uni}_constituents_survivorship.csv")
          else:
              _scsv = _os.path.join("data", _base[:-4] + "_survivorship.csv")
              _scons = "data/constituents_survivorship.csv"
          _meta_path = _scsv.replace("_survivorship.csv", "_survivorship_meta.json")

          # ---- Calibration params (region/sector/TTF) + reuse signature -------
          _seed = int(_cfg0.get("survivorship_seed", 7))
          _params = {}
          _pf = _cfg0.get("survivorship_params_file")
          if _pf and _os.path.exists(_pf):
              _params = _json.load(open(_pf))
              print(f"[survivorship] calibration file loaded: {_pf}", flush=True)
          elif _pf:
              print(f"[survivorship] WARNING calibration file not found: {_pf}", flush=True)
          _params = {**_params, "market_col": _cfg0.get("market_ticker", "SPY")}
          _sig = _hashlib.sha256(_json.dumps(
              {"seed": _seed, "params": _params, "price": _base},
              sort_keys=True, default=str).encode()).hexdigest()[:16]

          # ---- Decide: reuse (validated) / regenerate / refuse ---------------
          _need, _reason = False, ""
          if not _os.path.exists(_scsv):
              _need, _reason = True, "panel missing"
          elif not _os.path.exists(_meta_path):
              # No sidecar -> cannot validate. Reuse (legacy behaviour) but warn.
              if bool(_cfg0.get("survivorship_require_explicit")):
                  raise SystemExit(
                      f"[survivorship] require_explicit set and no validation sidecar "
                      f"for {_scsv}; pre-generate it or remove the flag.")
              print(f"[survivorship] WARNING reusing unvalidated panel (no sidecar "
                    f"{_meta_path}); regeneration not attempted.", flush=True)
          else:
              try:
                  _m = _json.load(open(_meta_path))
                  if _m.get("signature") != _sig:
                      _need, _reason = True, "seed/params mismatch vs sidecar"
              except Exception as e:
                  _need, _reason = True, f"unreadable sidecar ({e})"

          if _need:
              if bool(_cfg0.get("survivorship_require_explicit")):
                  raise SystemExit(
                      f"[survivorship] panel invalid ({_reason}) and require_explicit "
                      f"is set; pre-generate {_scsv}.")
              print(f"[survivorship] regenerating panel ({_reason}) ...", flush=True)
              _px = _pd.read_csv(_px_path, index_col=0, parse_dates=True)
              _px = _px[~_px.index.duplicated()].sort_index().ffill()
              # Generate from the BASE constituents (never the survivorship file),
              # choosing the granularity file that matches `sector_column`.
              _col = _cfg0.get("sector_column", "GICS Sector")
              _base_cons = _cfg0.get("constituents_base",
                                     "data/constituents_gics.csv"
                                     if _col == "GICS Sub-Industry"
                                     else "data/constituents.csv")
              if not _os.path.exists(_base_cons):
                  _base_cons = "data/constituents.csv"
              _cons = _pd.read_csv(_base_cons)
              _res = SurvivorshipModel(_params).generate(_px, _cons, seed=_seed)
              _res.prices.to_csv(_scsv)
              _res.constituents.to_csv(_scons, index=False)
              _res.meta[["Symbol", "fail", "reason"]].rename(
                  columns={"fail": "delist_date"}).to_csv(
                  "data/survivorship_delist.csv", index=False)
              _json.dump({
                  "signature": _sig, "seed": _seed, "params": _params,
                  "price_csv": _px_path, "constituents": _base_cons,
                  "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
              }, open(_meta_path, "w"), indent=2, default=str)
              print(f"[survivorship] generated {_scsv} + {_scons} from "
                    f"{_base_cons} (seed={_seed}); sidecar {_meta_path}", flush=True)
          else:
              print(f"[survivorship] reusing validated panel: {_scsv}", flush=True)

          overrides = dict(overrides)
          overrides.update({"price_csv": _scsv, "oos_price_csv": _scsv,
                            "constituents_path": _scons})
          print(f"[survivorship] using synthetic panel: {_scsv} + {_scons}", flush=True)

    if from_tape:
        return run_from_tape(from_tape, config_path)

    # Inject flags into config
    overrides["download"] = do_download
    overrides["verbose"] = verbose

    # Load config
    cfg = Config(config_path=config_path, overrides=overrides)

    # Config-driven market-distress batch: run the SAME config across stress
    # windows (default off). See modules/validation/distress.py.
    if cfg.get("run_distress"):
        from modules.validation.distress import run_distress_batch
        return run_distress_batch(
            config_path, overrides,
            cfg.get("distress_out_dir", "results/19/distress"),
            build_flow, cfg.get("distress_only"), verbose)

    # Build the flow from the config's component versions (swap without code
    # changes); falls back to V1 defaults if unspecified.
    _flow = build_flow(cfg.get("components", {}) or {})
    _known = [v.__name__ for d in COMPONENTS.values() for v in d.values()
              if hasattr(v, '__name__')]
    print(f"[main] components: "
          + ", ".join(s.__name__ for s in _flow
                      if s.__name__ in _known), flush=True)

    # Run pipeline — config header + stage output are captured by the
    # orchestrator (mirrored to terminal only with --verbose).
    orchestrator = PipelineOrchestrator(
        cfg, _flow, verbose=verbose,
        results_dir=cfg.get("results_dir", "results"))
    orchestrator.run()

    print("\n[v3.2] Pipeline completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())