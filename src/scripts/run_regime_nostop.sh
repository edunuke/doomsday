#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1
PY=/Users/edunuke/anaconda3/envs/portoptv1/bin/python
for cfg in config_62_nostop_regime_scaled_signal config_63_nostop_regime_entry_gate config_64_nostop_regime_conditional_univ; do
  echo "=== RUN ${cfg} ==="
  "${PY}" main.py --config "configs/${cfg}.json"
  echo "=== DONE ${cfg} (exit $?) ==="
done
echo "=== ALL NOSTOP REGIME EXPERIMENTS COMPLETE ==="
