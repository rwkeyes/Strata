#!/bin/bash
# Copyright (c) 2025-2026 Dirigo Agents LLC. See LICENSE.txt for terms.
#
# strata-selftest.sh - prove the Strata payload in THIS image works on THIS box, or say exactly why not.
#
# Written because the payload's three vendor paths fail in three different ways that all look alike from
# outside ("no output"), and because the interesting failure is a path that LOADS and then finds no
# matching code object - which no amount of file listing reveals.
#
#   strata-selftest              probe every vendor path present in the payload
#   strata-selftest --pack DIR [--engine-args "--native S --spec T --prefill C"]   the definitive test
#   strata-selftest --force-arch gfx1100   exercise the AMD branch on a box without an AMD dGPU
#   strata-selftest --json       machine-readable (the advisor can read this)
#
# Exit: 0 = at least one path is READY; 1 = nothing on this box is supported; 2 = a path is PRESENT BUT
# BROKEN (a bug in the image, not a hardware mismatch) - this is the code QA should alarm on.
# Depth without weights: the engine parses its arguments BEFORE it touches a backend, so no flag-only
# invocation can prove device access. What is proven here is: the binary loads and links against its OWN
# bundled runtime, the family's code object is inside it, and the driver sees a card of that family.
# The last mile (weights -> coherent tokens) needs --pack; the verdict says so rather than pretending.
set -u

# Resolve the script's OWN path through symlinks: the image installs this as /usr/local/bin/strata-selftest,
# and a bare $(dirname $0) then points at /usr/local/bin, where there is no payload -> "FATAL: no payload".
_SELF="$(readlink -f "$0" 2>/dev/null || echo "$0")"
PAYLOAD_DEFAULT="$(cd "$(dirname "$_SELF")" && pwd)"
[ -d "$PAYLOAD_DEFAULT/bin" ] || { [ -d /opt/dirigo/engines/strata/bin ] && PAYLOAD_DEFAULT=/opt/dirigo/engines/strata; }
PAYLOAD="${STRATA_PAYLOAD:-$PAYLOAD_DEFAULT}"
PACK=""; FORCE_ARCH=""; JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pack) PACK="${2:-}"; shift 2 ;;
    --engine-args) ENGINE_ARGS="${2:-}"; shift 2 ;;   # e.g. --native SHARD1 --spec T --prefill CHUNK
    --force-arch) FORCE_ARCH="${2:-}"; shift 2 ;;
    --json) JSON=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done
[ -d "$PAYLOAD/bin" ] || { echo "FATAL: no payload at $PAYLOAD (set STRATA_PAYLOAD)"; exit 2; }

READY=0; BROKEN=0; OUT=""; AMD_OK=""; NV_OK=""; INTEL_OK=""
say() { [ "$JSON" = 1 ] || printf '%s\n' "$*"; }
# verdict <path> <state> <engine> <detail...>
verdict() { local p="$1" st="$2" e="$3"; shift 3
  OUT="${OUT}${OUT:+,}{\"path\":\"$p\",\"state\":\"$st\",\"engine\":\"$e\",\"detail\":\"$(printf '%s' "$*" | tr '"' "'")\"}"
  say "[$st] $p  ${e:+engine: $e  }$*"
  case "$st" in READY) READY=1 ;; BROKEN|RUNTIME-BROKEN|FAILED) BROKEN=1 ;; esac
}

# ---- the engine's own runtime must supply every library it names --------------------------------
# A host copy of ROCm/oneAPI can mask a hole in the bundle until the image boots without it, so the
# check is done with the bundle alone and any load from outside it is a failure.
runtime_closure_ok() {   # <binary> <libdir> [extra libdir]
  local bin="$1" libdir="$2" extra="${3:-}" out
  out=$(ROCM_PATH=/nonexistent LD_LIBRARY_PATH="$libdir${extra:+:$extra}" ldd "$bin" 2>&1)
  if printf '%s' "$out" | grep -q "not found"; then
    printf '%s' "$(printf '%s' "$out" | grep 'not found' | head -2 | tr '\n' ';')"; return 1
  fi
  local ext b have
  ext=$(ROCM_PATH=/nonexistent LD_LIBRARY_PATH="$libdir${extra:+:$extra}" LD_DEBUG=libs "$bin" --version 2>&1 \
        | sed -n 's/.*calling init: \(.*\)/\1/p' | sort -u | grep -E '^/(opt|usr/local)/(rocm|intel)')
  have=""
  for b in $(printf '%s\n' "$ext"); do
    # only a library the bundle ALSO carries means the loader chose the host copy over ours
    if [ -e "$libdir/$(basename "$b")" ] || { [ -n "$extra" ] && [ -e "$extra/$(basename "$b")" ]; }; then
      have="$have $b"
    fi
  done
  [ -z "$have" ] || { printf 'the host copy of a bundled library wins: %s' "$have"; return 1; }
  return 0
}
# ---- does the binary carry this family's device code? -------------------------------------------
# The image has no CUDA/ROCm tooling, so the check must work with binutils alone (a false negative here
# would report a working engine as unsupported, the worst possible failure mode for a self-test).
has_code_object() {      # <binary> <gfx|sm_xx>
  local bin="$1" a="$2" n=0
  if command -v roc-obj-ls >/dev/null 2>&1 && [ "${a#gfx}" != "$a" ]; then
    n=$(roc-obj-ls -v "$bin" 2>/dev/null | grep -c "$a" || true)
  fi
  if [ "${n:-0}" = 0 ] && command -v cuobjdump >/dev/null 2>&1 && [ "${a#sm_}" != "$a" ]; then
    n=$(cuobjdump --list-elf "$bin" 2>/dev/null | grep -c "$a" || true)
  fi
  if [ "${n:-0}" = 0 ] && command -v readelf >/dev/null 2>&1; then
    n=$(readelf -S "$bin" 2>/dev/null | grep -c "$a" || true)          # .text.<arch> section names
  fi
  if [ "${n:-0}" = 0 ]; then n=$(strings -a "$bin" 2>/dev/null | grep -c "$a" || true); fi
  [ "${n:-0}" -gt 0 ]
}
kernel_dir_covers() {    # <libdir> <gfx>
  local d="$1" a="$2"
  [ -d "$d/lib/rocblas/library" ] || return 0   # no kernel dir = the engine's own kernels only
  ls "$d/lib/rocblas/library" 2>/dev/null | grep -q "$a" || return 1
  return 0
}

# ---- 1. AMD / HIP -------------------------------------------------------------------------------
# Decode a KFD gfx_target_version (e.g. 100306, 110000) into an arch name: major*10000+minor*100+step,
# so 100306 -> 10.3.6 -> gfx1036 and 110000 -> 11.0.0 -> gfx1100. A fixed table was wrong for gfx1036
# (100306, not 100304) and silently skipped such a card, hiding the reason; a decode that is right for
# every value we can check beats a table that rots. The legacy collector's explicit values stay as overrides.
AMD_ARCHS="$(awk '/strata-hip-rdna/{for(i=3;i<=NF;i++)print $i}' "$PAYLOAD/ARCHS" 2>/dev/null | tr '\n' ' ')"
decode_gfx() {
  local v="$1" major minor step arch
  case "$v" in
    110100|110101|110102|110103) echo gfx1101; return ;;   # legacy collector values, kept as overrides
    110200|110201|110202)        echo gfx1102; return ;;
    110300)                      echo gfx1103; return ;;
    100300|100302)               echo gfx1030; return ;;
    100301)                      echo gfx1031; return ;;
    100303)                      echo gfx1032; return ;;
  esac
  [ "${#v}" -eq 6 ] || return 1
  major=$(printf '%s' "$v" | cut -c1-2); minor=$(printf '%s' "$v" | cut -c3-4); step=$(printf '%s' "$v" | cut -c5-6)
  arch=$(printf 'gfx%s%d%d' "$(printf '%d' "$major")" "$(printf '%d' "$minor")" "$(printf '%d' "$step")")
  printf '%s' "$arch"
}
amd_gfx=""; amd_raw=""
if [ "${FORCE_ARCH#gfx}" != "$FORCE_ARCH" ]; then amd_gfx="$FORCE_ARCH"; else
  for p in /sys/class/kfd/kfd/topology/nodes/*/properties; do
    [ -f "$p" ] || continue
    v=$(awk '/gfx_target_version/{print $2}' "$p" 2>/dev/null)
    [ -n "${v:-}" ] || continue
    [ "$v" = "0" ] && continue
    d=$(decode_gfx "$v" || true)
    if [ -n "$d" ]; then amd_gfx="$d"; amd_raw="$v"; break; fi
    [ -z "$amd_raw" ] && amd_raw="$v"      # a real card whose encoding we do not know: report it, do not hide it
  done
fi
if [ -n "$amd_gfx" ]; then
  H="$PAYLOAD/hip-runtime"; B="$PAYLOAD/bin/strata-hip-rdna"; RT_ORIGIN="bundled hip-runtime/"
  # The runtime may come from the dirigo-rocm-runtime package (or a system ROCm) instead of a
  # bundled copy: the image no longer carries 1.7 GB of private ROCm per payload. Same
  # libraries, same sonames — the self-test just has to look where they actually are.
  if [ ! -d "$H/lib" ]; then
    for _cand in /usr/lib/dirigo/rocm /opt/rocm; do
      if [ -d "$_cand/lib" ]; then H="$_cand"; RT_ORIGIN="system runtime ($_cand)"; break; fi
    done
    unset _cand
  fi
  if [ ! -x "$B" ]; then verdict amd-broken BROKEN - "bin/strata-hip-rdna is missing or not executable"
  elif [ ! -d "$H/lib" ]; then verdict amd-broken BROKEN - "no HIP runtime: neither hip-runtime/ nor /usr/lib/dirigo/rocm/lib"
  elif ! has_code_object "$B" "$amd_gfx"; then
    verdict amd UNSUPPORTED - "the AMD card here reports ${amd_raw:-unknown} -> $amd_gfx, and this engine carries no code object for it. Supported AMD: ${AMD_ARCHS:-see ARCHS}. RDNA1 (gfx101x) and gfx1034/1035/1036/104X are outside the payload's scope"
  elif ! kernel_dir_covers "$H" "$amd_gfx"; then
    verdict amd "$amd_gfx" - "engine has $amd_gfx code, but the bundled rocBLAS kernels do not - the GEMM fallback would be taken"
  else
    d=$(runtime_closure_ok "$B" "$H/lib" "$H/lib/rocm_sysdeps/lib") && AMD_OK=1 \
      && verdict amd READY "strata-hip-rdna" "code object for $amd_gfx present, rocBLAS kernels present, loads against $RT_ORIGIN alone${PACK:+; asserting a pack run}" \
      || verdict amd RUNTIME-BROKEN "strata-hip-rdna" "$d"
  fi
fi

# ---- 2. NVIDIA / CUDA ---------------------------------------------------------------------------
cc=""; if [ "${FORCE_ARCH#sm_}" != "$FORCE_ARCH" ]; then cc="${FORCE_ARCH#sm_}"; else
  if command -v nvidia-smi >/dev/null 2>&1; then
    # nvidia-smi prints a sentence (not an error) when the driver cannot be reached - take digits only
    cc=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' .' \
         | grep -oE '^[0-9]{2,3}' || true)
    [ -n "$cc" ] || cc=""
  fi
fi
if [ -n "$cc" ]; then
  eng=""; case "$cc" in 60|61|62|70|72|75) eng=strata-cuda12 ;; 75|80|86|89|90|100|101|120) eng=strata-cuda13 ;; esac
  [ "$cc" = "75" ] && eng=strata-cuda13   # sm_75 is in both; cuda13 is the newer toolchain
  if [ -z "$eng" ]; then
    verdict nvidia UNSUPPORTED - "compute capability $cc is not in either CUDA build (sm_60..sm_75 = cuda12, sm_75..sm_120 = cuda13); pre-Pascal needs an older CUDA and is not supported"
  else
    B="$PAYLOAD/bin/$eng"
    if [ ! -x "$B" ]; then verdict nvidia-broken BROKEN - "bin/$eng is missing"
    elif ! has_code_object "$B" "sm_$cc"; then verdict nvidia UNSUPPORTED - "$eng has no sm_$cc code object (PTX only); the driver would JIT it, which is supported but slower to start"
    else
      NV_OK=1; verdict nvidia READY "$eng" "sm_$cc code object present; needs the image's CUDA runtime (libcudart/libcublas), the driver's own nvidia-smi reports the card"
    fi
  fi
fi

# ---- 3. Intel / SYCL ----------------------------------------------------------------------------
intel=""; case "$FORCE_ARCH" in 0x56*) intel=alchemist ;; 0xe2*|0xb0*) intel=battlemage ;; esac
if [ -n "$intel" ]; then :; else
  for c in /sys/class/drm/card[0-9]*/device; do
    [ -r "$c/vendor" ] || continue
    [ "$(cat "$c/vendor" 2>/dev/null)" = "0x8086" ] || continue
    id=$(cat "$c/device" 2>/dev/null)
    case "$id" in 0x56??|0x4f??) intel="alchemist" ;; 0xe2??|0xb0??) intel="battlemage" ;; esac
    [ -n "$intel" ] && break
  done
fi
if [ -n "$intel" ]; then
  case "$intel" in
    alchemist) B="$PAYLOAD/bin/strata-sycl-dg2"; note="A-series (Alchemist): the JIT engine - 20-60 s of kernel compilation on the first token" ;;
    battlemage) B="$PAYLOAD/bin/strata-sycl-xe2"; note="B-series (Xe2): the AOT engine" ;;
    *) B=""; note="device $intel is not an Arc family this payload covers" ;;
  esac
  O="$PAYLOAD/oneapi-runtime"
  if [ -z "$B" ]; then verdict intel UNSUPPORTED - "$note"
  elif [ ! -x "$B" ]; then verdict intel-broken BROKEN - "bin/$(basename "$B") is missing"
  elif [ ! -d "$O/lib" ]; then verdict intel-broken BROKEN - "oneapi-runtime/lib is missing"
  else
    # the adapters are dlopen'd, so their absence is invisible to ldd: check them by name
    miss=""
    for a in libur_adapter_level_zero.so.0 libur_adapter_level_zero_v2.so.0 libumf.so.1 libmkl_sycl_blas.so.6; do
      ls "$O/lib/$a" >/dev/null 2>&1 || miss="$miss $a"
    done
    if [ -n "$miss" ]; then verdict intel RUNTIME-BROKEN "$(basename "$B")" "oneapi-runtime is missing:$miss"
    else
      d=$(runtime_closure_ok "$B" "$O/lib") && INTEL_OK=1 \
        && verdict intel READY "$(basename "$B")" "$note; loads against oneapi-runtime/ alone (Level-Zero adapter + libumf present)" \
        || verdict intel RUNTIME-BROKEN "$(basename "$B")" "$d"
    fi
  fi
fi

# ---- 4. the definitive test, when weights are available -----------------------------------------
if [ -n "$PACK" ]; then
  # the runner must match the DETECTED hardware: this picked the HIP wrapper on an Intel box once, which
  # reported a perfectly runnable pack as FAILED.
  run=""
  if [ -n "$AMD_OK" ] && [ -x "$PAYLOAD/strata-hip" ]; then run="$PAYLOAD/strata-hip"
  elif [ -n "$NV_OK" ] && [ -x "$PAYLOAD/bin/${NV_ENG:-strata-cuda13}" ]; then run="$PAYLOAD/bin/${NV_ENG:-strata-cuda13}"   # CUDA has no wrapper; the image's CUDA libs are used
  elif [ -n "$INTEL_OK" ] && [ -x "$PAYLOAD/strata-sycl" ]; then run="$PAYLOAD/strata-sycl"
  fi
  if [ -z "$run" ]; then verdict pack SKIPPED - "--pack given but no wrapper matches this hardware"
  else
    say ""
    say "== running one token through $PACK with $run (this is the test that catches silent garbling)"
    if timeout 1800 "$run" --pack "$PACK" --tokens 1,2,3 ${ENGINE_ARGS:-} >/tmp/strata-selftest.out 2>&1; then
      verdict pack RUNS "$(basename "$run")" "a token was produced; inspect the output for COHERENCE - a build can run at full speed and still be wrong (see the AVX1-floor ik_llama case)"
      tail -3 /tmp/strata-selftest.out
    else
      rc=$?
      # a native (IQ) pack refuses a plain --pack run and names the flags it wants; say so instead of "failed"
      if grep -q "is a native (IQ) pack" /tmp/strata-selftest.out 2>/dev/null; then
        verdict pack NEEDS-RECIPE "$(basename "$run")" "this pack is native (IQ): re-run with --engine-args \"--native SHARD1 --spec T --prefill CHUNK\" (T >= 2, all three together); on a box with the xe driver add --stream-experts or the experts are computed on the CPU"
      elif grep -q "ple-gguf is required" /tmp/strata-selftest.out 2>/dev/null; then
        verdict pack NEEDS-RECIPE "$(basename "$run")" "the engine wants the PLE table: add --engine-args \"--ple-gguf <the model's second GGUF shard>\" (or --no-ple for an explicit diagnostic ablation)"
      else
        verdict pack FAILED "$(basename "$run")" "exit $rc - see /tmp/strata-selftest.out"; tail -4 /tmp/strata-selftest.out
      fi
    fi
  fi
fi

# ---- summary ------------------------------------------------------------------------------------
if [ "$JSON" = 1 ]; then
  printf '{"payload":"%s","paths":[%s],"any_ready":%s,"any_broken":%s}\n' \
    "$PAYLOAD" "$OUT" "$([ "$READY" = 1 ] && echo true || echo false)" "$([ "$BROKEN" = 1 ] && echo true || echo false)"
else
  say ""
  if [ -z "$OUT" ]; then say "No GPU this payload supports is present (or no GPU at all)."
  elif [ "$BROKEN" = 1 ]; then say "RESULT: a path in this image is BROKEN - see the [BROKEN] line above."
  elif [ "$READY" = 1 ]; then say "RESULT: ready. Add --pack DIR to run the end-to-end test."
  else say "RESULT: this box is not in the payload's support set; nothing to run."
  fi
fi
[ "$BROKEN" = 1 ] && exit 2
[ "$READY" = 1 ] && exit 0
exit 1
