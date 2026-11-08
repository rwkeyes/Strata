# Dirigo Strata 0.2.11 — engine payload (engine 0.1.57)

Baked engine payload for Dirigo's Strata deployment. Built **2026-11-06** on vega from
`github.com/Niko1221/Strata` at tag **`v0.2.11`** plus the 18 Dirigo commits on top of it
(HEAD `2502825d`, *serve: route the conversation cache by traffic shape*). Every binary prints
`strata 0.1.57`.

**Binaries only — no weights.** The Qwen3.8-Flash-Next pack (IQ1_M expert shards, MTP head, expert
profile) lives on the model store and is not part of this repository or its release.

## Assets

| asset | unpacks to | packed size | sha256 |
|---|---|---|---|
| `bin.tar.zst` | `bin/` — 5 engines + `libstrata_kq.so` | 621,472,833 B | `0c45a02946a3f2d44b713a23f1a2a49aab1b3411f6f784039d09fecffc1e138c` |
| `hip-runtime.tar.zst` | `hip-runtime/` — AMD HIP/ROCm runtime | 1,000,834,376 B | `cbe93d46c771b5558afe28e829707b9b3272fbb6761f9309d52d4202026e05f6` |
| `oneapi-runtime.tar.zst` | `oneapi-runtime/` — oneMKL/SYCL runtime subset | 59,208,128 B | `3ec962b77ea8104916d4777e6f9f704d5b241aed9acf1e8f2f8ff0529a7d989b` |

Checksums are also in `SHA256SUMS.assets` in this repository. Unpack all three into one directory to
get the payload root the wrappers (`strata-sycl`, `strata-hip`) and `strata-selftest.sh` expect:

```bash
mkdir -p /opt/dirigo/engines/strata && cd /opt/dirigo/engines/strata
for a in bin hip-runtime oneapi-runtime; do tar --zstd -xf $a.tar.zst; done
./strata-selftest.sh          # probes every vendor path present on this box
```

## What is in `bin/` (sizes are decimal MB, sha256 as shipped)

| binary | vendor path | architectures | size | version |
|---|---|---|---|---|
| `strata-hip-rdna` | AMD HIP, wave32 | gfx1030 1031 1100 1101 1102 1103 1150 1151 1152 1200 1201 | 505 MB | 0.1.57 |
| `strata-cuda13` | NVIDIA, CUDA 13 | sm_75 80 86 89 90 100 120 | 671 MB | 0.1.57 |
| `strata-cuda12` | NVIDIA, CUDA 12 | sm_60 61 70 75 (Pascal / Volta) | 444 MB | 0.1.57 |
| `strata-sycl-xe2` | Intel SYCL, AOT | Arc B-series (Xe2) — bmg-g31 (B70 / B60 / B580 / B570) | 104 MB | 0.1.57 |
| `strata-sycl-dg2` | Intel SYCL, JIT at first start | Arc A-series (Alchemist / DG2) | 49 MB | 0.1.57 |
| `libstrata_kq.so` | — | K-quant MMQ prompt library (ships with the CUDA engines) | 38 MB | 0.1.57 |

Architecture lists, byte sizes and per-file sha256 are machine-readable in `MANIFEST.json`;
`ARCHS` is the one-line-per-binary form.

## Verified before this release was published

Every line below is a command that was run against *these* files, not a projection:

- `bin/strata-cuda13 --version`, `bin/strata-cuda12 --version`, `bin/strata-hip-rdna --version` →
  `strata 0.1.57` on this build host.
- `bin/strata-sycl-xe2` and `bin/strata-sycl-dg2` need the bundled oneMKL, so they were run through
  their wrapper: `./strata-sycl --version` and `STRATA_SYCL_ENGINE=bin/strata-sycl-dg2 ./strata-sycl
  --version` → `strata 0.1.57`.
- `MANIFEST.json` byte sizes and sha256 recomputed from the shipped binaries: 5/5 match
  (`strata-cuda12`, `strata-cuda13`, `strata-hip-rdna`, `strata-sycl-dg2`, `strata-sycl-xe2`).
- Asset tarballs listed, not just sized: `bin.tar.zst` 7 entries, `hip-runtime.tar.zst` 1773 entries,
  `oneapi-runtime.tar.zst` 24 entries — each equal to its source tree plus the top-level directory.
- `bin/` contains all six expected files after unpacking; the SYCL wrapper selects
  `bin/strata-sycl-xe2` on a B-series card and `bin/strata-sycl-dg2` on Alchemist/DG2.

## What this is not

- **No weights.** Nothing here can fetch a model; the engine runs the Qwen3.8-Flash-Next pack family only.
- **No CPU-only lane.** Strata has no dlopen backend system, and the `strata` executable only exists in
  the CUDA/HIP/SYCL branches — without a supported GPU there is no engine to run.
- **No CUDA toolkit.** The CUDA binaries link `libcudart`/`libcublas` 12.x or 13.x; you supply those.
  The AMD and Intel runtimes *are* bundled (`hip-runtime/`, `oneapi-runtime/`).
- **Not GPU-validated by this repository.** The `--version` checks above prove the binaries load and
  link against their own bundled runtime. Proving device access and coherent tokens needs weights and
  the card; `strata-selftest.sh` does that on a real box. The GuideLLM numbers in
  `GUIDELLM-RESULTS.md` were measured on an Arc Pro B70 and apply to that card only.
- **Not announced.** The repository is private; nothing here is a public release yet.

## Provenance

- Upstream: `github.com/Niko1221/Strata`, MIT. Tag `v0.2.11`, HEAD `2502825d` (+18 commits).
- Two build-side patches carried in the tree and re-checked on this tag: the HIP `alignas` build fix
  (upstream #778) and the oneMKL link narrowing to the BLAS domain. Both are described under
  "Rebuilding" in `README.md`.
- ggml source pin `3cf0325` (llama.cpp, no download).
- Per-variant build logs as produced: `build-logs/build-{hip,cuda13,cuda12,sycl-xe2,sycl-dg2}.log`.
  The two SYCL variants reported `ninja: no work to do` — the binaries were already current for this
  tree, and were re-collected into the payload.
