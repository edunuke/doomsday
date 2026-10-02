"""Build the warmup ablation: 1y / 2y / 5y, identical walk-forward window.

Hold the OOS window fixed (2021-09-03 -> 2026-09-03) and vary only the
training/warmup length by trimming the series start. Retrain cadence and
screening window are held constant across variants.
"""
import json
import pandas as pd

SRC = "/Users/edunuke/Desktop/statarb-prival/src/data/universe_daily_long.csv"
BASE_CONFIG = "/Users/edunuke/Desktop/statarb-prival/src/config_long.json"

df = pd.read_csv(SRC, index_col=0, parse_dates=True)
end = df.index[-1]
walk_start = pd.Timestamp("2021-09-03")  # fixed across all variants

base = json.load(open(BASE_CONFIG))

for years, tag in [(1, "1y"), (2, "2y"), (5, "5y")]:
    start = walk_start - pd.DateOffset(years=years)
    sub = df.loc[start:end]
    path = f"/Users/edunuke/Desktop/statarb-prival/src/data/universe_daily_{tag}.csv"
    sub.to_csv(path)
    lookback = int(sub.index.searchsorted(walk_start))
    cfg = dict(base)
    cfg["price_csv"] = f"data/universe_daily_{tag}.csv"
    cfg["oos_price_csv"] = f"data/universe_daily_{tag}.csv"
    cfg["lookback_days"] = lookback
    cfg["retrain_freq_days"] = 90
    cfg["screen_lookback_days"] = 252
    cfg["state_persistence_file"] = f"data/state_{tag}.pkl"
    cpath = f"/Users/edunuke/Desktop/statarb-prival/src/config_{tag}.json"
    json.dump(cfg, open(cpath, "w"), indent=2)
    print(f"{tag}: series {sub.index[0].date()} -> {sub.index[-1].date()} "
          f"rows={len(sub)}  lookback_days={lookback}  "
          f"walk_start={sub.index[lookback].date()}  oos={len(sub)-lookback}")