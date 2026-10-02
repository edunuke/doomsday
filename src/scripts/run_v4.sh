#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1
PY=/Users/edunuke/anaconda3/envs/portoptv1/bin/python
for cfg in config_v4a_res3_alpha4 config_v4b_res4_alpha1 config_v4c_res4_alpha4; do
  echo "=== RUN ${cfg} ==="; "${PY}" main.py --config "configs/${cfg}.json"; echo "=== DONE ${cfg} ==="
done
echo "=== V4 EXPERIMENTS COMPLETE ==="
