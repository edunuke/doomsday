"""
CONFIG SWEEP — one-at-a-time knob sensitivity on START-ROBUSTNESS.

Objective (cheap, causal): run the config ONCE over the 10y sample, then
measure the distribution of rolling 2y (504d) returns. A config is start-robust
if the rolling-2y MEDIAN is high, the POSITIVE FRACTION is high, and the worst
rolling 2y is survivable. Also records full-period ann/DD/Sharpe.

This is screening, not optimization: OAT perturbations, no grid search, and the
acceptance reading is 'does the median across starts move at all' — not a new
in-sample max.

Usage: python scripts/sweep_config.py --tag oat1
Output: results/_sweep/<tag>/<variant>/runs + results/_sweep/summary_<tag>.csv
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
BASE = "configs/config_v3_10y_screener2.json"

# --- built-in OAT set (one knob per variant) --------------------------------
OAT = {
    "base": {},
    "entry_z_0.75": {"entry_z_base": 0.75},
    "entry_z_1.25": {"entry_z_base": 1.25},
    "exit_z_0.5": {"exit_z_spread": 0.5},
    "gross_2.0": {"max_gross_exposure": 2.0},
    "gross_3.4": {"max_gross_exposure": 3.4},
    "mincorr_0.25": {"min_correlation": 0.25},
    "screen_freq_10": {"screening_freq_days": 10},
    "retrain_45": {"retrain_freq_days": 45},
    "kalman_delta_1e3": {"kalman_delta": 0.001},
    "kalman_noise_1e2": {"kalman_obs_noise": 0.01},
    "disp_tilt_0": {"alpha_v5_dispersion_tilt": 0.0},
    "betashrink_0": {"factor_beta_shrink": 0.0},
    "regime_scale_0.6": {"regime_exposure_scale": 0.6},
    "single_stock_0.40": {"max_single_stock_gross": 0.40},
    "mincorr_0.60": {"min_correlation": 0.60},
}

# --- round 2: around kdelta (kalman_delta=1e-3) as new base -----------------
OAT2 = {
    "kdelta": {},
    "entry_z_0.75": {"entry_z_base": 0.75},
    "entry_z_1.25": {"entry_z_base": 1.25},
    "exit_z_0.30": {"exit_z_spread": 0.30},
    "mincorr_0.25": {"min_correlation": 0.25},
    "betashrink_0": {"factor_beta_shrink": 0.0},
    "regime_scale_0.2": {"regime_exposure_scale": 0.2},
    "gross_2.0": {"max_gross_exposure": 2.0},
    "disp_tilt_0.5": {"alpha_v5_dispersion_tilt": 0.5},
    "promote_60": {"promote_top_n": 60},
    "ecdf_0.70": {"entry_ecdf_percentile": 0.70},
    "screen_30": {"screening_freq_days": 30},
    "retrain_180": {"retrain_freq_days": 180},
    "hold_buf_3": {"max_holding_buffer": 3.0},
    "kalman_delta_3e3": {"kalman_delta": 0.003},
    "kalman_delta_3e4": {"kalman_delta": 0.0003},
}

# --- round 3: mean-reversion threshold (liveness vs edge) on fixed plumbing
OAT3 = {
    "hurst_0.43": {},
    "hurst_0.46": {"max_hurst_exponent": 0.46},
    "hurst_0.48": {"max_hurst_exponent": 0.48},
    "hurst_0.50": {"max_hurst_exponent": 0.50},
    "hurst_0.55": {"max_hurst_exponent": 0.55},
    "hl_max_40": {"max_half_life": 40.0},
    "hl_max_60": {"max_half_life": 60.0},
    "slope_2.5": {"max_slope_tstat": 2.5},
}


# --- round 4: ILF universe — high-incidence knobs under funnel_mode=simple ---
# r41 (real-real book) showed -$52/trade expectancy, PF 0.98: config transfer
# from the S&P tuning window is the binding-constraint hypothesis.
# NOTE: under funnel_mode="simple" the promotion path (walk_forward top-K by
# Parametric_Alpha) BYPASSES entry_z_base / entry_ecdf_percentile /
# min_override_z entirely — those knobs are structurally dead here (verified:
# entry_z 0.50/0.75/1.00 produced bit-identical tapes). The set below only
# contains knobs that bind the executed exit/entry path in simple mode.
OAT_ILF = {
    "base": {},
    # exit threshold (base 0.2) — early-exit economics; binds exit + exp_yield
    "exit_z_0.10": {"exit_z_spread": 0.10},
    "exit_z_0.35": {"exit_z_spread": 0.35},
    "exit_z_0.50": {"exit_z_spread": 0.50},
    # holding period (base 2/60, buffer 1.5) — turnover is the fee-drag driver
    "maxhold_30": {"max_holding_days": 30},
    "maxhold_90": {"max_holding_days": 90},
    "maxhold_120": {"max_holding_days": 120},
    "minhold_5": {"min_holding_days": 5},
    "holdbuf_1.0": {"max_holding_buffer": 1.0},
    "holdbuf_2.5": {"max_holding_buffer": 2.5},
    # stop (base 2.2)
    "stop_1.8": {"stop_loss_z": 1.8},
    "stop_2.8": {"stop_loss_z": 2.8},
    # book size / friction (base simple_k=20, promote=35, friction_mult=8)
    "simple_k_10": {"simple_max_pairs": 10},
    "simple_k_40": {"simple_max_pairs": 40},
    "promote_20": {"promote_top_n": 20},
    "promote_50": {"promote_top_n": 50},
    "friction_4": {"friction_clearance_multiplier": 4.0},
    "friction_16": {"friction_clearance_multiplier": 16.0},
}

# --- round 5: ILF interaction grid on the BINDING knobs ---------------------
# ilf1 findings: exit_z binds (U-shaped, 0.5 best via turnover cut), min_hold
# binds, buffer binds; max_holding_days / simple_k / promote / friction are
# dead knobs (bit-identical tapes). Gross edge is positive across configs —
# fees/turnover are the binding constraint. This round tests interactions.
OAT_ILF2 = {
    "e50_m5": {"exit_z_spread": 0.50, "min_holding_days": 5},
    "e50_m5_b10": {"exit_z_spread": 0.50, "min_holding_days": 5,
                   "max_holding_buffer": 1.0},
    "e50_m8": {"exit_z_spread": 0.50, "min_holding_days": 8},
    "e65_m5": {"exit_z_spread": 0.65, "min_holding_days": 5},
    "e65_m8": {"exit_z_spread": 0.65, "min_holding_days": 8},
    "e50_b20": {"exit_z_spread": 0.50, "max_holding_buffer": 2.0},
    "e35_m5": {"exit_z_spread": 0.35, "min_holding_days": 5},
    "e50_m5_stop18": {"exit_z_spread": 0.50, "min_holding_days": 5,
                      "stop_loss_z": 1.8},
}

# --- round 6: volume-overlay robustness -------------------------------------
# v4c (keep lagged mover-leg relvol <= 1.0) doubled per-leg gross edge and
# made both cohorts positive; v4a (>= 1.0) lost money, i.e. the complementary
# half is the anti-placebo control (so the gain is not merely "trading less").
# This round maps the threshold gradient and the mover-window sensitivity.

# --- round 7: full (7-layer) funnel re-calibration for ILF -------------------
# v5/v6 (full mode at the S&P-inherited Hurst 0.43) produced an EMPTY book:
# the full-mode-only reversion gates (Hurst/HL/slope) rejected every ILF
# candidate. These variants relax the Hurst gate to find the region where the
# 7-layer funnel actually trades on ILF, then its performance is comparable.
OAT_ILF_FULL = {
    "full_hurst_0.43": {},
    "full_hurst_0.50": {"max_hurst_exponent": 0.50},
    "full_hurst_0.55": {"max_hurst_exponent": 0.55},
    "full_hurst_0.60": {"max_hurst_exponent": 0.60},
    "full_hurst_0.55_hl60": {"max_hurst_exponent": 0.55, "max_half_life": 60.0},
}


# --- round 8: full (7-layer) funnel INTERMEDIATE calibration -----------------
# Bounds established: S&P-inherited gates (alpha floor 0.35 via
# friction_clearance_multiplier=8, Hurst 0.43) -> 0 trades; ALL gates off ->
# -$843/leg (trending spreads admitted). This finds the working middle:
# alpha floor scaled to ILF alpha magnitudes (~0.09) and Hurst relaxed.
OAT_ILF_FULLCAL = {
    "h55_f0": {"max_hurst_exponent": 0.55, "friction_clearance_multiplier": 0.0,
               "entry_ecdf_percentile": 0.5, "min_override_z": 0.25,
               "entry_z_base": 0.5},
    "h55_f1": {"max_hurst_exponent": 0.55, "friction_clearance_multiplier": 1.0,
               "entry_ecdf_percentile": 0.5, "min_override_z": 0.25,
               "entry_z_base": 0.5},
    "h55_f2": {"max_hurst_exponent": 0.55, "friction_clearance_multiplier": 2.0,
               "entry_ecdf_percentile": 0.5, "min_override_z": 0.25,
               "entry_z_base": 0.5},
    "h60_f1": {"max_hurst_exponent": 0.60, "friction_clearance_multiplier": 1.0,
               "entry_ecdf_percentile": 0.5, "min_override_z": 0.25,
               "entry_z_base": 0.5},
    "h50_f1": {"max_hurst_exponent": 0.50, "friction_clearance_multiplier": 1.0,
               "entry_ecdf_percentile": 0.5, "min_override_z": 0.25,
               "entry_z_base": 0.5},
}


# --- round 9: TIER-1 signal improvements -------------------------------------
# v4c baseline: 24 legs, $2,030/leg, Sharpe 0.68 (negative volume gate).
# Tier-1 levers, one at a time vs that baseline:
#   ev_gate  : cost-aware EV entry gate (OU reversion vs round-trip cost)
#   volcont  : continuous volume weighting + reversal discriminator
#   fdr      : screener v5 (BH-FDR on EG p + mean-crossing persistence)
#   borrow   : fee realism (300 / 1000 bps LatAm borrow) on the same book
OAT_ILF_T1 = {
    "t1_base": {},
    "t1_ev": {"ev_gate_enabled": True, "ev_gate_mult": 1.0,
              "ev_exit_z": 0.65, "ev_max_hold_days": 60},
    "t1_ev_2x": {"ev_gate_enabled": True, "ev_gate_mult": 2.0,
                 "ev_exit_z": 0.65, "ev_max_hold_days": 60},
    "t1_volcont": {"volume_weight_mode": "continuous", "vol_w_floor": 0.5,
                   "rev_disc_enabled": True, "rev_disc_fast": 5,
                   "rev_disc_slow": 20},
    "t1_fdr": {"components": {"residualizer": "v3", "alphacube": "v1",
                              "selection": "v1", "walkforward": "v3",
                              "allocator": "v2", "regime": "v3",
                              "sizing": "v1", "screener": "v5"},
               "fdr_q": 0.10, "min_mean_crossings": 4},
    "t1_b300": {"borrow_bps": 300.0},
    "t1_b1000": {"borrow_bps": 1000.0},
}

OAT_ILF_VOL = {
    "base_min0_max1.00": {},
    "max_0.95": {"volume_gate_max_relvol": 0.95},
    "max_1.05": {"volume_gate_max_relvol": 1.05},
    "max_1.10": {"volume_gate_max_relvol": 1.10},
    "win_10": {"volume_gate_mover_window": 10},
    "win_3": {"volume_gate_mover_window": 3},
}


def rolling_stats(metrics_csv):
    m = pd.read_csv(metrics_csv, index_col=0, parse_dates=True)
    r = m["Live_Realized_Return"].astype(float).fillna(0.0)
    cum = (1 + r).cumprod()
    roll = (cum / cum.shift(504) - 1).dropna()
    dd = (1 - cum / cum.cummax()).max()
    ann = (1 + r).prod() ** (252 / len(r)) - 1
    return dict(ann=float(ann), full_dd=float(dd),
                roll2y_med=float(roll.median()) if len(roll) else np.nan,
                roll2y_p25=float(roll.quantile(.25)) if len(roll) else np.nan,
                roll2y_pos=float((roll > 0).mean()) if len(roll) else np.nan,
                roll2y_min=float(roll.min()) if len(roll) else np.nan,
                n=int(len(roll)))


def run_variant(name, overrides, tag, base_path=None):
    base = json.load(open(os.path.join(HERE, base_path or BASE)))
    base.update(overrides)
    base["run_distress"] = False
    rdir = os.path.join(HERE, "results", "_sweep", tag, name)
    base["results_dir"] = os.path.join("results", "_sweep", tag, name, "runs")
    shutil.rmtree(os.path.join(HERE, "results", "_sweep", tag, name), ignore_errors=True)
    cfgp = os.path.join(HERE, "results", "_sweep", tag, name, "cfg.json")
    os.makedirs(os.path.dirname(cfgp), exist_ok=True)
    json.dump(base, open(cfgp, "w"), indent=2)
    r = subprocess.run([PY, os.path.join(HERE, "main.py"), "--config", cfgp],
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[sweep] {name} FAILED: {r.stdout[-400:]} {r.stderr[-200:]}", flush=True)
        return {"variant": name, "error": True, **overrides}
    runs = sorted(int(d) for d in os.listdir(base["results_dir"]) if d.isdigit())
    run = os.path.join(base["results_dir"], str(max(runs)))
    st = rolling_stats(os.path.join(run, "csv", "metrics.csv"))
    ps = pd.read_csv(os.path.join(run, "csv", "portfolio_stats.csv")); ps.columns = ["k", "v"]
    S = ps.set_index("k")["v"]
    return {"variant": name,
            "total_%": float(S["Total Return [%]"]), "sharpe": float(S["Sharpe Ratio"]),
            **st, **overrides}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="oat1")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--set", default="oat1")
    ap.add_argument("--only", default=None, help="comma list of variant names")
    a = ap.parse_args()
    variants = {"oat1": OAT, "oat2": OAT2, "oat3": OAT3, "ilf1": OAT_ILF, "ilf2": OAT_ILF2, "ilfvol": OAT_ILF_VOL, "ilffull": OAT_ILF_FULL, "ilffullcal": OAT_ILF_FULLCAL, "ilft1": OAT_ILF_T1}[a.set]
    if a.only:
        keep = set(a.only.split(",")); variants = {k: v for k, v in variants.items() if k in keep}
    rows = []
    for name, ov in variants.items():
        print(f"[sweep] {name}: {ov}", flush=True)
        rows.append(run_variant(name, ov, a.tag, a.base))
        pd.DataFrame(rows).to_csv(os.path.join(HERE, "results", "_sweep",
                                               f"summary_{a.tag}.csv"), index=False)
    df = pd.DataFrame(rows)
    print("\n" + df.to_string(index=False))
    print(f"\n[sweep] wrote results/_sweep/summary_{a.tag}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
