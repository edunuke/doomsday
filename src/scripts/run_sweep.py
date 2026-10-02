"""Systematic OFAT sweep over strategy levers (full runs, early-stop OFF).

Runs each variant through main.py, then reads the auto-created results dir and
records the headline metrics into results/sweep_summary.csv.

Usage: python scripts/run_sweep.py [batch]
   batch in {A,B,C,D,E,all}  (default all)
"""
import json
import os
import re
import subprocess
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = json.load(open(os.path.join(SRC, "configs", "config_sweep_base.json")))
BASE["early_stop_enabled"] = False          # full-period evaluation
SWEEP_DIR = os.path.join(SRC, "configs", "sweep")
os.makedirs(SWEEP_DIR, exist_ok=True)
PY = "/Users/edunuke/anaconda3/envs/portoptv1/bin/python"
SUMMARY = os.path.join(SRC, "results", "sweep_summary.csv")

BATCHES = {
    "A": [  # entry-signal strictness
        ("A1_minz_0.75", {"min_override_z": 0.75}),
        ("A2_minz_1.0", {"min_override_z": 1.0}),
        ("A3_minz_1.25", {"min_override_z": 1.25}),
        ("A4_ecdf_0.85", {"entry_ecdf_percentile": 0.85}),
        ("A5_ecdf_0.90", {"entry_ecdf_percentile": 0.90}),
        ("A6_zbase_1.25", {"entry_z_base": 1.25}),
        ("A7_zbase_1.75", {"entry_z_base": 1.75}),
    ],
    "B": [  # Kalman filter responsiveness
        ("B1_kd_1e-3", {"kalman_delta": 1e-3}),
        ("B2_kd_1e-5", {"kalman_delta": 1e-5}),
        ("B3_noise_1e-2", {"kalman_obs_noise": 1e-2}),
        ("B4_noise_1e-4", {"kalman_obs_noise": 1e-4}),
    ],
    "C": [  # candidate screening
        ("C1_corr_0.60", {"min_correlation": 0.60}),
        ("C2_corr_0.75", {"min_correlation": 0.75}),
        ("C3_coint_0.01", {"p_value_threshold": 0.01}),
        ("C4_coint_0.10", {"p_value_threshold": 0.10}),
    ],
    "D": [  # portfolio construction
        ("D1_top20", {"promote_top_n": 20}),
        ("D2_sleeve_0.20", {"max_sleeve_weight": 0.20}),
        ("D3_gross_2.0", {"max_gross_exposure": 2.0}),
        ("D4_gross_3.5", {"max_gross_exposure": 3.5}),
    ],
    "E": [  # component combos ON TOP of the V3-residualizer base
        ("E1_res_v1_ablation", {"components": {"residualizer": "v1", "alphacube": "v1", "walkforward": "v1"}}),
        ("E2_alpha_v2", {"components": {"residualizer": "v3", "alphacube": "v2", "walkforward": "v1"}}),
        ("E3_alpha_v3", {"components": {"residualizer": "v3", "alphacube": "v3", "walkforward": "v1"}}),
        ("E4_wf_v2",    {"components": {"residualizer": "v3", "alphacube": "v1", "walkforward": "v2"}}),
    ],
}

KEYS = ["Total Return [%]", "Max Drawdown [%]", "Sharpe Ratio", "Sortino Ratio",
        "Win Rate [%]", "Profit Factor", "Excess Return vs SOFR [%]",
        "Annualised Excess [%]", "Information Ratio"]


def run_variant(name, overrides):
    cfg = dict(BASE)
    cfg.update(overrides)
    path = os.path.join(SWEEP_DIR, f"{name}.json")
    json.dump(cfg, open(path, "w"), indent=2)
    print(f"\n########## VARIANT {name} :: {overrides} ##########", flush=True)
    import glob as _glob
    _pat = os.path.join(SRC, "results", "[0-9]*")
    before = set(_glob.glob(_pat))
    proc = subprocess.run([PY, "main.py", "--config", path], cwd=SRC,
                          capture_output=True, text=True)
    after = set(_glob.glob(_pat))
    new = sorted(after - before, key=lambda q: int(os.path.basename(q)))
    if not new:
        print(f"[sweep] {name}: no new results dir; tail:\n{(proc.stdout+proc.stderr)[-600:]}",
              flush=True)
        return None
    rd = new[-1]
    ps = os.path.join(rd, "csv", "portfolio_stats.csv")
    if not os.path.exists(ps):
        print(f"[sweep] {name}: missing {ps}", flush=True)
        return None
    import pandas as pd
    s = pd.read_csv(ps); s.columns = ["k", "v"]; s = s.set_index("k")["v"]
    row = {"variant": name, "run": os.path.basename(rd)}
    for k in KEYS:
        try:
            row[k] = float(s.get(k, "nan"))
        except Exception:
            row[k] = float("nan")
    print(f"[sweep] {name} ({row['run']}): ret={row['Total Return [%]']:+.2f}% "
          f"sharpe={row['Sharpe Ratio']:.2f} IR={row['Information Ratio']:.3f}", flush=True)
    return row


def main():
    batch = sys.argv[1] if len(sys.argv) > 1 else "all"
    variants = []
    for b in (["A", "B", "C", "D", "E"] if batch == "all" else [batch]):
        variants += BATCHES[b]
    import pandas as pd
    rows = []
    for name, ov in variants:
        r = run_variant(name, ov)
        if r:
            rows.append(r)
        # persist incrementally so an overnight crash keeps progress
        if rows:
            pd.DataFrame(rows).to_csv(SUMMARY, index=False)
    print(f"\n[sweep] wrote {SUMMARY} ({len(rows)} variants)", flush=True)


if __name__ == "__main__":
    main()
