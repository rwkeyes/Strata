#!/usr/bin/env bash
# strata-calibrate-q2_0.sh - build an expert-cache profile for the Q2_0 pack from ONE serve session.
#
# The published path (tools/collect_profile_traces.py) starts the engine once per prompt; for Q2_0 each start pays ~185 s
# of profile fill, so 45 prompts would be hours. Instead: one startup with --dump-routing, ~12 varied prompts, then
# tools/make_profile_mix.py over that trace - same record format, one startup.
#
# usage: strata-calibrate-q2_0.sh [max-new]
# UNDO: rm this file and ~/strata-trace-run/routing-q2_0.txt
set -uo pipefail
MAXNEW=${1:-120}
S=/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731
TREE=$S/strata-0.2.13
OUT=$HOME/strata-trace-run
CONF=$OUT/conf-q2_0-route.json
TRACE=$OUT/routing-q2_0.txt
mkdir -p "$OUT"
rm -f "$TRACE"

python3 - "$OUT/conf-q2_0.json" "$CONF" <<'PY'
import json, sys
src, dst = sys.argv[1:3]
d = json.load(open(src)); a = list(d["args"])
# --dump-routing is the CUDA/HIP and SYCL-host mechanism; on the SYCL device path it writes nothing (measured: 13
# prompts, 0-byte file). The device-side counter is STRATA_USAGE_DUMP, which works with STRATA_VERIFY_NO_HOST.
if "--dump-routing" in a:
    del a[a.index("--dump-routing"):a.index("--dump-routing") + 2]
d["args"] = a
d["log"] = "/media/bob/3d651e2c-e9a4-4758-ba77-863725fe3731/strata-0.2.13/strata-q2_0-usage.log"
json.dump(d, open(dst, "w"), indent=1)
print("STRATA_USAGE_DUMP ->", sys.argv[0] and "set for the engine")
PY

echo "[$(date +%H:%M:%S)] ===== calibrating q2_0 (max-new $MAXNEW)"
STRATA_USAGE_DUMP=$TRACE ~/bin/strata-card-window.sh "$TREE" 8097 --conf "$CONF" -- bash -c "
    curl -s -m 5 http://127.0.0.1:\$PORT/v1/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"  engine:\",d[\"engine\"])'
    /usr/bin/python3 $HOME/bin/strata-calibrate-prompts.py --url http://127.0.0.1:\$PORT --max-new $MAXNEW
" 2>&1 | grep -aE '=====|engine:|/[0-9]+ |corpus done|restore'
echo "[$(date +%H:%M:%S)] routing trace: $(ls -la "$TRACE" 2>/dev/null | awk '{print $5" bytes"}')"
