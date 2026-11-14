#!/usr/bin/env bash
# strata-intercept-sweep.sh - price a request's fixed cost, with and without the fork's cache routing.
#
# Two card windows on the 0.2.13 tree, same engine, same model:
#   on      the deployed config (conv_route: on)  -> single-shot traffic runs with the prompt cache OFF
#   shadow  conv_route: shadow (classify and log, never switch) -> every request parks, like stock
#
# Each window runs ~/bin/strata-intercept-probe.py over a 4..2048-token ladder of unique prompts. The probe reads
# the engine's own "prompt N tokens = ... read in X ms" and "conversation cache: parked ... in Y ms" lines, so
# TTFT splits into prompt read + park + everything else.
#
# UNDO: rm this file and ~/strata-intercept-sweep
set -uo pipefail
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
BASE=$TREE/strata-coder-iq1_m-sycl-tuned.json
OUT=$HOME/strata-intercept-sweep
PROBE=$HOME/bin/strata-intercept-probe.py
mkdir -p "$OUT"

variant() {   # name key value
    python3 - "$BASE" "$OUT/conf-$1.json" "$2" "$3" <<'PY'
import json, sys
src, dst, key, val = sys.argv[1:5]
d = json.load(open(src))
try: val = json.loads(val)
except Exception: pass
d[key] = val
json.dump(d, open(dst, "w"), indent=1)
print(dst, key, "=", d[key])
PY
}

arm() {   # tag
    local tag=$1 conf="$OUT/conf-$1.json"
    echo "[$(date +%H:%M:%S)] ===== intercept-$tag"
    ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$conf" -- bash -c "
        EL=\$(python3 -c \"import json;print(json.load(open('$conf'))['log'])\")
        curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
        /usr/bin/python3 $PROBE --port \$PORT --log \"\$EL\" --out $OUT/probe-$tag.json --label $tag
    " 2>&1 | grep -E '=====|engine:|^\s+\{|wrote|restore'
}

variant on conv_route on
variant shadow conv_route shadow

for tag in on shadow; do
    arm "$tag"
done
echo "[$(date +%H:%M:%S)] intercept sweep done"
