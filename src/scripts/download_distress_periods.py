"""
BATCH PERIOD DOWNLOADER

Downloads all 18 regime-testing windows from todo/backtests.md into
data/distress/*.csv using the existing S&P 500 constituents.csv universe.

Usage:
    /Users/edunuke/anaconda3/envs/portoptv1/bin/python download_all_periods.py
"""

import os
import sys
import time

# Ensure src/ is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.data.download_data import download_window

# (name, start, end) — end is exclusive per Yahoo convention
PERIODS = [
    # ---- Market distress (bad regimes) ----------------------------------
    ("tariff_2025",      "2025-02-19", "2025-08-08"),  # -18.9% drawdown, VIX 60+
    ("yen_carry_2024",   "2024-07-16", "2024-09-16"),  # VIX 23.9 -> 65 intraday
    ("ai_geo_2026",      "2026-01-27", "2026-04-01"),  # AI-bubble + Iran fear
    ("iran_oil_2026",    "2026-06-01", "2026-08-01"),  # Dow -950, oil $67->$111
    ("aug_vol_2025",     "2025-07-28", "2025-10-01"),  # low -> high vol transition
    ("svb_2023",         "2023-03-01", "2023-06-01"),  # regional banking crisis
    ("rates_2023",       "2023-09-01", "2023-11-01"),  # 10Y -> 5%
    ("bear_2022",        "2022-09-06", "2023-03-01"),  # bear trough + burn-in
    # ---- Market rallies (good regimes) -----------------------------------
    ("recovery_2025",    "2025-04-08", "2025-08-08"),  # +35% off Apr trough
    ("rally_2024",       "2024-11-05", "2025-02-01"),  # post-election melt-up
    ("ath_2026",         "2026-07-01", "2026-09-04"),  # fresh ATH complacency
    ("calm_2024",        "2024-01-02", "2024-07-01"),  # low-VIX baseline
    # ---- Canonical historical (survivorship-biased membership) ----------
    ("covid_2020",       "2020-02-19", "2020-09-01"),  # -34% in 23 sessions
    ("gfc_2008",         "2008-09-15", "2009-03-09"),  # -57% slow bleed
    ("dotcom_2001",      "2001-06-01", "2002-06-01"),  # secular unwind slice
    ("usdowngrade_2011", "2011-07-22", "2011-11-01"),  # VIX 82% spike
    ("china_2015",       "2015-08-17", "2016-02-11"),  # slow bleed
    ("powell_2018",      "2018-10-03", "2019-01-01"),  # everything-selloff
]

OUTPUT_DIR = "data/distress"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    n = len(PERIODS)
    failed = []

    for idx, (name, start, end) in enumerate(PERIODS, 1):
        out_path = os.path.join(OUTPUT_DIR, f"{name}.csv")
        print(f"\n{'='*80}", flush=True)
        print(f"[{idx}/{n}] {name}: {start} -> {end}", flush=True)
        print("=" * 80, flush=True)
        t0 = time.time()
        try:
            download_window(
                start=start,
                end=end,
                output_path=out_path,
                constituents_path="data/constituents.csv",
            )
            elapsed = time.time() - t0
            print(f"[{idx}/{n}] OK in {elapsed:.1f}s -> {out_path}", flush=True)
        except Exception as exc:
            print(f"[{idx}/{n}] FAILED: {exc}", flush=True)
            failed.append((name, str(exc)))

    print(f"\n{'='*80}", flush=True)
    print(f"DONE. {n - len(failed)}/{n} windows downloaded. Output dir: {OUTPUT_DIR}/", flush=True)
    if failed:
        print("FAILED windows:", flush=True)
        for name, err in failed:
            print(f"  - {name}: {err}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()