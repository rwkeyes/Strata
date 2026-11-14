#!/usr/bin/env bash
# strata-intercept-cause.sh - which engine path owns the ~220 ms that remains with the cache off?
#
# The ladder probe put the fixed cost at 222 ms (cache off) / 434 ms (cache on) inside the engine's own prefill
# accounting, with the PLE read path already excluded by the row-cache sweep. This fixes the prompt at ~75 tokens
# and toggles one engine subsystem per card window, six samples each:
#
#   base    deployed config
#   nospec  --spec 0        (no MTP/suffix drafting: the verify geometry at request start)
#   nocache --prompt-cache 0 (no conversation cache slots at all)
#   base2   deployed config again (drift check)
#
# UNDO: rm this file and ~/strata-intercept-cause
set -uo pipefail
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
BASE=$TREE/strata-coder-iq1_m-sycl-tuned.json
OUT=$HOME/strata-intercept-cause
PROBE=$HOME/bin/strata-intercept-probe.py
mkdir -p "$OUT"

variant() {   # name extra-args...
    local name=$1; shift
    python3 - "$BASE" "$OUT/conf-$name.json" "$@" <<'PY'
import json, sys
src, dst, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
d = json.load(open(src)); d["args"] = list(d.get("args", [])) + extra
json.dump(d, open(dst, "w"), indent=1); print(dst, "extra:", extra)
PY
}

arm() {   # tag
    local tag=$1 conf="$OUT/conf-$1.json"
    echo "[$(date +%H:%M:%S)] ===== cause-$tag"
    ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$conf" -- bash -c "
        EL=\$(python3 -c \"import json;print(json.load(open('$conf'))['log'])\")
        curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
        /usr/bin/python3 $PROBE --port \$PORT --log \"\$EL\" --out $OUT/probe-$tag.json --label $tag \
            --sizes 75 --samples 6
    " 2>&1 | grep -E '=====|engine:|^\s+\{|wrote|restore'
}

variant base
variant base2
variant nospec --spec 0
variant nocache --prompt-cache 0

for tag in base nospec nocache base2; do
    arm "$tag"
done
echo "[$(date +%H:%M:%S)] cause sweep done"
