# Dirigo Strata 0.2.12 — the verified tree, and what was measured

This branch is the **one place** for the Strata tree Dirigo Agents runs and the evidence behind it. It is one
snapshot commit — upstream Strata **v0.2.12** (`146df3c46`, engine **0.1.58**) plus this fork's carry, in the state
that served `:8095` on vega on 2026-11-08 — with every measurement kept under
[`dirigo-results/`](#what-is-in-dirigo-results).

The snapshot is `9fd4e15d` (5,837 tracked files); the tree hash below belongs to it. Commits after it on this branch
change documentation only and leave `src/`, `include/`, `sycl/`, `metal/`, `serve/`, `setup.py` and `CMakeLists.txt`
byte-identical, so a re-clone of the tag plus the carry still reproduces the deployed tree.

Nothing here is aspirational: each number below was produced on 2026-11-07/08 on one Intel Arc Pro B70 (32 GB), with the
model and configuration named in the same document that carries the result.

> **New to Strata?** This page is the evidence, not the tour. Read [README.md](README.md) for what this fork is and
> [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) to get running; come back here when you want the numbers.

## Provenance — what this tree is

| | |
| --- | --- |
| Base | upstream tag **v0.2.12**, commit `146df3c468739147f520ac3816de806f71232571` |
| Engine | **0.1.58** (upstream 0.2.12; v0.2.11 was 0.1.57) |
| Carry | **35 files, +5,024 / −264** — the complete delta, kept as [`dirigo-results/notes/carry-0212.patch`](dirigo-results/notes/carry-0212.patch) so the difference from the tag is auditable in one file |
| Model | Qwen3.8-Flash-Next GSQ-RCO (Coder) **IQ1_M**, pack `coder-iq1_m`, 131,072-token context |
| Build | SYCL AOT `bmg-g31`, Intel oneAPI 2026.2, `cmake -S sycl -B build-sycl-aot -DSTRATA_SYCL_AOT=bmg-g31`, `ninja -j10` |
| Verified by | clean-room re-clone of the tag + the carry → **identical git tree hash** `b5335e96…`; a from-scratch build (17 m 13 s, 0 errors); the suite; a card gate; the standard benchmark protocol; GuideLLM |

The carry, in one line each: an in-place engine retune (`TUNE` between requests) and the server levers built on it
(conversation-cache routing, per-request draft window, thinking budget by prompt, per-request draft depth, autotune
shadow mode), the calibration and payload build fixes (HIP `alignas`, oneMKL link narrowing), a prompt-kind classifier,
the measurement tooling, and a test fix that makes `test_hip_ordinal` independent of the host's AMD topology.

## Verification (2026-11-08)

| check | result | evidence |
| --- | --- | --- |
| Clean-room reproduction | fresh `git clone --branch v0.2.12` + carry = deployed tree, **same tree hash** `b5335e96a518a5c90e87aaaf92f52bfdf0f6ef04` (5,837 tracked files both) | `dirigo-results/notes/deployment-and-audit-2026-11-08.md` |
| Build from scratch | 0 errors, engine **112,593,920 bytes** (same size as deployed), `--version` → `strata 0.1.58`, AOT `bmg-g31` | same |
| Unit / integration suite | **1,535 tests, 611 s, 17 skipped, 0 failures** | [deployment record](dirigo-results/notes/deployment-and-audit-2026-11-08.md) — the raw log is not kept in this repository |
| Card gate (live service) | minefield **5/5**, one-token 200, extraction at a 24-token budget answered in 0.42 s with **0 reasoning chars**, live `POST /v1/tune` applied | deployment record |
| Standard benchmark protocol (`strata-qwen38-thinking-v1-draft.1`) | **status complete, 21/21 requests, 0 failed, 12/12 sanity**; prompt 1,404 tok/s at 4K, 1,773 at 16K; decode 80.8/68.1 tok/s; TTFT 2.94/9.27 s | [`dirigo-results/standard-benchmark/`](dirigo-results/standard-benchmark/) |
| Community-format comparison (0.2.11 / 0.2.12 / stock 0.2.12, interleaved) | 0.2.12 decode +0.4…+1.2 % over 0.2.11, within ±0.3 % of stock; needle 6/6 | [`dirigo-results/community-report/`](dirigo-results/community-report/) |

Suite history worth knowing: 1,524 tests before the per-request draft-window commit (the +11 is exactly
`test_spec_window.py`), and one failure before the topology fix — `test_hip_ordinal`, which failed on the unmodified tag
on any machine with an integrated Radeon.

## Speed against stock, measured with GuideLLM (the standard tool)

**GuideLLM 0.8.1**, `synthetic_text` 512 in / 128 out, concurrent streams 1/2/4/8, `max_duration` 45 s per point,
loopback, one slot, 0 errored requests at every point. Both arms in one session, back to back (0.2.11 tree, engine
0.1.57 → 0.2.12 tree, engine 0.1.58):

| concurrency | TTFT 0.2.11 → 0.2.12 | output tok/s 0.2.11 → 0.2.12 |
| --- | --- | --- |
| 1 | 938 → **727 ms (−22.5 %)** | 59.7 → 62.6 (+4.8 %) |
| 2 | 3 016 → **2 640 ms (−12.4 %)** | 56.9 → 62.6 (+10.0 %) |
| 4 | 7 278 → **6 114 ms (−16.0 %)** | 59.7 → 68.3 (+14.3 %) |
| 8 | 9 807 → 9 758 ms (−0.5 %) | 56.9 → 59.7 (+5.0 %) |

The difference is **this fork's conversation-cache routing, not the upstream tag**. The same 0.2.12 build with
`conv_route: shadow` (labels only, never retunes) lands exactly on 0.2.11 — 934 / 3 004 / 7 583 / 9 807 ms TTFT,
56.9–59.7 tok/s:

| concurrency | `conv_route: shadow` | `conv_route: on` (deployed) | on vs shadow |
| --- | --- | --- | --- |
| 1 | 934 ms | 727 ms | **−22.2 %** |
| 2 | 3 004 ms | 2 640 ms | −12.1 % |
| 4 | 7 583 ms | 6 114 ms | −19.4 % (tok/s **+20.0 %**) |
| 8 | 9 807 ms | 9 758 ms | −0.5 % |

Raw results: [`dirigo-results/guidellm/`](dirigo-results/guidellm/). Limits are in the write-up: one card, one slot, one
quantization, a single-shot workload — it measures the "run of single-shot prompts" side of the router, not the switch
back on a conversational turn, and the `--conv-route-batch-after` threshold was not swept.

## What else was measured, and what was rejected

Full write-up: [`dirigo-results/notes/improvements-2026-11-08.md`](dirigo-results/notes/improvements-2026-11-08.md).

* **Thinking on coding answers: measured, not adopted.** Eight one-function tasks and six harder ones (multi-step builds
  plus debugging) with executable checks: correctness **equal or worse with thinking** (14/14 vs 14/14 at a 2,000 cap;
  9/10 vs 10/10 on the harder set), while wall time is **2.5× to 10×** longer; under a 700-token budget thinking
  *truncated three answers into non-compiling code*. Not applied — it needs a traffic-shaped measurement and a
  budget-based rule, and the sample is one model on one box.
* **`--spec-min-p 0.7`: −7.0 % decode on code** (83.4 → 77.5 tok/s, n=4 interleaved). The +12 % that once justified 0.7
  was an engine 0.1.55 result. The deployed **0.5** stands.
* **`--pcie-frac` 0.30 vs the probe's 0.19: noise** — +3.4 % then −3.7 % on the same lane in two interleaved windows.
  The engine's own PCIe probe stays.
* **Prompt-lookup drafting**: `suffix_draft` 3 (the engine default) is worth about **+7 % on the code lane** — switching
  it off per request costs 6.9 %. Deeper windows (31) are not better. Copy-shaped lanes are flat because this model
  reproduces only ~20 of 120 source lines verbatim.
* **`spec_window` / `code_tune`**: implemented and exposed as launcher variables, left **off** — neither has a measured
  win on this box's traffic yet.

## What is in `dirigo-results/`

| path | what |
| --- | --- |
| `notes/deployment-and-audit-2026-11-08.md` | the 0.2.11 → 0.2.12 promotion: what changed, the evidence it took, the rollback command, the lever audit, the clean-room reproduction |
| `notes/improvements-2026-11-08.md` | every experiment of 2026-11-08 with its numbers, including the ones that failed |
| `notes/carry-0212.patch` | the complete delta against tag v0.2.12 — 35 files, +5,024/−264 |
| `unit-tests/` | not shipped in this repository: the suite result (1,535 tests, OK) is recorded in `notes/deployment-and-audit-2026-11-08.md` |
| `standard-benchmark/` | the standard-protocol result folder: `README.md`, `results.json`, `outputs.jsonl`, `config.json`, template and corpus provenance, plus the operator's hardware correction |
| `community-report/` | the three-build comparison (0.2.11 / 0.2.12 / stock 0.2.12): README, per-request JSON, the two probe scripts |
| `guidellm/` | GuideLLM 0.8.1 results: 0.2.11, 0.2.12, 0.2.12-with-shadow, and the summaries |
| `0.2.13-port/` | fork vs **stock v0.2.13**: same battery both sides, GuideLLM side by side, the chart, the raw JSON, and the correction log from re-deriving every number |
| `payload-0.2.11/` | the previous engine payload's record (MANIFEST, ARCHS, release notes, GuideLLM results, selftest) migrated from the old payload repo; the binaries themselves live with the payload, not in git |

## Run it

```sh
# build (SYCL AOT for an Intel Arc card, oneAPI 2026.2)
cmake -S sycl -B build-sycl-aot -DSTRATA_SYCL_AOT=bmg-g31
ninja -C build-sycl-aot -j10 strata

# serve (the deployed configuration: 131k context, int8 KV, 32k resident KV, prompt cache 6/4096,
# spec 6 / min-p 0.5, MTP window 8192, streamed experts, one slot)
./build-sycl-aot/strata --serve --pack <pack> --native <shard-1.gguf> --ple-gguf <shard-2.gguf> \
  --expert-profile <profile.bin> --expert-cache auto --stream-experts --prefill auto \
  --prompt-cache 6 --prompt-cache-every 4096 --batch 0 --spec 6 --spec-min-p 0.5 \
  --mtp <draft> --mtp-draft-vocab <vocab.bin> --mtp-window 8192 \
  --max-context 131072 --kv int8 --kv-resident 32768 --vram-reserve-mib 700
```

The server's own levers are read from the run config (`thinking_policy`, `conv_route`, `conv_route_batch_after`,
`spec_window`, `spec_window_needle_min`, `short_answer`, `code_tune`) or the matching flags; `GET /v1/tuning` reports
every one of them plus the tuner, and `POST /v1/tune` retunes the running engine.

## Upstream

The parts of this carry that belong upstream are open against `Niko1221/Strata` as separate, independently landable
branches on this fork:

| branch | what |
| --- | --- |
| `serve/thinking-skip-for-short-direct-answers` | #5512 — skip the thinking block for prompts that ask for a short direct answer (draft) |
| `serve/in-place-tune-and-cache-routing` | #5516 — `TUNE` between requests + `POST /v1/tune` + cache routing (draft, scope issue #5515) |
| `hip/aligned-shared-int4-staging` | #5518 — the HIP `alignas(16) int4` build fix (ready) |
| `bench/arc-pro-b70-0212` | #5522 — the community-format benchmark report (ready) |
| `bench/arc-pro-b70-standard-1` | #5556 — the standard-protocol pilot on this card (ready) |
| `tests/hip-ordinal-host-independent` | #5602 — `test_hip_ordinal` without the host's AMD topology (ready) |
| `port/0.2.13` | the 0.2.12 carry replayed onto **v0.2.13**, built and measured against stock v0.2.13 — report in [`dirigo-results/0.2.13-port/`](dirigo-results/0.2.13-port/) |

`main` on this fork stays a mirror of upstream; this branch is where Dirigo's verified state lives.
