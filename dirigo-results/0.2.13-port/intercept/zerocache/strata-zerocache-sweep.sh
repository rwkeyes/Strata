#!/usr/bin/env bash
# strata-zerocache-sweep.sh - is a 0-slot prompt cache a real profile, or a one-shot-only trap?
#
# Section 6 left one lead open: a start-up `--prompt-cache 0` measured 58 ms cheaper per request than the router's
# cache-off path at 75 tokens (324 ms against 382). This window runs that profile against the traffic shape it would
# have to serve, and checks the thing the router cannot do in it:
#
#   zero   --prompt-cache 0 + conv_route on   ladder at 75/512/2048, then the mixed shape
#   ref    deployed config, ladder only       the reference for the same prompts on this card today
#
# The router's cache_on comes from the START-UP args (`--prompt-cache N`), so with 0 it has no value to switch back
# to: a conversation in this profile should re-read its own context every turn. If C2 re-prefills, "0 slots" is a
# one-shot-only deployment and the 58 ms is not worth a profile.
#
# UNDO: rm this file and ~/strata-zerocache-sweep
set -uo pipefail
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
BASE=$TREE/strata-coder-iq1_m-sycl-tuned.json
OUT=$HOME/strata-zerocache-sweep
LADDER=$HOME/bin/strata-intercept-probe.py
MIXED=$HOME/bin/strata-mixed-probe.py
mkdir -p "$OUT"

python3 - "$BASE" "$OUT/conf-ref.json" "$OUT/conf-zero.json" <<'PY'
import json, sys
src, ref, zero = sys.argv[1:4]
d = json.load(open(src)); json.dump(d, open(ref, "w"), indent=1)
# --prompt-cache is an ENGINE argument (it lives in the config's args list), not a server flag: SRV_ARGS cannot set
# it, because serve/server.py rejects what it does not know. Rewrite the value in place instead.
z = json.load(open(src)); a = list(z.get("args", []))
if "--prompt-cache" in a:
    a[a.index("--prompt-cache") + 1] = "0"
else:
    a += ["--prompt-cache", "0"]
z["args"] = a; json.dump(z, open(zero, "w"), indent=1)
print(ref, "->", " ".join(d.get("args", []))[:120])
print(zero, "->", " ".join(a)[:120])
PY

arm() {   # tag server-args... ; runs ladder always, mixed only when MIX=1
    local tag=$1; shift
    local conf="$OUT/conf-$tag.json"
    echo "[$(date +%H:%M:%S)] ===== zero-$tag SRV_ARGS='$*'"
    SRV_ARGS="$*" ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$conf" -- bash -c "
        curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
        /usr/bin/python3 $LADDER --port \$PORT --log \"\$(python3 -c \"import json;print(json.load(open('$conf'))['log'])\")\" \
            --out $OUT/ladder-$tag.json --label $tag --sizes 75,512,2048 --samples 3
        if [ \"\${MIX:-0}\" = 1 ]; then
            /usr/bin/python3 $MIXED --url http://127.0.0.1:\$PORT --label $tag --singles 2 --out $OUT/mixed-$tag.json
        fi
    " 2>&1 | grep -E '=====|engine:|^  [0-9]|ttft|C2|wrote|restore'
}

# ref ladder already measured in the first attempt (ladder-ref.json); re-run it only when REF=1
[ "${REF:-0}" = 1 ] && MIX=0 arm ref
MIX=1 arm zero
echo "[$(date +%H:%M:%S)] zero-cache sweep done"
