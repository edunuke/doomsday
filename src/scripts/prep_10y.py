import json
import pandas as pd

SRC = "/Users/edunuke/Desktop/statarb-prival/src/data/universe_daily_long.csv"
DST = "/Users/edunuke/Desktop/statarb-prival/src/data/universe_daily_10y.csv"

# Trim to 10 years ending at the last available date
df = pd.read_csv(SRC, index_col=0, parse_dates=True)
end = df.index[-1]
start = end - pd.DateOffset(years=10)
df10 = df.loc[start:end]
df10.to_csv(DST)
print(f"10y series: {df10.index[0].date()} -> {df10.index[-1].date()}"
      f"  rows={len(df10)}  cols={df10.shape[1]}")

# Walk-forward start = 5 years before the end (train = preceding 5 years)
walk_start = end - pd.DateOffset(years=5)
lookback = int(df10.index.searchsorted(walk_start))
print(f"walk-forward starts {df10.index[lookback].date()}  -> lookback_days={lookback}")
print(f"OOS days = {len(df10) - lookback}")

# Update config
with open("/Users/edunuke/Desktop/statarb-prival/src/config_long.json") as f:
    cfg = json.load(f)
cfg["price_csv"] = "data/universe_daily_10y.csv"
cfg["oos_price_csv"] = "data/universe_daily_10y.csv"
cfg["lookback_days"] = lookback              # walk-forward start offset (5y)
cfg["retrain_freq_days"] = 180               # semi-annual retrain (30*6)
cfg["screen_lookback_days"] = 252            # short coint rescreen window
cfg["state_persistence_file"] = "data/state_10y.pkl"
# train_lookback_days intentionally omitted -> defaults to lookback_days (5y)
cfg.pop("train_lookback_days", None)
with open("/Users/edunuke/Desktop/statarb-prival/src/config_long.json", "w") as f:
    json.dump(cfg, f, indent=2)
print("config_long.json updated:")
print("  lookback_days     =", cfg["lookback_days"])
print("  retrain_freq_days =", cfg["retrain_freq_days"])
print("  screen_lookback_days =", cfg["screen_lookback_days"])