# Performance: what has been tried, and what is left

This page exists so the next session does not re-derive a month of measurements. It covers the Arc Pro B70
(SYCL, `bmg-g31`) deployment and the fork's tree; every line points at the evidence, and the closed list is as
important as the open one.

## 1. Tried and kept

| lever | what it bought | recorded in |
| --- | --- | --- |
| conversation-cache routing (server) | first token **−22.1 %** at one stream against stock 0.2.13; the engine itself at parity | [`0.2.13-port/`](../dirigo-results/0.2.13-port/) |
| answer-length draft depth (`spec=4` at ≤16 tokens) | **+11.3 %** generation rate on short answers | `serve/`, release notes 0.2.10 |
| thinking suppressed for short asks | **−58 % wall, −89.5 % tokens** on direct-answer turns | `serve/prompt_kind.py` |
| prompt parking off | **−25…−30 %** on follow-ups (it recovered a cost our launcher had added) | launcher, 0.2.10 round |
| model on NVMe instead of the spinning disk | a 2,655-token code prompt went from *watchdog kill* to **3.0 s** | `PERF-IDEAS.md` §6 |
| calibration: one load, warm path, skipped confirmations | **394 s → 276 s** per calibration | `tools/calibrate.py`, `AUTOTUNE.md` |
| KV `int8` + `--kv-resident 32768` | the only KV format that pays here | release notes, `AMD_HIP_PERFORMANCE.md` |
| prefill chunks 8,192 (upstream 0.1.13), layer-major prefill (0.2.7) | prompt reads at 1.3–1.8k tok/s on long prompts | `docs/DETAILS.md` history table |
| engines 0.2.10 → 0.2.13 | decode +0.4…+1.2 % per release | [`notes/`](../dirigo-results/notes/) |

## 2. Tried and closed — with the number that killed it

| lever | result |
| --- | --- |
| `--batch 4` (several requests at once) | **wedges the GPU** (`UR_RESULT_ERROR_DEVICE_LOST`, Xe reset); batch 2 is stable but **−9…−36 %** throughput |
| `--kv-grow` | neutral on this box (prefill 1 172 vs 1 170 tok/s, decode 74.0 vs 69.1 within spread) |
| `--suffix-draft` 0/15/31/63, `--lookup-chain`, per-request `lookup` | inert: 71.6 / 72.7 / 72.4 / 71.7 tok/s; ±0.5 % on every lane |
| `--prefill auto:16384` / `:32768` | +8.3 % on a fresh 8.8k, but **+21 % wall** on a cached follow-up; 32k also −5.0 % on 30k |
| `--prompt-cache-tail` | every lane within ±0.5 % |
| `--conversation-cache-mib 4096` | flat (the eviction counter's advice did not survive a paired run) |
| `--spec-min-p 0.7` | **−7.0 %** decode on code; 0.5 stands |
| `--mtp-max-t` as an independent knob | it is `--spec` by another name |
| 4-bit KV (`k8v4`, `q4_0`) | prefill **−26 % / −29 %** |
| `--ple-io ram` | needs the 28.8 GB table in RAM; this box has 30 GB total, and the 92 GB box OOM'd under a 58 GB model set |
| `--expert-cache` size arms | flat: 58.19–58.21 tok/s across three arms |
| `--ple-row-cache` 4 M / 16 M rows, `--ple-inflight` 1024 | 4 M rows and inflight 1024 are **flat** (inside the 0.2–3.6 % drift band); 16 M rows (1.44 GB) costs **−8 % on fresh 4.2k, −8…−12 % on 8.8k, −17 % on a 4.4k follow-up, −10 % on 30k**. The 94 MB default and inflight 256 stand — [the sweep](../dirigo-results/0.2.13-port/ple-sweep/) |
| `spec_window` policy | recovered only half the retrieval loss (+1.3…4.8 % vs +3.6…7.5 %) — left off |
| DFlash2 / DSpark drafters | **CUDA/HIP only**; the SYCL delta in 0.2.12 is 3 lines in `prefill.cpp` |

## 3. Where the remaining time is

Fitted over the 18 fresh legs of the 0.2.13 battery: **prefill ≈ 336 ms fixed + 0.67 ms/token** (1,492 tok/s
marginal). A 113-token prompt costs 409 ms, of which ~330 ms is not tokens. Decode runs 48–115 tok/s depending on
the lane. The engine is within 1.1 % of stock on all eight legs, so what is left is either upstream's or in that
fixed term.

## 4. Not tried, ranked

| # | idea | evidence it could pay | cost | first test |
| --- | --- | --- | --- | --- |
| 1 | ship `conv-route-batch-after` 1–2 | measured **−21 % median TTFT** (0.371 → 0.293 s), summed −29 %, steady state at request 2 instead of 5; the default is still 4 | a flag + a probe | a chat-after-single-shot shape to price the flip-back |
| 2 | attribute the ~330 ms fixed per-request cost | nobody has split it on SYCL: slot init, expert warm-up for the first layers, KV allocation. The PLE read path is now excluded by measurement (§5) | an hour, no code | `bench_prefill` at 8/16/32/64/128/256 tokens |
| 3 | residency-biased router on SYCL | `route_prior.cu` is CUDA-only; here **12,288 of 24,576** experts are resident, so half of every token's experts cross PCIe. Upstream measured +15 % / +37 % on a 12 GB NVIDIA card | engine port, days | decode lanes, 5 interleaved pairs |
| 4 | prefill chunk at 4k/8k/12k with intervals | 16k/32k were tested, but 8,192 was never shown to be the knee | ~20 min | paired, three prompt sizes, per-prompt medians |
| 5 | CPU pool workers and affinity on the 7700X | 15 workers is a 16-thread default; p-core pinning was **+27 %** decode on the 13700K | 15 min | workers 8/12/15/16, `--pool-affinity` on/off |
| 6 | dispatch overhead on SYCL | the Vulkan analysis found ~88 % of per-dispatch cost is fixed; nobody has counted dispatches per token on SYCL | a day | `unitrace` kernel count per token, then fuse the five most frequent small ops |
| 7 | DFlash2 block drafter on SYCL | upstream's headline decode lever; 7 drafts per pass | weeks | only after 1–3 |

## 5. The PLE sweep, run 2026-11-14 — closed

Six card windows on the 0.2.13 tree, `bench_prefill` plus `longprobe` in each, `strata-b70` restored between
windows and proven with a real completion. Prefill tok/s, median per leg:

| leg (prompt) | default | cache 4 M | cache 16 M | inflight 1024 | both | default again |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fresh (4,210) | 1,310 | 1,309 | **1,205** | 1,312 | 1,233 | 1,312 |
| follow-up (4,451) | 325 | 321 | **269** | 322 | 323 | 321 |
| needle (7,855) | 1,432 | 1,432 | 1,474 | 1,439 | 1,470 | 1,428 |
| fresh (8,830) | 1,344 | 1,398 | **1,231** | 1,395 | 1,224 | 1,392 |
| follow-up (9,071) | 271 | 268 | 264 | 268 | 255 | 269 |
| long (30,178) | 1,820 | 1,809 | **1,640** | 1,818 | **1,576** | 1,813 |

The two default arms bound the drift: 0.2 % on the 4.2k leg, 3.6 % on the 8.8k one, ≤1.2 % elsewhere. Against that
band, **cache 4 M rows and inflight 1024 are flat, and cache 16 M rows is 8–17 % slower** — the 1.44 GB allocation
costs more than the row hits it buys on unique prompts, and inflight 256 was not starving the drive.

What this rules out: the fixed ~330 ms per request is **not** PLE row-read starvation. What it does not rule out: a
bigger cache helping repeat traffic that re-reads the same n-grams — no leg here does that, because follow-ups
reuse the KV cache instead of re-reading rows.

Artifacts: [`0.2.13-port/ple-sweep/`](../dirigo-results/0.2.13-port/ple-sweep/) — the six configs, the per-leg JSON, the log with each
window's restore line, `summary.json`, and the script.

## 6. Method rules these numbers were produced under

- Paired or interleaved arms; median of repeats; the box's run-to-run spread stated next to the effect.
- One prompt per configuration is not a measurement: every lane here has several requests, and a single-prompt
  difference is only believed when it repeats.
- A missing or failed sample makes a candidate *unknown*, not slow.
- A lever that only shows a win through a second, unexplained side effect is not adopted.
