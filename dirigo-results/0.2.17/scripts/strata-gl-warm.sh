#!/usr/bin/env bash
# strata-gl-arms.sh - run GuideLLM (4 concurrency levels, 45 s each) against a list of Strata arms, one card
# window per arm, with production restored after every window by the card-window wrapper.
#
#   ~/bin/strata-gl-arms.sh <outdir> <label> <tree> <config> [<label> <tree> <config> ...]
#
# Warm A/B: 30 s at c=1 (warm-up only, the level that is equal in both engines) then 45 s at c=2, which is
# the band where 0.2.17 reads slower. Interleave arms A/B/A/B so drift hits both. Each arm writes <outdir>/guidellm-<label>.json and .log.  Arms run in the order given, so an interleaved
# d a b a d order lands drift on both sides.
# UNDO: rm ~/bin/strata-gl-arms.sh
set -uo pipefail
OUT=${1:?outdir}; shift
mkdir -p "$OUT"
GL=$HOME/bench-venv/bin/guidellm
TOK='kind=huggingface_auto,model=/home/bob/bench-tokenizer'
MODEL=qwen3.8-flash-next-coder-iq1_m

while [ $# -ge 3 ]; do
  label=$1 tree=$2 conf=$3; shift 3
  echo "[$(date +%H:%M:%S)] ===== $label  ($tree)"
  ~/bin/strata-card-window.sh "$tree" 8097 --conf "$conf" -- bash -c "
    curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine\",d[\"engine\"])'
    grep -hoE 'pre-filled [0-9]+ of [0-9]+ slots|[0-9]+ experts missing from VRAM' '$tree/strata-coder-iq1_m-sycl-tuned.log' 2>/dev/null | tail -2
    cd ~ && env -u PYTHONPATH '$GL' run \
      --backend 'kind=openai_http,target=http://127.0.0.1:'\$PORT'/v1,model=$MODEL' \
      --tokenizer '$TOK' --data 'kind=synthetic_text,prompt_tokens=512,output_tokens=128' \
      --profile 'kind=concurrent' --override 'profile.streams' 1,2 \
      --constraint 'kind=max_duration,seconds=45' \
      --output 'kind=json,path=$OUT/guidellm-$label.json' > '$OUT/guidellm-$label.log' 2>&1
    python3 -c \"
import json;d=json.load(open('$OUT/guidellm-$label.json'))
for b in d['benchmarks']:
    m=b['metrics']; c=b['config']['strategy']['worker_count']
    print('  c=%d  TTFT %.0f ms  %.1f tok/s  %d ok %d err' % (c,
      m['time_to_first_token_ms']['successful']['median'], m['output_tokens_per_second']['successful']['mean'],
      m['request_totals']['successful'], m['request_totals']['errored']))\"
  " 2>&1 | grep -E '=====|engine |pre-filled|experts missing|c=[0-9]|restore:'
done
echo "[$(date +%H:%M:%S)] arms done"
