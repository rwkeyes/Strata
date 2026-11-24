#!/usr/bin/env bash
# strata-turnaround-run.sh <tree> <label> [n] [config] [prompt-tokens] [max-tokens]
#   <tree>   the tree whose server (and .venv) runs the probe window
#   [config] optional run config; use it to point that server at another tree's engine binary
#   [prompt-tokens] default 512; 8000-32000 measures the long-prompt/short-answer shape where prefill dominates
#   [max-tokens]    default 128; lower it (e.g. 32) when the interesting cost is the first token, not the output
# Runs the sequential turnaround probe against <tree> in a card window and restores production after.
# Prints the probe's RESULT line. Writes $OUT/probe-<label>.json (default OUT=~/strata-0217-turnaround).
set -uo pipefail
TREE=${1:?tree}; LABEL=${2:?label}; N=${3:-16}
OUT=${OUT:-$HOME/strata-0217-turnaround}; mkdir -p "$OUT"
CONF=${4:-$TREE/strata-coder-iq1_m-sycl-tuned.json}
PT=${5:-512}; MT=${6:-128}
[ -r "$CONF" ] || { echo "no config $CONF" >&2; exit 2; }
~/bin/strata-card-window.sh "$TREE" 8097 -- bash -c "
  EL=\$(/usr/bin/python3 -c \"import json;print(json.load(open('$CONF'))['log'])\")
  curl -s -m 10 http://127.0.0.1:\$PORT/v1/status | /usr/bin/python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine\", d[\"engine\"], \"loaded\", d.get(\"loaded\"))'
  /usr/bin/python3 $HOME/bin/strata-turnaround-probe.py --url http://127.0.0.1:\$PORT --engine-log \"\$EL\" \
      --label $LABEL --n $N --prompt-tokens $PT --max-tokens $MT --out $OUT/probe-$LABEL.json 2>&1 | grep -E 'RESULT|Error|Traceback|  1/'
" 2>&1 | grep -E 'engine |RESULT|restore:|card window|Error|Traceback|  1/'
