#!/usr/bin/env bash
# strata-0213-ab.sh - our 0.2.13 port vs stock 0.2.13, the same battery used for 0.2.11/0.2.12:
#   bench_prefill (fresh 4.2k / 8.8k and their follow-ups) + longprobe (code, 30k prompt, 7.9k needle, over-window),
#   one engine per window, four windows in the order d s s d so drift lands on both arms.
# UNDO: rm this file and ~/strata-0213-ab
set -uo pipefail
S=<data>
OUT=$HOME/strata-0213-ab
BENCH=<home>/forktest/engines/strata/tools/hip/bench_prefill.py
LONG=<home>/bin/strata-longprobe.py
MODEL=qwen3.8-flash-next-coder-iq1_m
mkdir -p "$OUT"

arm() {   # tag tree conf pass
    local tag=$1 tree=$2 conf=$3 pass=$4
    local label="$tag-p$pass"
    echo "[$(date +%H:%M:%S)] ===== $label ($tree)"
    ~/bin/strata-card-window.sh "$tree" 8097 --conf "$conf" -- bash -c "
        EL=\$(python3 -c \"import json;print(json.load(open('$conf'))['log'])\")
        curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
        /usr/bin/python3 $BENCH --url http://127.0.0.1:\$PORT --model $MODEL --engine-log \"\$EL\" \
            --output $OUT/bench-$label.json --label $label >/dev/null 2>&1
        /usr/bin/python3 $LONG --url http://127.0.0.1:\$PORT --model $MODEL --engine-log \"\$EL\" \
            --label $label --out $OUT/longprobe-$label.json >/dev/null 2>&1
        echo '  probes written:' \$(ls -1 $OUT/*-$label.json | wc -l)
    " 2>&1 | grep -E '=====|engine:|probes written|restore|over PCIe|pre-filled'
}

for pass in 1 2; do
    if [ "$pass" = "1" ]; then order="d s"; else order="s d"; fi
    for a in $order; do
        if [ "$a" = "d" ]; then
            arm d0213 "$S/strata-0.2.13" "$S/strata-0.2.13/strata-coder-iq1_m-sycl-tuned.json" "$pass"
        else
            arm s0213 "$S/strata-0.2.13-stock" "$S/strata-0.2.13-stock/strata-coder-iq1_m-sycl-tuned.json" "$pass"
        fi
    done
done
echo "[$(date +%H:%M:%S)] battery done"
