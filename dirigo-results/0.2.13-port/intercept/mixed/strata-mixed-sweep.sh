#!/usr/bin/env bash
# strata-mixed-sweep.sh - does turning the cache off sooner cost a conversation anything?
#
# Two card windows on the 0.2.13 tree, the same engine and model, differing only in conv_route_batch_after:
#   ba4  the deployed value        (4 consecutive single-shot prompts before the cache goes off)
#   ba1  the value the single-shot probe favoured (1)
#
# Each window runs ~/bin/strata-mixed-probe.py --singles 6: a repeated prompt, a two-turn conversation, six unique
# single-shot prompts, then the conversation's next turn and the repeated prompt again. A step that jumps from
# ~0.3 s to seconds is a re-prefill, which is the cost of a flip.
#
# UNDO: rm this file and ~/strata-mixed-sweep
set -uo pipefail
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
BASE=$TREE/strata-coder-iq1_m-sycl-tuned.json
OUT=$HOME/strata-mixed-sweep
PROBE=$HOME/bin/strata-mixed-probe.py
mkdir -p "$OUT"

variant() {   # name key value
    python3 - "$BASE" "$OUT/conf-$1.json" "$2" "$3" <<'PY'
import json, sys
src, dst, key, val = sys.argv[1:5]
d = json.load(open(src)); d[key] = json.loads(val)
json.dump(d, open(dst, "w"), indent=1); print(dst, key, "=", d[key])
PY
}

arm() {   # tag [server args]
    local tag=$1; shift
    local conf="$OUT/conf-$tag.json"
    # the batch_after value is a SERVER flag: serve/server.py reads --conv-route-batch-after and ignores the
    # config's "conv_route_batch_after" key, so a config variant alone is inert (measured 2026-11-14).
    echo "[$(date +%H:%M:%S)] ===== mixed-$tag SRV_ARGS='$*'"
    SRV_ARGS="$*" ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$conf" -- bash -c "
        curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
        /usr/bin/python3 $PROBE --url http://127.0.0.1:\$PORT --label $tag --singles 6 --out $OUT/mixed-$tag.json
    " 2>&1 | grep -E '=====|engine:|conv route:|ttft|C2|wrote|restore'
}

variant ba4 conv_route_batch_after 4
variant ba1 conv_route_batch_after 1

arm ba4                                  # the deployed value (server default 4)
arm ba1 --conv-route-batch-after 1       # the value the single-shot probe favoured
echo "[$(date +%H:%M:%S)] mixed sweep done"
