"""
Generate the synthetic delisting panel ONCE and save it to data/ so runtime just
loads it. Writes:
  data/<base>_survivorship.csv        (real + synthetic price columns)
  data/constituents_survivorship.csv  (real + synthetic sector map)
  data/survivorship_meta.json         (calibration + validation summary)

Usage:
  python scripts/generate_survivorship.py --config configs/config_v3_wf3_5y.json
"""
from __future__ import annotations
import argparse, json, os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.survivorship import SurvivorshipModel, PARAMS  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_v3_wf3_5y.json")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, a.config)))
    base = os.path.basename(cfg["price_csv"])
    prices = pd.read_csv(os.path.join(HERE, "data", base), index_col=0, parse_dates=True)
    prices = prices[~prices.index.duplicated()].sort_index().ffill()
    cons = pd.read_csv(os.path.join(HERE, cfg.get("constituents_path", "data/constituents.csv")))

    model = SurvivorshipModel()
    res = model.generate(prices, cons, seed=a.seed)
    out_csv = os.path.join(HERE, "data", base[:-4] + "_survivorship.csv")
    res.prices.to_csv(out_csv)
    out_cons = os.path.join(HERE, "data", "constituents_survivorship.csv")
    res.constituents.to_csv(out_cons, index=False)
    out_del = os.path.join(HERE, "data", "survivorship_delist.csv")
    res.meta[["Symbol", "fail", "reason"]].rename(
        columns={"fail": "delist_date"}).to_csv(out_del, index=False)
    v = SurvivorshipModel.validate(res)
    meta = {"seed": a.seed, "params": PARAMS, "validation": v,
            "base": base, "output_csv": os.path.basename(out_csv)}
    json.dump(meta, open(os.path.join(HERE, "data", "survivorship_meta.json"), "w"),
              indent=2, default=str)
    print(f"[survivorship] wrote {out_csv}\n  {out_cons}")
    print(f"  n_synthetic={v['n_synthetic']}  mean_ttf_y={v['mean_ttf_years']}  "
          f"reasons={v['reasons']}  synth_vol={v['synth_ann_vol']:.3f}")
    for k, d in v['per_sector'].items():
        print(f"    {k:24s} n={d['n']:3d} ttf {d['mean_ttf_y']:5.1f}y (target {d['target_ttf_y']})")


if __name__ == "__main__":
    raise SystemExit(main())
