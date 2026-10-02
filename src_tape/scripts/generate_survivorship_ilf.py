"""
Generate the LatAm (ILF) synthetic delisting panel with REGION-ADJUSTED calibration.

Region adjustment rationale (Damodaran, "Country Default Spreads and Risk
Premiums", Jan-2025 update, pages.stern.nyu.edu/~adamodar/pc/datasets/ctryprem.xls):

  Country      Moody's   Default spread   Country risk premium
  Brazil       Ba1       ~2.48%           3.24%
  Colombia     Baa3-ish  -                2.85%
  Mexico       Baa2      ~1.62%           2.46%
  Peru         Baa3      ~2.00%           2.07%
  Chile        Aa3       ~0.51%           1.10%
  US (mature)  Aa1/Aa3   ~0.23%           ~0.23%

ILF country weights (approx., iShares Latin America 40):
  Brazil ~55%, Mexico ~20%, Chile ~10%, Colombia ~7.5%, Peru ~7.5%
  -> weighted regional CRP ~= 2.75%  vs  US ~0.23%

Mapping to generator levers:
  * idio/total vol: NOT overridden — the model calibrates vol from the REAL
    LatAm panel itself, which is already region-faithful.
  * fail_rate: x2.0 regional multiplier. The raw default-probability implied
    by the spread ratio (2.75/0.23 ~= 12x) is damped (spread-to-PD is
    convex and the US spread sits at the rating floor); ~2x matches
    emerging-market corporate mortality evidence (Altman EMEA/LatAm studies)
    and keeps the synthetic count sane for a 42-name universe.
  * ttf_mean: x0.85 — distress resolves faster in LatAm (currency/commodity
    shocks hit quickly; workouts shorter than US Chapter-11 limbo).
  * reason_mix: bk 0.15 -> 0.25, M&A 0.45 -> 0.35 — credit failure is a
    larger share of LatAm delistings; fewer mega-cap M&A exits.
  * delist_ret: perf mean -0.35 -> -0.50 (sd 0.20 -> 0.28) — deeper
    pre-delist decay; bk unchanged at -0.95.
  * drift_neg: -0.35 -> -0.50 annualised distress drift (higher hurdle rates
    accelerate terminal decline; Damodaran LatAm cost of equity ~10-13% vs US
    ~8-9%).
  * vol_ramp: 2.0 -> 2.5 — distressed LatAm names see sharper vol spikes.
  * crisis_mult: add LatAm-specific stress years in the panel window:
    2016 (Brazil recession/impeachment tail, commodity bust) 1.5,
    2020 (Covid; LatAm GDP -7%) 2.0 (up from 1.8), 2023 (regional banking/
    political stress) 1.2.

Writes:
  data/ILF_survivorship.csv
  data/ILF_constituents_survivorship.csv
  data/ILF_survivorship_delist.csv
  data/ILF_survivorship_meta.json   (region-adjusted params + validation)
"""
from __future__ import annotations
import argparse, json, os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.survivorship import SurvivorshipModel, PARAMS  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# REGION-ADJUSTED CALIBRATION (LatAm)
# ---------------------------------------------------------------------------
FAIL_RATE_MULT = 2.0     # Damodaran-weighted regional CRP 2.75% vs US 0.23%
TTF_MULT = 0.85

IND_LATAM = {
    sector: (round(min(rate * FAIL_RATE_MULT, 0.12), 4),      # cap hazard/name
             round(ttf * TTF_MULT, 2), shape)
    for sector, (rate, ttf, shape) in PARAMS["industries"].items()
}

PARAMS_LATAM = {
    "industries": IND_LATAM,
    "reason_mix": {"M&A": 0.35, "perf": 0.40, "bk": 0.25},
    "delist_ret": {"M&A": [0.25, 0.05], "perf": [-0.50, 0.28], "bk": [-0.95, 0.10]},
    "crisis_mult": {"2016": 1.5, "2020": 2.0, "2023": 1.2, "else": 1.0},
    "idio_pctile": PARAMS["idio_pctile"],   # calibrated from real LatAm panel
    "vol_ramp": 2.5,
    "stress_days": PARAMS["stress_days"],
    "drift_neg": -0.50,
    "idio_rho": PARAMS["idio_rho"],
    "shrink": PARAMS["shrink"],
}

PARAMS_FILE = os.path.join(HERE, "data", "ILF_survivorship_params.json")
if not os.path.exists(PARAMS_FILE):
    json.dump(PARAMS_LATAM, open(PARAMS_FILE, "w"), indent=2, default=str)

REGION_NOTES = {
    "region": "LatAm (ILF universe: Brazil, Mexico, Chile, Colombia, Peru)",
    "source": ("Damodaran, Country Default Spreads and Risk Premiums, Jan-2025 "
               "update; pages.stern.nyu.edu/~adamodar/pc/datasets/ctryprem.xls"),
    "country_crp_pct": {"Brazil": 3.24, "Colombia": 2.85, "Mexico": 2.46,
                        "Peru": 2.07, "Chile": 1.10, "US_mature": 0.23},
    "ilf_country_weights": {"Brazil": 0.55, "Mexico": 0.20, "Chile": 0.10,
                            "Colombia": 0.075, "Peru": 0.075},
    "weighted_regional_crp_pct": 2.75,
    "fail_rate_multiplier": FAIL_RATE_MULT,
    "ttf_multiplier": TTF_MULT,
    "notes": ("idio vol NOT overridden: generator calibrates vol from the real "
              "LatAm price panel, which is already region-faithful. fail_rate "
              "raw spread-implied PD ratio (~12x) damped to 2x; bk share "
              "raised (credit-led LatAm distress); perf delist decay deepened; "
              "LatAm crisis years 2016/2020/2023 weighted."),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    prices = pd.read_csv(os.path.join(HERE, "data", "ILF_daily.csv"),
                         index_col=0, parse_dates=True)
    prices = prices[~prices.index.duplicated()].sort_index().ffill()
    cons = pd.read_csv(os.path.join(HERE, "data", "ILF_constituents_gics.csv"))
    # Overlays (ILF/ISZ6/MCBZ6) are excluded from generation so no synthetic
    # is seeded from index returns; generator labels them Index/Overlay.
    cons_gen = cons[cons["GICS Sector"] != "Index/Overlay"]

    model = SurvivorshipModel(params={**PARAMS_LATAM, "market_col": "ILF"})
    res = model.generate(prices, cons_gen, seed=a.seed)

    out_csv = os.path.join(HERE, "data", "ILF_survivorship.csv")
    out_cons = os.path.join(HERE, "data", "ILF_constituents_survivorship.csv")
    out_del = os.path.join(HERE, "data", "ILF_survivorship_delist.csv")
    res.prices.to_csv(out_csv)
    res.constituents.to_csv(out_cons, index=False)
    res.meta[["Symbol", "fail", "reason"]].rename(
        columns={"fail": "delist_date"}).to_csv(out_del, index=False)

    v = SurvivorshipModel.validate(res, industries=model.IND)
    # Signature used by main.py to validate panel reuse (seed + params + price).
    import hashlib as _hl
    _sig = _hl.sha256(json.dumps(
        {"seed": a.seed, "params": {**PARAMS_LATAM, "market_col": "ILF"},
         "price": "ILF_daily.csv"}, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    meta = {"seed": a.seed, "signature": _sig, "region": REGION_NOTES,
            "params": PARAMS_LATAM, "base_params_us": PARAMS, "validation": v,
            "base": "ILF_daily.csv", "output_csv": "ILF_survivorship.csv"}
    json.dump(meta, open(os.path.join(HERE, "data", "ILF_survivorship_meta.json"), "w"),
              indent=2, default=str)

    print(f"[survivorship-latam] wrote {out_csv}\n  {out_cons}")
    print(f"  n_synthetic={v['n_synthetic']}  mean_ttf_y={v['mean_ttf_years']}  "
          f"reasons={v['reasons']}  synth_vol={v['synth_ann_vol']:.3f}")
    for k, d in v["per_sector"].items():
        print(f"    {k:24s} n={d['n']:3d} ttf {d['mean_ttf_y']:5.1f}y "
              f"(target {d['target_ttf_y']})")


if __name__ == "__main__":
    raise SystemExit(main())
