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
| `--prompt-cache 0` as a deployment profile | equal to the router's cache-off path at matched sizes, and every conversation turn re-prefills: `C2` 2.482 s, `C3` 2.390 s against 0.587 / 0.549 — §8 |
| `conv-route-batch-after` 1 | wins 0.52 s over the first six single-shot requests of a burst, loses **1.88 s** on the conversation turn that follows it — §7 | 
| `--spec 0` to remove drafting entirely | the engine refuses it on a native IQ pack: *"it needs `--native` SHARD1, `--spec T (T >= 2)` and `--prefill` CHUNK"* — so drafting cannot be excluded this way |

## 3. Where the remaining time is

Fitted over the 18 fresh legs of the 0.2.13 battery: **prefill ≈ 336 ms fixed + 0.67 ms/token** (1,492 tok/s
marginal). A 113-token prompt costs 409 ms, of which ~330 ms is not tokens. Decode runs 48–115 tok/s depending on
the lane. The engine is within 1.1 % of stock on all eight legs, so what is left is either upstream's or in that
fixed term.

## 4. Not tried, ranked

| # | idea | evidence it could pay | cost | first test |
| --- | --- | --- | --- | --- |
| 1 | ~~defer the slot refill past the first token~~ | **closed, measured, refuted (§9)**: the flag shipped in a probe binary and TTFT went 398 → 2,945 ms while decode fell from 92 to **0.4 tok/s**. The borrowed slots are the decode working set, so the refill is a prefetch for the tokens about to be generated, not a tax | — | done |
| 2 | **make the refill cheaper, not later** | the refill is right to exist but 0.319 ms/slot is a PCIe copy of experts decode will touch; only the ones the first N decode positions actually route are worth pre-loading. Shrink the set, keep the timing | engine change, days | the same ladder with a reduced refill set, decode watched |
| 3 | ~~ship `conv-route-batch-after` 1–2~~ | **closed, keep 4.** §7: 1 buys 0.52 s across the first six single-shot requests and costs **1.88 s** on the next conversation turn (`C2` 2.465 s against 0.587) — break-even at ~21 single-shots per conversation re-open | — | done; the config key that would have set it was also dead (PR #16) |
| 4 | ~~engine-side per-request timer~~ | **done, no build**: `STRATA_TRACE` already splits the prompt path — §9 names 109 ms of read-side fixed work, 72 ms of refill and 56 ms of bookkeeping | — | done |
| 5 | ~~a one-shot mode that also drops the cache slots~~ | **closed, §8**: no saving over the router's cache-off path at matched sizes (+2 ms at 456 tokens, +8 ms at 1,608), and a 0-slot start severs conversation reuse for good — every turn re-prefilled | — | done |
| 6 | residency-biased router on SYCL | `route_prior.cu` is CUDA-only; here **12,288 of 24,576** experts are resident, so half of every token's experts cross PCIe. Upstream measured +15 % / +37 % on a 12 GB NVIDIA card | engine port, days | decode lanes, 5 interleaved pairs |
| 7 | prefill chunk at 4k/8k/12k with intervals | 16k/32k were tested, but 8,192 was never shown to be the knee | ~20 min | paired, three prompt sizes, per-prompt medians |
| 8 | CPU pool workers and affinity on the 7700X | 15 workers is a 16-thread default; p-core pinning was **+27 %** decode on the 13700K | 15 min | workers 8/12/15/16, `--pool-affinity` on/off |
| 9 | dispatch overhead on SYCL | the Vulkan analysis found ~88 % of per-dispatch cost is fixed; nobody has counted dispatches per token on SYCL | a day | `unitrace` kernel count per token, then fuse the five most frequent small ops |
| 10 | DFlash2 block drafter on SYCL | upstream's headline decode lever; 7 drafts per pass | weeks | only after 1–3 |

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

## 6. The fixed per-request cost, measured 2026-11-14

A ladder of unique prompts (74 … 1,608 real tokens), two arms — the deployed `conv_route: on` and `shadow` (cache
on, like stock) — client TTFT from the first content delta, and the engine's own `prompt … read in X ms` and
`conversation cache: parked … in Y ms` lines for the split:

| arm | TTFT fit | prefill fit (engine) | server (TTFT − prefill) | park |
| --- | --- | --- | --- | --- |
| `conv_route: on` (cache off for one-shot) | **243 ms + 0.77 ms/token** | **222 ms + 0.77 ms/token** | 21–32 ms | ~100 ms on the first 3 requests only, then none |
| `shadow` (cache on, like stock) | **455 ms + 0.79 ms/token** | **434 ms + 0.79 ms/token** | 21–24 ms | ~101 ms on every request |

1. **Nothing hides outside the engine's own accounting.** TTFT is prefill plus ~21 ms of server, in both modes; the
   unaccounted term that earlier notes called "450–500 ms" is not overhead, it is prefill.
2. **The cache-on state costs +212 ms per request** (intercept 434 against 222) — the prize the router takes. At
   `batch_after 4` a run's first three fresh requests still pay it: 484 / 485 / 402 ms at 74 / 74 / 75 tokens against
   290 ms once the cache is off.
3. **Parking is ~101 ms and happens after the answer**, in both modes; it is not part of TTFT, and with the cache off
   it stops entirely.
4. **Retracted the same day — the cache slots cost nothing when unused.** A first comparison put `--prompt-cache 0` at
   324 ms against 382 ms on the router's cache-off path at 75 tokens, read as a 58 ms slot cost. A matched-size rerun
   (§8) shows that was an artefact: the 382 ms arm's median included its first samples while the cache was still on
   (the hysteresis), so it measured the cache-on state, not the slots. At sizes both arms serve cache-off, the 0-slot
   profile is equal — 456 tokens: 539.7 against 537.3; 1,608 tokens: 1,471.3 against 1,463.3.
5. **The draft layer could not be switched off to test its share**: `--spec 0` is refused on a native IQ pack, which
   needs `--spec T (T >= 2)`.
6. **The ~165 ms is now split, without a build** — the engine already prints a per-request trace under `STRATA_TRACE`:
   ~109 ms of fixed work inside the prompt read, ~72 ms of expert-slot refill, ~56 ms of cache bookkeeping. See §9.

Artifacts: [`0.2.13-port/intercept/`](../dirigo-results/0.2.13-port/intercept/) — both ladders, the three
cause-sweep arms, `summary.json`, the probe and both drivers, and the logs with every restore line.

## 7. Mixed traffic: why `batch_after` stays at 4

One traffic shape, two card windows, the same build and model, differing only in the server's
`--conv-route-batch-after` (4, the deployed value, against 1). Each window: a repeated ~3,000-token prompt, a
two-turn conversation, six unique single-shot prompts, then the conversation's next turn and the repeat again. TTFT in
seconds:

| step | what it is | `batch_after 4` | `batch_after 1` |
| --- | --- | --- | --- |
| R1 | the 3k prompt, first time | 2.263 | 2.134 |
| C1 | conversation, turn 1 | 2.212 | 1.973 |
| **C2** | **conversation, turn 2 — should reuse** | **0.587** | **2.465** |
| S1 | single-shot 1 | 0.564 | 0.517 |
| S2 | single-shot 2 | 0.498 | 0.328 |
| S3 | single-shot 3 | 0.503 | 0.325 |
| S4 | single-shot 4 | 0.454 | 0.327 |
| S5–S6 | single-shot 5–6 | 0.328 / 0.328 | 0.327 / 0.327 |
| C3 | conversation, turn 3 | 0.549 | 0.544 |
| R2 | the 3k prompt again | 2.330 | 2.316 |

1. **`batch_after 1` wins 0.52 s across six single-shot requests** — 0.17 s, 0.18 s and 0.13 s on the second, third
   and fourth, then nothing, because the cache is off either way by the fifth.
2. **It loses 1.88 s on the conversation's second turn.** Served with the cache off, the first turn parks nothing, so
   the second turn re-reads its whole context: 2.465 s against 0.587 s.
3. **Break-even is ~21 single-shot requests per conversation re-open** (1.88 s ÷ 0.087 s saved per request). A one-shot
   extraction queue clears that easily; anything that alternates a tool call with a chat turn never does.
4. **The flip costs the request right after it, and nothing else** — `C3` reuses in both arms (0.549 / 0.544), and the
   repeat `R2` re-reads in both (2.330 / 2.316), because another conversation's context displaced it either way.
5. **The value was never actually settable, until it was fixed.** Both configs produced identical results on all
   eleven steps until the flag was passed by hand: `serve/server.py` read `--conv-route-batch-after` and ignored the
   config's `conv_route_batch_after`, while the launcher writes the key and passes no flag — so every deployment ran 4
   whatever the config said. **Fixed and merged**: `b906f0b5`
   ([PR #16](https://github.com/rwkeyes/Strata/pull/16)), three files under `serve/`, resolution order flag → config → 4,
   with a test that pins all four cases. It is behaviour-neutral for the deployment (its config says 4, and 4 is what it
   resolves to) and it is the only code change on this branch after the snapshot — `DIRIGO.md` records that.

Decision: **4 stands.** 1 is only right for a pure single-shot queue, and this fork is not sold as one.

Artifacts: [`0.2.13-port/intercept/mixed/`](../dirigo-results/0.2.13-port/intercept/mixed/) — both arms, the two inert
runs that exposed the dead key, the probe and the driver.

## 8. The 0-slot profile: no saving, and no chat

Two windows on the build: the deployed config, and the same config with `--prompt-cache 0` in the engine arguments.
Each ran the ladder at 126 / 456 / 1,608 real tokens (three samples each, so the first three requests are still inside
the router's cache-on hysteresis) and then the mixed traffic shape.

| real tokens | deployed TTFT | 0 slots TTFT | difference |
| --- | --- | --- | --- |
| 126 | 546.1 ms (cache on, 102 ms park) | **350.0 ms** | −196.1 ms |
| 456 | 537.3 ms (cache off) | 539.7 ms | +2.4 ms |
| 1,608 | 1,463.3 ms (cache off) | 1,471.3 ms | +8.0 ms |

1. **The 58 ms from §6 does not exist.** Where both arms serve with the cache off, the times are equal within noise;
   the 196 ms at 126 tokens is the cache-on state (§6's own +212 ms intercept and ~102 ms park) that the reference arm
   had not yet switched out of.
2. **0 slots severs conversation reuse permanently.** The router's cache-on value comes from the start-up arguments, so
   a server started at 0 has nothing to switch back to: `C2` 2.482 s, `C3` 2.390 s, `R2` 2.111 s — every turn and repeat
   re-reads its context, against 0.587 / 0.549 / 2.330 on the deployed config.
3. **So there is no profile to offer.** A 0-slot deployment is a one-shot-only server that saves nothing on one-shots.

This closes the last non-engine lead. What is left — the ~165 ms inside prefill — needs a phase timer in the request
path, which means a build.

Artifacts: [`0.2.13-port/intercept/zerocache/`](../dirigo-results/0.2.13-port/intercept/zerocache/) — both ladders, the
mixed shape, the configs, the driver, and the window log with every restore line.

## 9. Where the ~165 ms goes: the expert-slot refill

`STRATA_TRACE=1` makes engine 0.1.59 print the prompt path per request — `prompt start`, `lent N slots`, `prompt chunk`,
`read N tokens in X ms`, `refill start`, `refilled N slots in X ms`, `prompt done`. No build is needed to read it. (The
per-request *admission* trace needs `--batch`, so with `--batch 0` it stays silent; the prompt path is what matters here.)

Ladder with the deployed router, six samples per size, medians:

| real tokens | read | refill | lent slots | refill per slot |
| --- | --- | --- | --- | --- |
| 127 | 252 ms | **71.7 ms** | 225 | 0.319 |
| 457 | 432 ms | **82.6 ms** | 259 | 0.319 |
| 1,609 | 1,245 ms | **188.2 ms** | 589 | 0.320 |

1. **The refill is a fixed per-request cost on the TTFT path.** It runs *after* the prompt read and *before* `prompt
   done`, so the first token waits for it: 72 ms on a short prompt, 188 ms on a 1.6 k one.
2. **It is one PCIe transfer per lent expert slot at 0.319 ms/slot** — the same rate at all three sizes to within 0.3 %.
   The prompt borrows expert slots while it reads; afterwards the engine refills them with the profile's hottest experts.
3. **The read itself carries 109 ms of fixed work** (`109 ms + 0.706 ms/token` over the three sizes): first-layer expert
   reach and chunk setup, plus ~56 ms of cache bookkeeping. 109 + 72 + 56 ≈ the 165 ms §6 could not name, leaving
   nothing unexplained at this resolution.
4. **This reconciles §6 with the dispatch.** Pinning the cache on (`conv_route: shadow`) raises TTFT by 148–234 ms but
   raises `read + refill` by only 7–29 ms: the cache-on cost is checkpoint work inside the engine's prompt span, not in
   the prompt read. Both measurements were right about different parts of the same 200 ms.

**The lever this looked like — and how it was refuted.** It reads like a tax to defer: the slots go back to the
profile's hottest experts, and decode "needs its own experts". Built as `STRATA_REFILL_DEFER=1` (source patch, one
`if`, probe binary, default unchanged) and measured on the same ladder:

| real tokens | refill kept | refill deferred | decode, kept | decode, deferred |
| --- | --- | --- | --- | --- |
| 126 | 398 ms | **2,945 ms** | 92.0 tok/s | 30.9 tok/s |
| 456 | 539 ms | **3,126 ms** | 91.8 tok/s | **0.5 tok/s** |
| 1,608 | 1,468 ms | **3,953 ms** | 74.3 tok/s | **0.4 tok/s** |

The per-sample sequence says why: at 126 tokens the first two requests are normal (0.553, 0.537 s) and the third is
3.069 s — the collapse begins once the cache holds the previous prompt's experts instead of the profile's. **The borrowed
slots are the decode working set**: the refill is a prefetch for the tokens about to be generated, so skipping it moves
the cost from one 72–188 ms transfer per request into a streamed expert fetch per token. 0.32 ms/slot buys 92 tok/s
instead of 0.5.

What survives is a narrower question: not *when* to refill, but *how much* — only the experts the first decode positions
actually route are worth pre-loading. That is item 2 of §4 now.

Artifacts: [`0.2.13-port/intercept/trace/`](../dirigo-results/0.2.13-port/intercept/trace/) — both ladders, the engine
logs with every trace line, the parser and the window driver, and [`trace/q2_0/`](../dirigo-results/0.2.13-port/intercept/trace/q2_0/)
for the second weight set: its ladder, its config, its engine log, and both calibration attempts.
[`trace/refilldefer/`](../dirigo-results/0.2.13-port/intercept/trace/refilldefer/) holds the refutation: both ladders,
both engine logs, the source patch as a diff, the A/B driver and the analysis script.

### Cross-weight check: it ran, and the fix was the profile

The first three attempts failed, and the last failure named the real problem: the config pointed `--expert-profile` at
`data/expert-profile-mine.bin`, which is **48×256** — the profile of the *pruned* coder model (GSQ-RCO Coder keeps 256 of
512 experts). The Q2_0 pack declares 48×512, and the tree already ships `data/expert-profile.bin` at 48×512. Pointed at
that, Q2_0 served without any calibration:

| real tokens | Q2_0 read | Q2_0 refill | lent slots | refill per slot | IQ1_M read | IQ1_M refill |
| --- | --- | --- | --- | --- | --- | --- |
| 128 | 1,075 ms | 75.2 ms | 313 | 0.240 | 252 ms | 71.7 ms |
| 458 | 1,556 ms | 81.6 ms | 365 | 0.224 | 432 ms | 82.6 ms |
| 1,610 | 2,055 ms | 160.9 ms | 726 | 0.222 | 1,245 ms | 188.2 ms |

1. **The mechanism reproduces on different weights.** The refill is there, sits between `read` and `prompt done`, and
   scales with lent slots: 0.22–0.26 ms/slot on Q2_0 against 0.319 on IQ1_M — a different rate, as different expert
   payloads should give, but the same phase and the same ordering.
2. **The absolute numbers are not comparable, and are not used above.** Q2_0 is the **unpruned 512-expert** model: its
   `--expert-cache auto` found 19,208 of 24,576 slots (24.73 GiB) and mirrored the other 6,761 experts in 8.70 GiB of
   pinned host memory, so ~27 % of experts cross PCIe; IQ1_M is the pruned 256-expert variant. Q2_0's dense shard also
   sits on the cold disk (there was no room for 37.6 GB on this NVMe), which inflates its read further.
3. **So §9's four findings stand on two weight sets for the mechanism, and on one for the magnitudes.** The ladder in
   §9 remains the IQ1_M one; nothing above claims Q2_0 is slower or faster than it.

**Calibrating Q2_0 by its own traces is blocked, three ways** (all logs shipped):

| attempt | result |
| --- | --- |
| serve + `--dump-routing` | 0-byte file after 13 prompts — the SYCL device path does not write it |
| serve + `STRATA_USAGE_DUMP` | no file after 12 prompts |
| `tools/collect_profile_traces.py --mode usage` | `--spec needs the device residency table (--expert-profile, --expert-cache and the token graph)`; where it did prefill, 37 tokens took **80.3 s** at 0.5 tok/s (5,364 experts streamed, 77.4 s of it on the host) |

A Q2_0-specific profile therefore needs the serve-mode usage dump to flush (an engine fix) or the one-shot collector to
work on a native pack — not another parameter. Until then Q2_0 runs on the shipped full profile, which is enough for a
mechanism check but not for a ranking study.

## 10. Method rules these numbers were produced under

- Paired or interleaved arms; median of repeats; the box's run-to-run spread stated next to the effect.
- One prompt per configuration is not a measurement: every lane here has several requests, and a single-prompt
  difference is only believed when it repeats.
- A missing or failed sample makes a candidate *unknown*, not slow.
- A lever that only shows a win through a second, unexplained side effect is not adopted.
