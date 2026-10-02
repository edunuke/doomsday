#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1
PY=/Users/edunuke/anaconda3/envs/portoptv1/bin/python
for cfg in config_lev1_sleeve35 config_lev2_sleeve40 config_lev3_sleeve45 config_lev4_gross35; do
  echo "=== RUN ${cfg} ==="; "${PY}" main.py --config "configs/${cfg}.json"; echo "=== DONE ${cfg} ==="
done
echo "=== LEVERAGE EXPERIMENTS COMPLETE ==="
