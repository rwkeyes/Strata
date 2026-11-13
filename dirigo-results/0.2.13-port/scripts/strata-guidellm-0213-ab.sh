#!/usr/bin/env bash
# GuideLLM A/B: stock Strata v0.2.11 vs our tuned deployment, same card, same tool, same workload.
# Arms run stock then ours on :8097 (production stopped), each measured with GuideLLM's concurrency sweep and the
# llama-bench-equivalent pp/tg shapes, then production is restored by one trap.
set -uo pipefail
S=<data>
P=$HOME/strata-perf
GL=$HOME/bench-venv/bin/guidellm
TOK='kind=huggingface_auto,model=<home>/bench-tokenizer'
OUT=$HOME/strata-perf/results-0213-ab

restore() {
    local rc=$?
    trap - EXIT INT TERM
    pkill -f '[s]erve/server.py --engine strata' >/dev/null 2>&1
    for _ in $(seq 1 40); do pgrep -f '[b]uild-sycl-aot/strata' >/dev/null || break; sleep 1; done
    sleep 6
    ~/bin/strata-b70-serve.sh start 2>&1 | grep -E 'engine:|experts:|over PCIe' | head -3
    exit "$rc"
}
trap restore EXIT INT TERM

start_arm() {   # label tree conf
    local label=$1 tree=$2 conf=$3
    systemctl --user stop strata-b70.service 2>/dev/null
    pkill -f '[s]erve/server.py --engine strata' >/dev/null 2>&1
    for _ in $(seq 1 40); do pgrep -f '[b]uild-sycl-aot/strata' >/dev/null || break; sleep 1; done
    for _ in $(seq 1 30); do
        gpu=$(awk '/^GPUActive/{print $2}' /proc/meminfo)
        [ "${gpu:-99999999}" -lt 4200000 ] && break
        sleep 2
    done
    ( trap - EXIT INT TERM; cd "$tree" && ONEAPI_ROOT=/opt/intel/oneapi MKLROOT=/opt/intel/oneapi/mkl/latest \
      LD_LIBRARY_PATH=/opt/intel/oneapi/compiler/latest/lib:/opt/intel/oneapi/mkl/latest/lib:/opt/intel/oneapi/tbb/latest/lib:/opt/intel/oneapi/umf/latest/lib \
      ONEAPI_DEVICE_SELECTOR=level_zero:0 SYCL_CACHE_PERSISTENT=0 ZES_ENABLE_SYSMAN=1 STRATA_SYCL_TREE=$tree \
      .venv/bin/python serve/server.py --engine strata --config "$conf" --port 8097 > "$P/serve-ab-$label.log" 2>&1 & )
    for i in $(seq 1 90); do
        curl -fsS -m 3 http://127.0.0.1:8097/v1/status 2>/dev/null | grep -q '"loaded": *true' && { echo "  loaded after $((i*2)) s"; break; }
        sleep 2
    done
    curl -fsS -m 5 http://127.0.0.1:8097/v1/status >/dev/null 2>&1 || { echo "  $label DID NOT LOAD"; return 1; }
    curl -fsS -m 5 http://127.0.0.1:8097/v1/status | python3 -c 'import json,sys;print("  engine",json.load(sys.stdin)["engine"])'
}

arm() {   # label tree conf
    local label=$1 tree=$2 conf=$3
    echo "[$(date +%H:%M:%S)] ===== $label"
    start_arm "$label" "$tree" "$conf" || return 1
    local logf; logf=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['log'])" "$conf")
    grep -hoE 'pre-filled [0-9]+ of [0-9]+ slots|[0-9]+ experts missing from VRAM' "$logf" 2>/dev/null | tail -2
    echo "  -- pp/tg"
    python3 "$HOME/bin/bench-pptg.py" --url http://127.0.0.1:8097 --label "$label" --repeats 2 \
        --out "$OUT/ab-pptg.jsonl" 2>&1 | grep -E 'pp|tg' | sed 's/^/  /'
    echo "  -- GuideLLM sweep"
    ( cd ~ && env -u PYTHONPATH "$GL" run \
        --backend "kind=openai_http,target=http://127.0.0.1:8097/v1,model=qwen3.8-flash-next-coder-iq1_m" \
        --tokenizer "$TOK" --data 'kind=synthetic_text,prompt_tokens=512,output_tokens=128' \
        --profile 'kind=concurrent' --override 'profile.streams' 1,2,4,8 \
        --constraint 'kind=max_duration,seconds=45' \
        --output "kind=json,path=$OUT/guidellm-0213ab-$label.json" ) > "$OUT/guidellm-0213ab-$label.log" 2>&1
    grep -A6 'Request Latency' "$OUT/guidellm-0213ab-$label.log" | grep -E 'concurrent@' | sed 's/^/  /'
    pkill -f '[s]erve/server.py --engine strata' >/dev/null 2>&1
    for _ in $(seq 1 40); do pgrep -f '[b]uild-sycl-aot/strata' >/dev/null || break; sleep 1; done
    sleep 4
}

arm d0213 "<data>/strata-0.2.13" "<data>/strata-0.2.13/strata-coder-iq1_m-sycl-tuned.json"
arm s0213 "<data>/strata-0.2.13-stock" "<data>/strata-0.2.13-stock/strata-coder-iq1_m-sycl-tuned.json"
echo "[$(date +%H:%M:%S)] done"
