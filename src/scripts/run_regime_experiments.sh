#!/usr/bin/env bash
# Runs the regime experiments sequentially (one alpha-cube phase at a time).
set -u
cd "$(dirname "$0")/.." || exit 1
PY=/Users/edunuke/anaconda3/envs/portoptv1/bin/python
for cfg in config_59_regime_scaled_signal config_60_regime_entry_gate config_61_regime_conditional_univ; do
  echo "=== RUN ${cfg} ==="
  "${PY}" main.py --config "configs/${cfg}.json"
  echo "=== DONE ${cfg} (exit $?) ==="
done
echo "=== ALL REGIME EXPERIMENTS COMPLETE ==="
