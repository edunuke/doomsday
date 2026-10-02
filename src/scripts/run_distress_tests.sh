#!/usr/bin/env bash
# ==============================================================================
# StatArb v3.2 — Market Distress / Regime Test Batch Runner (two-file mode)
#
# For each window in data/distress/*.csv the pipeline is run with:
#   price_csv     = data/distress_in/<name>.csv     (504-day warmup, in-sample)
#   oos_price_csv = data/distress/<name>.csv        (the distress window, OOS)
#
# This gives the stationarity metrics (Hurst / half-life / ECDF rolling
# windows) valid history BEFORE the walk-forward loop opens — see
# todo/backtests.md §0 / §5 and download_warmups.py.
#
# lookback_days is set to the warmup length so start_idx falls exactly at
# the first distress row (the loop then trades only the OOS window).
#
# Usage:
#     ./run_distress_tests.sh                        # run ALL windows
#     ./run_distress_tests.sh tariff_2025            # run a single window
#     ./run_distress_tests.sh --verbose              # all, terminal output on
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="/Users/edunuke/anaconda3/envs/portoptv1/bin/python"

# ---------------------------------------------------------------------------
# Args: [<window-name>|all] [--verbose]
# ---------------------------------------------------------------------------
TARGET="all"
VERBOSE=""
for a in "$@"; do
    case "$a" in
        --verbose|-v) VERBOSE="--verbose" ;;
        *) TARGET="$a" ;;
    esac
done

OOS_DIR="data/distress"
WARM_DIR="data/distress_in"

if [[ "$TARGET" == "all" ]]; then
    OOS_FILES=("$OOS_DIR"/*.csv)
else
    OOS_FILES=("$OOS_DIR/$TARGET.csv")
fi

if [[ ! -e "${OOS_FILES[0]}" ]]; then
    echo "[run_distress] ERROR: no window files found for target '$TARGET' in $OOS_DIR/" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Run all windows
# ---------------------------------------------------------------------------
TOTAL=${#OOS_FILES[@]}
IDX=0

for oos in "${OOS_FILES[@]}"; do
    IDX=$((IDX + 1))
    name=$(basename "$oos" .csv)
    warm="$WARM_DIR/$name.csv"

    if [[ ! -e "$warm" ]]; then
        echo "[run_distress] WARN: missing warmup $warm — run download_warmups.py first. Skipping $name." >&2
        continue
    fi

    warm_days=$("$PYTHON" - "$warm" <<'EOF'
import sys, pandas as pd
df = pd.read_csv(sys.argv[1], index_col=0, parse_dates=True)
print(len(df))
EOF
)
    # lookback must equal the warmup row count so that
    #   start_idx = min(lookback, len(warmup + oos))  ==  len(warmup)
    # i.e. the walk-forward loop opens exactly at the first distress row and
    # trades ONLY the OOS window (start_idx = len(warmup) = first OOS row).
    lookback=$warm_days

    echo
    echo "======================================================================="
    echo "[$IDX/$TOTAL] Window: $name"
    echo "      warmup(w) = $warm       ($warm_days days)"
    echo "      oos      = $oos"
    echo "      lookback_days = $lookback"
    echo "======================================================================="

    "$PYTHON" main.py \
        --price_csv      "$warm" \
        --oos_price_csv  "$oos" \
        --lookback_days  "$lookback" \
        $VERBOSE

    echo "[run_distress] FINISHED: $name — see results/{N}/ (config.json records price_csv=$warm)"
done

echo
echo "======================================================================="
echo "[run_distress] DONE. $TOTAL windows processed."
echo "Match results to windows via results/{N}/config.json -> price_csv / oos_price_csv."
echo "======================================================================="