#!/usr/bin/env bash
# ==============================================================================
# StatArb v3.2 — Entry point
#
# Runs main.py inside the portoptv1 conda environment.
#
# Usage:
#     ./entrypoint.sh                          # defaults (quiet)
#     ./entrypoint.sh --verbose                # mirror runtime to terminal
#     ./entrypoint.sh --download               # fetch data from Yahoo first
#     ./entrypoint.sh --max_net_beta_exposure 0.03   # override params
#     ./entrypoint.sh --download --start 2025-02-19 --end 2025-08-08 \
#                     --output data/distress/tariff_2025.csv   # custom window
# ==============================================================================
set -euo pipefail

# Path to the portoptv1 python interpreter (independent of which conda is active).
PORT1_PYTHON="/Users/edunuke/anaconda3/envs/portoptv1/bin/python"

if [[ ! -x "$PORT1_PYTHON" ]]; then
    echo "[entrypoint] ERROR: portoptv1 interpreter not found at $PORT1_PYTHON" >&2
    echo "[entrypoint] Create it with:  conda create -n portoptv1 python=3.12 && conda install -n portoptv1 numpy pandas scipy statsmodels matplotlib joblib vectorbt yfinance" >&2
    exit 1
fi

# Always run from this script's own directory (src/), wherever it's invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[entrypoint] Using python: $PORT1_PYTHON"
echo "[entrypoint] Working dir : $(pwd)"
echo "[entrypoint] Arguments   : $*"
echo "-"

# Pass all CLI arguments straight through to main.py.
exec "$PORT1_PYTHON" main.py "$@"