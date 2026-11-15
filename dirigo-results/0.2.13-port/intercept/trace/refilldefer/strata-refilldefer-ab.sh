#!/usr/bin/env bash
# strata-refilldefer-ab.sh - does skipping the post-prompt slot refill cost anything?
#
# The refill hands the lent expert slots back before the first token (0.319 ms/slot, 72-188 ms). STRATA_REFILL_DEFER=1
# skips it, leaving the prompt's experts in the cache - which is what the decode that follows wants. The risk is the
# NEXT request: its prompt lends new slots and may find fewer of its own resident.
#
# So the ladder runs six samples per size in one window: the first sample pays for the previous request's leftovers, and
# a rising trend across samples is that cost; comparison between arms at the same sample index is the benefit.
#
# usage: strata-refilldefer-ab.sh TAG [--defer]
# UNDO: rm this file and ~/strata-refilldefer-ab
set -uo pipefail
TAG=${1:?tag}
shift || true
DEFER=""
for a in "$@"; do [ "$a" = "--defer" ] && DEFER=1; done
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
CONF=$TREE/strata-coder-iq1_m-sycl-tuned.json
OUT=$HOME/strata-refilldefer-ab
mkdir -p "$OUT"

echo "[$(date +%H:%M:%S)] ===== refilldefer-$TAG defer=${DEFER:-0}"
env STRATA_TRACE=1 ${DEFER:+STRATA_REFILL_DEFER=1} \
  ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$CONF" -- bash -c "
    LOG=\$(python3 -c \"import json;print(json.load(open('$CONF'))['log'])\")
    curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
    BEFORE=\$(stat -c%s \"\$LOG\")
    /usr/bin/python3 \$HOME/bin/strata-intercept-probe.py --port \$PORT --log \"\$LOG\" --out $OUT/ladder-$TAG.json \
        --label $TAG --sizes 75,512,2048 --samples 6
    tail -c +\$((BEFORE + 1)) \"\$LOG\" > $OUT/engine-$TAG.log
" 2>&1 | grep -aE '=====|engine:|wrote|restore'
echo "[$(date +%H:%M:%S)] done"
