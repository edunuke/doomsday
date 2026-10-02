"""
BATCH WARMUP PERIOD DOWNLOADER

Downloads the 504-trading-day pre-window immediately before each distress
period, into data/distress_in/<name>.csv.

These warmup files provide the in-sample history the engine's stationarity
metrics need (Hurst window, half-life window, ECDF window all use rolling
windows that are NaN across the first ~126 rows). Pairing a warmup file as
`price_csv` with the distress window as `oos_price_csv` gives the pipeline
valid mean-reversion history BEFORE the walk-forward loop starts.

Usage:
    /Users/edunuke/anaconda3/envs/portoptv1/bin/python download_warmups.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.data.download_data import download_window

# Mirrors the periods in download_all_periods.py / todo/backtests.md
PERIODS = [
    ("tariff_2025",      "2025-02-19", "2025-08-08"),
    ("yen_carry_2024",   "2024-07-16", "2024-09-16"),
    ("ai_geo_2026",      "2026-01-27", "2026-04-01"),
    ("iran_oil_2026",    "2026-06-01", "2026-08-01"),
    ("aug_vol_2025",     "2025-07-28", "2025-10-01"),
    ("svb_2023",         "2023-03-01", "2023-06-01"),
    ("rates_2023",       "2023-09-01", "2023-11-01"),
    ("bear_2022",        "2022-09-06", "2023-03-01"),
    ("recovery_2025",    "2025-04-08", "2025-08-08"),
    ("rally_2024",       "2024-11-05", "2025-02-01"),
    ("ath_2026",         "2026-07-01", "2026-09-04"),
    ("calm_2024",        "2024-01-02", "2024-07-01"),
    ("covid_2020",       "2020-02-19", "2020-09-01"),
    ("gfc_2008",         "2008-09-15", "2009-03-09"),
    ("dotcom_2001",      "2001-06-01", "2002-06-01"),
    ("usdowngrade_2011", "2011-07-22", "2011-11-01"),
    ("china_2015",       "2015-08-17", "2016-02-11"),
    ("powell_2018",      "2018-10-03", "2019-01-01"),
]

# Number of trading days of history BEFORE the distress window to use as warmup.
# 504 is the engine's default lookback_days. Anything >= ~300 operates the
# Hurst/half-life/ECDF windows correctly; short warmups (e.g. 300) are fine too
# but the pipeline's start_idx = min(lookback_days, len(warmup)).
WARMUP_DAYS = 504
OUTPUT_DIR = "data/distress_in"


def _bday_start_before(start_date: str, n: int) -> str:
    """Return the calendar date ~ n trading days before *start_date*."""
    import pandas as pd
    start = pd.Timestamp(start_date)
    # back up 1.6x calendar days to land ~n business days before, then snap back
    approx = start - pd.Timedelta(days=int(n * 1.6) + 30)
    bdays = pd.date_range(end=approx, periods=n + 1, freq="B")
    return bdays[0].strftime("%Y-%m-%d")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    n = len(PERIODS)
    failed = []

    for idx, (name, start, end) in enumerate(PERIODS, 1):
        warm_start = _bday_start_before(start, WARMUP_DAYS)
        out_path = os.path.join(OUTPUT_DIR, f"{name}.csv")
        print(f"\n{'='*80}", flush=True)
        print(f"[{idx}/{n}] Warmup {name}: {warm_start} -> {start}  (~{WARMUP_DAYS} trading days)", flush=True)
        print("=" * 80, flush=True)
        t0 = time.time()
        try:
            download_window(
                start=warm_start,
                end=start,  # exclusive — warmup ends right before distress starts
                output_path=out_path,
                constituents_path="data/constituents.csv",
            )
            print(f"[{idx}/{n}] OK in {time.time() - t0:.1f}s -> {out_path}", flush=True)
        except Exception as exc:
            print(f"[{idx}/{n}] FAILED: {exc}", flush=True)
            failed.append((name, str(exc)))

    print(f"\n{'='*80}", flush=True)
    print(f"DONE. {n - len(failed)}/{n} warmup windows downloaded to {OUTPUT_DIR}/", flush=True)
    if failed:
        print("FAILED:", flush=True)
        for name, err in failed:
            print(f"  - {name}: {err}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()