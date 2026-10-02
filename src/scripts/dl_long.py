import sys
sys.path.insert(0, "/Users/edunuke/Desktop/statarb-prival/src")
from modules.data.download_data import download_window

out = download_window(
    "2006-01-03",
    "2026-09-04",
    "/Users/edunuke/Desktop/statarb-prival/src/data/universe_daily_long.csv",
)
print("DONE:", out)
print("WROTE files. Now computing lookback for start_idx at 2008-01-01.")