#!/usr/bin/env bash
# strata-trace-window.sh - run the ladder with the engine's own STRATA_TRACE and split the fixed cost.
#
# The 0.2.13 engine already prints a per-request admission trace (drain, prompt read, windows, checkpoints, first
# window, slot copy, slot freed, checkpoint copy) when STRATA_TRACE is set, so no build is needed to ask which phase
# owns the ~165 ms that section 6 of docs/PERF-NEXT.md left unnamed.
#
# Six samples per size so each size is measured in BOTH router states: with batch_after 4 the first four requests of a
# run are served cache-on, the fifth onward cache-off.
#
# usage: strata-trace-window.sh [tag] [sizes] [samples]
# UNDO: rm this file and ~/strata-trace-run
set -uo pipefail
TAG=${1:-iq1_m}
SIZES=${2:-75,512,2048}
SAMPLES=${3:-6}
CONF=${CONF:-$TREE/strata-coder-iq1_m-sycl-tuned.json}   # CONF=... for a different pack/weights
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
OUT=$HOME/strata-trace-run
PROBE=$HOME/bin/strata-intercept-probe.py
SPLIT=$HOME/bin/strata-trace-split.py
mkdir -p "$OUT"

echo "[$(date +%H:%M:%S)] ===== trace-$TAG sizes=$SIZES samples=$SAMPLES"
STRATA_TRACE=1 ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$CONF" -- bash -c "
    LOG=\$(python3 -c \"import json;print(json.load(open('$CONF'))['log'])\")
    curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
    BEFORE=\$(stat -c%s \"\$LOG\")
    /usr/bin/python3 $PROBE --port \$PORT --log \"\$LOG\" --out $OUT/ladder-$TAG.json --label $TAG \
        --sizes $SIZES --samples $SAMPLES
    /usr/bin/python3 $SPLIT --log \"\$LOG\" --since-bytes \$BEFORE --json $OUT/trace-$TAG.json
    cp \"\$LOG\" $OUT/engine-$TAG.log
" 2>&1 | grep -aE '=====|engine:|tok \(|^ +[0-9]+ +[0-9]+|request\(s\) traced|wrote|restore|no trace'
echo "[$(date +%H:%M:%S)] trace window done"
