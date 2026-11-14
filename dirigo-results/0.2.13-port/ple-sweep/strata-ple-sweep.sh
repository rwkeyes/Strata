#!/usr/bin/env bash
# strata-ple-sweep.sh - sweep the PLE read path's two untested knobs on the Arc.
#
# The defaults are `--ple-row-cache 1048576` (rows of 90 B = 94 MB) and `--ple-inflight 256`. The engine's own
# comment records that inflight 64 left the SSD half idle (32K read 303 -> 189 ms), so this knob family is known
# to matter and has never been swept on this box.
#
# Arms, one card window each, in this order so drift lands on both ends:
#   b       default                     (row cache 1 M, inflight 256)
#   c4      --ple-row-cache 4194304     (360 MB)
#   c16     --ple-row-cache 16777216    (1.44 GB)
#   i1k     --ple-inflight 1024
#   c16i1k  both
#   b2      default again (drift check)
#
# Each window: stop strata-b70, run the tree's own server on :8097 with a variant config, run bench_prefill (fresh
# 4.2k/8.8k + follow-ups + warmup) and longprobe (code, needle, 30k, over-window), restore the service.
#
# UNDO: rm this file and ~/strata-ple-sweep
set -uo pipefail
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
BASE=$TREE/strata-coder-iq1_m-sycl-tuned.json
OUT=$HOME/strata-ple-sweep
BENCH=/home/bob/forktest/engines/strata/tools/hip/bench_prefill.py
LONG=/home/bob/bin/strata-longprobe.py
MODEL=qwen3.8-flash-next-coder-iq1_m
mkdir -p "$OUT"

variant() {   # name extra-args...
    local name=$1; shift
    python3 - "$BASE" "$OUT/conf-$name.json" "$@" <<'PY'
import json, sys
src, dst, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
d = json.load(open(src))
d["args"] = list(d.get("args", [])) + extra
json.dump(d, open(dst, "w"), indent=1)
print(dst, "extra:", extra)
PY
}

arm() {   # tag
    local tag=$1
    local label="ple-$tag"
    local conf="$OUT/conf-$tag.json"
    echo "[$(date +%H:%M:%S)] ===== $label"
    ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$conf" -- bash -c "
        EL=\$(python3 -c \"import json;print(json.load(open('$conf'))['log'])\")
        curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
        /usr/bin/python3 $BENCH --url http://127.0.0.1:\$PORT --model $MODEL --engine-log \"\$EL\" \
            --output $OUT/bench-$label.json --label $label >/dev/null 2>&1
        /usr/bin/python3 $LONG --url http://127.0.0.1:\$PORT --model $MODEL --engine-log \"\$EL\" \
            --label $label --out $OUT/longprobe-$label.json >/dev/null 2>&1
        echo '  probes written:' \$(ls -1 $OUT/*-$label.json 2>/dev/null | wc -l)
        curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  status:\", {k:d.get(k) for k in (\"loaded\",\"engine\")})'
    " 2>&1 | grep -E '=====|engine:|probes written|status:|restore'
}

variant b
variant b2                       # identical to b, used for the closing drift check
variant c4  --ple-row-cache 4194304
variant c16 --ple-row-cache 16777216
variant i1k --ple-inflight 1024
variant c16i1k --ple-row-cache 16777216 --ple-inflight 1024

for tag in b c4 c16 i1k c16i1k b2; do
    arm "$tag"
done
echo "[$(date +%H:%M:%S)] sweep done"
