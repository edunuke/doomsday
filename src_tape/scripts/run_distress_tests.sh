#!/usr/bin/env bash
# ==============================================================================
# StatArb (src_tape) — Market Distress batch runner
#
# Runs the SAME config that produced run 19 across historical stress
# windows in data/distress/ (two-file mode: distress_in warmup + distress OOS,
# lookback_days = warmup length so the loop trades only the OOS window).
#
# Driven by the config flag `run_distress` (default false). This script builds
# a temp config with run_distress=true and calls main.py once; the batch loop
# and per-window results live in modules/validation/distress.py.
#
# Output: <run 19>/distress/{N}/ (+ manifest.json mapping run -> window),
# resolved through the results/ banding (results/1-20/19 by default).
#
# Usage:
#     ./scripts/run_distress_tests.sh                  # all windows
#     ./scripts/run_distress_tests.sh covid_2020       # a single window
#     ./scripts/run_distress_tests.sh --verbose        # terminal output on
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON="/Users/edunuke/anaconda3/envs/portoptv1/bin/python"
BASE_CONFIG="${BASE_CONFIG:-configs/config_v3_2y_screener2.json}"
if [ -z "${OUT_DIR:-}" ]; then
    OUT_DIR="$("$PYTHON" -c 'from modules.core.run_paths import run_file; print(run_file(19, "distress"))' 2>/dev/null         || echo "results/19/distress")"
fi

TARGET="all"; VERBOSE=""
for a in "$@"; do
    case "$a" in
        --verbose|-v) VERBOSE="--verbose" ;;
        *) TARGET="$a" ;;
    esac
done

TMP_CFG="$(mktemp -t distress_cfg.XXXXXX).json"
"$PYTHON" - "$BASE_CONFIG" "$TMP_CFG" "$OUT_DIR" "$TARGET" <<'PY'
import json, sys
base, out, out_dir, target = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cfg = json.load(open(base))
cfg["run_distress"] = True
cfg["distress_out_dir"] = out_dir
if target != "all":
    cfg["distress_only"] = target
json.dump(cfg, open(out, "w"), indent=2)
PY

echo "[run_distress] base=$BASE_CONFIG  out=$OUT_DIR  target=$TARGET"
"$PYTHON" main.py --config "$TMP_CFG" $VERBOSE
echo "[run_distress] FINISHED — see $OUT_DIR/ (manifest.json maps run -> window)"
