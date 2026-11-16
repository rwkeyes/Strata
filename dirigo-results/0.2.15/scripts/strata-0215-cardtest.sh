#!/usr/bin/env bash
# strata-0215-cardtest.sh - ONE bounded card window for a NEW tree on vega's Arc Pro B70:
#   stop strata-b70.service -> wait for the engine process to exit -> start the NEW tree's engine on :8097
#   with the same tuned settings -> minefield + card-gate + levers -> stop it -> hand the card back to
#   strata-b70.service and verify with a real completion on :8095.
#
#   N=<tree> ~/bin/strata-0215-cardtest.sh            # default: the 0.2.12 Dirigo port
#   N=<stock tree> TAG=s12 ~/bin/strata-0215-cardtest.sh
#
# Adapted from strata-0211-cardtest.sh (same gate, parametrised tree). The restore lives in ONE trap and
# always runs. UNDO: rm ~/bin/strata-0215-cardtest.sh
set -uo pipefail
D=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
N=${N:-$D/strata-0.2.15}
TAG=${TAG:-f15}
CONF=$N/strata-coder-iq1_m-sycl-tuned.json
PORT=${PORT:-8097}
MODEL=qwen3.8-flash-next-coder-iq1_m
SERVE_LOG=$N/serve-tuned.log
ENGINE_LOG=$N/strata-coder-iq1_m-sycl-tuned.log
OUT=${OUT:-$HOME/strata-0215-cardtest-$TAG.log}
: > "$OUT"

export ONEAPI_ROOT=/opt/intel/oneapi
export MKLROOT=$ONEAPI_ROOT/mkl/latest
LD_LIBRARY_PATH="$ONEAPI_ROOT/compiler/latest/lib:$MKLROOT/lib:$ONEAPI_ROOT/tbb/latest/lib:$ONEAPI_ROOT/umf/latest/lib"
export LD_LIBRARY_PATH
export ONEAPI_DEVICE_SELECTOR=level_zero:0 SYCL_CACHE_PERSISTENT=0 ZES_ENABLE_SYSMAN=1
export STRATA_SYCL_TREE=$N

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$OUT"; }
vram() { grep -iE '^GPUActive' /proc/meminfo | tr -s ' '; }
engine_alive() { pgrep -f '[b]uild-sycl-aot/strata --serve' >/dev/null; }
SRV=""

restore() {
  local rc=$?
  trap - EXIT INT TERM
  if [ -n "$SRV" ] && kill -0 "$SRV" 2>/dev/null; then
    log "restore: stopping the test server (pid $SRV)"
    kill "$SRV" 2>/dev/null
  fi
  for _ in {1..45}; do engine_alive || break; sleep 2; done
  engine_alive && log "restore: WARN engine still alive after 90 s"
  log "restore: vram used $(vram)"
  systemctl --user start strata-b70.service
  local code=""
  for _ in {1..150}; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8095/v1/chat/completions \
      -H 'content-type: application/json' \
      -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}],\"max_tokens\":1}" 2>/dev/null)
    [ "$code" = "200" ] && { log "restore: :8095 answers 200 - $(curl -fsS -m 5 http://127.0.0.1:8095/v1/status | head -c 200)"; break; }
    sleep 2
  done
  [ "$code" = "200" ] || log "restore: FAILED - :8095 last code $code (see ${N}/serve-tuned.log)"
  exit "$rc"
}
trap restore EXIT INT TERM

log "=== card window for $TAG ($N) on :$PORT (engine log $ENGINE_LOG)"
log "incumbent: $(systemctl --user is-active strata-b70.service) vram $(vram)"

# the tuned config for THIS tree, written by the one script that owns the flags
if ! STRATA_TREE="$N" "$HOME/bin/strata-b70-serve.sh" config >/dev/null 2>&1; then
  log "FAIL: strata-b70-serve.sh config failed for $N"; exit 2
fi
[ -f "$CONF" ] || { log "FAIL: no config $CONF"; exit 2; }

systemctl --user stop strata-b70.service
for _ in {1..45}; do engine_alive || break; sleep 2; done
engine_alive && log "WARN: incumbent engine still alive"
sleep 6
log "card released: vram $(vram)"

# the C++ kernel-parity battery: the same gate the release check runs, and it needs the card to itself
log "--- ctest (kernel parity battery, AOT bmg-g31)"
ctest_out=$(STRATA_SYCL_TREE="$N" "$HOME/bin/strata-0143-ctest.sh" 2>&1); ct=$?
printf '%s\n' "$ctest_out" | grep -E 'tests passed|tests failed out of|The following tests FAILED' | head -3 | tee -a "$OUT"
printf '%s\n' "$ctest_out" | sed -n '/The following tests FAILED/,$p' | grep -oE '^\s+[0-9]+ - [a-z0-9_]+' | sed 's/.*- //' | sort | tr '\n' ' ' | sed 's/^/  failed: /' | tee -a "$OUT"
log "  ctest rc=$ct"

cd "$N" || exit 2
: > "$SERVE_LOG"
nohup .venv/bin/python serve/server.py --engine strata --config "$CONF" --port "$PORT" >> "$SERVE_LOG" 2>&1 &
SRV=$!
log "test server pid $SRV, waiting on /v1/status loaded"

ready=no
for i in {1..150}; do
  if ! kill -0 "$SRV" 2>/dev/null; then log "FAIL: server died - last lines:"; tail -15 "$SERVE_LOG" | tee -a "$OUT"; exit 3; fi
  curl -fsS -m 3 "http://127.0.0.1:$PORT/v1/status" 2>/dev/null | grep -q '"loaded": *true' && { ready=yes; log "loaded after $((i*2)) s"; break; }
  [ $((i % 15)) -eq 0 ] && log "  still loading $((i*2)) s (vram $(vram))"
  sleep 2
done
[ "$ready" = yes ] || { log "FAIL: not loaded after 300 s - last lines:"; tail -20 "$SERVE_LOG" | tee -a "$OUT"; exit 4; }

log "--- engine's own lines from this start:"
grep -E 'engine started|session is up|pre-filled [0-9]+ of [0-9]+ slots|experts missing from VRAM|KV streaming' "$ENGINE_LOG" 2>/dev/null | tail -6 | tee -a "$OUT"
log "--- /v1/status:"; curl -fsS -m 5 "http://127.0.0.1:$PORT/v1/status" | head -c 400 | tee -a "$OUT"; echo

log "--- minefield (5 dequant probes)"
python3 "$HOME/bin/strata-minefield.py" --url "http://127.0.0.1:$PORT" --json "$HOME/strata-0215-minefield-$TAG.json" 2>&1 | tail -12 | tee -a "$OUT"
mf=${PIPESTATUS[0]}

log "--- card-gate (one-token 200, code answer, needle, over-window refusal)"
python3 "$HOME/.hermes/skills/mlops/strata-llm-engine/scripts/card-gate.py" \
  --url "http://127.0.0.1:$PORT" --model "$MODEL" --window-tokens 131072 2>&1 | tee -a "$OUT"
gate=${PIPESTATUS[0]}

log "--- the levers this tree carries (/v1/tuning, then a live retune; stock answers 404 = expected)"
python3 - "$PORT" <<'PY' 2>&1 | tee -a "$OUT"
import json, sys, urllib.request
port = sys.argv[1]
def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.load(r)
try:
    t = get("/v1/tuning")
    print("modes:", {k: (v.get("mode") if isinstance(v, dict) else v)
                     for k, v in t.items() if k in ("mode", "thinking", "code_tune", "short_answer")})
except Exception as e:
    print("tuning REFUSED:", e)
body = json.dumps({"settings": {"adapt_every": 6, "spec_min_p": 0.7}}).encode()
req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/tune", data=body,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print("tune:", r.status, json.load(r).get("applied"))
except Exception as e:
    print("tune REFUSED:", e)
PY
lever=$?

log "--- 24-token extraction (the answer must not be eaten by a reasoning channel)"
python3 - "$PORT" "$MODEL" <<'PY2' 2>&1 | tee -a "$OUT"
import json, sys, urllib.request
port, model = sys.argv[1], sys.argv[2]
body = json.dumps({"model": model, "max_tokens": 24, "temperature": 0,
                   "messages": [{"role": "user", "content":
                                 "Reply with only the ISO date (YYYY-MM-DD) of 9 June 2024. No other text."}]}).encode()
req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    print("extraction: content=%r reasoning_chars=%d finish=%s"
          % (msg.get("content"), len(msg.get("reasoning_content") or ""), ch.get("finish_reason")))
    print("  usage:", (d.get("usage") or {}).get("completion_tokens"), "completion tokens")
except Exception as e:
    print("extraction REFUSED:", e)
PY2
extract=$?

log "RESULT ctest=$ct minefield=$mf gate=$gate lever=$lever extraction=$extract"
[ "$mf" -eq 0 ] && [ "$gate" -eq 0 ] && exit 0
exit 1
