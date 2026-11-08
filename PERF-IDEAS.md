# Performance: where the time goes, and what to do about it

Measured on the build box (Intel Arc Pro B70 32 GB, Ryzen 7 7700X, Coder IQ1_M, 22.12 GiB expert cache, one engine load)
on 2026-11-02. Every number below came from a run, not an estimate. Instruments: `~/bin/strata-tps-probe.py`,
`~/bin/strata-ttft-probe.py`, `STRATA_CAL_TIMING=1 tools/calibrate.py <cfg>`.

---

## 1. Calibration: the split of 394 s

```
  0.6 →  94.7 s   engine load (weights + 30 GB experts into RAM + 22.12 GiB cache fill)   93 s   24 %
 94.7 → 118.8 s   PCIe-share sweep, 8 samples                                            24 s    6 %   (all within 0.17 %)
118.8 → 167.0 s   draft-floor sweep, 3 values × 3 rounds = 9 samples                    48 s   12 %
167.0 → 215.4 s   confirmation, 6 samples (default vs winner)                           48 s   12 %
215.4 → 241.3 s   the thrown-away warm sample (512 tokens)                              26 s    7 %
241.3 → 338.0 s   expert tier: 3 arms + the second pass in reverse order                97 s   25 %
338.0 → 394.0 s   last arm, teardown                                                    56 s   14 %
```

Five cuts, cheapest first. **Two of them are now implemented and measured: 394 s → 276 s** (see section 6). The
table below is the original estimate:

| cut | change | saves here |
|---|---|---|
| **skip the confirmation when the margin is already clear** | the draft floor led by 4.4 % in all three rounds; a 3 %-plus lead in every round needs no 6-sample re-measure | **48 s** |
| **one pass for a tie, not two** | all three tier arms landed 58.19–58.21 tok/s; if pass one spreads < 0.5 %, the default stands and pass two proves nothing | **40 s** |
| **probe the PCIe share's extremes only** | 0.0 and 1.0 first; within 1 % means the knob is inert on this box (it was: 0.17 % across all eight) and the middle six are noise | **18 s** |
| **attach to a running engine** | every knob is now warm; the sweeps do not need their own load. Re-calibration becomes ~3 min instead of 6½, and a running service is never stopped | **93 s** |
| **shorter warm-up sample** | one 512-token sample costs 26 s; 128 tokens has the same effect (it exists to flush the first-answer penalty, not to measure) | **20 s** |

All five together: **394 s → ~175 s**, and with attach mode on a warm server, **~80 s**. The first three are pure
policy changes in `tools/calibrate.py` and need no engine work.

## 2. TTFT: a 0.62 s floor, and where it comes from

Unique prompt per sample (identical prefixes hit the conversation cache and report 14 000 tok/s "prefill", which
measures the cache, not the prompt path):

| prompt tokens | TTFT | implied prompt rate |
|---:|---:|---:|
| ~2 | 0.68 s | — |
| 64 | 0.61 s | — |
| 512 | 0.77 s | 1 900 tok/s |
| 1 573 | 1.51 s | 1 044 tok/s |
| 6 073 | 4.18 s | 1 453 tok/s |

So: **~0.62 s fixed + ~1/1450 per token**. The engine's own accounting for a 329-token request:

```
prompt 329 tokens = 0 reused + 329 read in 780 ms (422.0 tok/s), 4 generated in 90 ms
conversation cache: parked 332 tokens in 74.7 ms; ... reused_kv_bytes=0
KV streaming: 92.22% of 12948 block reads hit VRAM, 4.1 MiB read from RAM
```

Three things stand out, each a candidate:

1. **39 KV block reads per prompt token, 7.8 % of them from RAM.** Raising `--kv-resident` (32 768 → 65 536 cells,
   +1.55 GiB pinned, ~800 of 11 615 expert slots) trades cache for resident KV. Restart class, so it is a
   one-shot A/B, not a sweep.
2. **The conversation cache costs 75–107 ms per request and reused 0 bytes** on unique prompts — 12 % of the TTFT
   floor spent parking work nobody reuses. A prompt-length threshold (park only above N tokens) is the fix; see
   the Infernix threshold idea below.
3. **422 tok/s for a 329-token prompt vs 1 453 tok/s for a 6 073-token one.** The short-prompt path is 3.4× less
   efficient. `--short-read` and `--prefill` chunk are both warm-tunable and **neither has ever been measured**.

## 3. Decode: 35 tok/s, and what is unmeasured

Warm median 35.05 tok/s on a 128-token thinking answer (the calibrator's own shape reads 58.9). Draft acceptance
swings hard by prompt: 59/75 (79 %) on one, 1/6 (17 %) on another.

Warm-tunable settings the calibrator never touches: `suffix_draft`, `mtp_max_t`, `short_read`, `prompt_cache`,
`prompt_cache_every`, `prefill`. Restart-class settings never measured: `--spec` depth (4), `--kv-resident`,
`--expert-cache`.

---

## 4. What Infernix does that transfers

Read from `~/infernix-src` (CUDA engine, RTX 5090 measurements). Its kernel work does not port to SYCL, but three
of its *policies* do — and it documents the negative results too.

### 4.1 N-gram copy drafting: up to 63 drafts, batched 31, minimum match 12

`docs/ngram.md`, measured 2026-10-22 on a 5090, NVFP4 27B, copy workload = 24 source files continued greedily):

| copy drafts | copies | code |
|---|---:|---:|
| none | 612.0 tok/s | 329.5 tok/s |
| 31 | 1 088.3 (+78 %) | 319.6 (−3 %) |
| 63 | **1 304.2 (+113 %)** | 333.9 (+1 %) |

and the cost they measured: *"a 63-draft copy round makes every row verify 64 columns, which cost the code
workload 6.3 % at two requests against 1.6 % with 31 drafts"*. Minimum match 8 and 16 moved copies +4.1 % and
−3.5 %, so 12 stayed.

**Mapping to Strata:** `suffix_draft` is exactly this knob (prompt-lookup draft depth, 0–64, warm-tunable) and the
calibrator has never measured it. Infernix's numbers say the answer is *workload-dependent* — a big win on
copy-heavy prompts, a small loss on ordinary prose when the window is too deep. That is a two-stratum measurement,
which is precisely what `serve/autotune.py` already stratifies for.

### 4.2 A measured threshold instead of one kernel for all lengths

`RESEARCH_NOTES.md`: the prompt kernel is switched at **768 visible keys** — equal below it, 1.6× faster from 1024,
3.4× at 8192, but 1.6× *slower* for short launches over an empty context. The threshold was swept, and 768 was
"within 0.03 % of the per-shape best"; the old 2048 "costs 2.3–2.7 % of the total and up to 64 % in its worst cell".

**Mapping to Strata:** the same shape of decision, one level up — *park in the conversation cache only above N
prompt tokens*, and *use the short-read path below N*. Both are policy, both are measurable, and the 0.62 s TTFT
floor is where the win is.

### 4.3 Resolve `auto` at startup, then report it

`--ngram-draft-tokens auto` picks the measured-fastest window at startup, logs it, and `Engine::options()` and the
request log carry the resolved value. Strata already has the reporting half (`strata` block, `GET /v1/tuning`);
the gap is that `auto` values are resolved invisibly to the calibrator's report.

---

## 5. Ranked experiments (each one a run, not a rewrite)

| # | experiment | cost | what it answers |
|---|---|---|---|
| 1 | `suffix_draft` ∈ {0, 15, 31, 63} on a copy workload **and** a code workload | 2 × 4 samples warm ≈ 60 s | does Strata get Infernix's +113 % on copy, and what does it cost on code? |
| 2 | `--kv-resident` 32 768 vs 65 536, 6 000-token prompts | 2 loads ≈ 4 min | TTFT and long-context decode against expert-cache slots |
| 3 | park-threshold: conversation cache on/off at 200 / 2 000 tokens | warm, ~40 s | how much of the 0.62 s floor is parking nobody reuses |
| 4 | `--short-read` ∈ {0, 512, 2048} at 300 and 3 000 tokens | warm, ~60 s | the 422 tok/s short-prompt prefill |
| 5 | `--spec` 4 vs 6 vs 2 | 3 loads ≈ 6 min | draft acceptance swings 17–79 %; is depth 4 right for code? |

Experiments 1, 3 and 4 need no engine restart and no source change — they are `POST /v1/tune` (or `strata_tune` on
a request) plus the two probes in `~/bin`. That is the payoff of the warm path: most of this list is now an
afternoon of measurements, not a rebuild per question.


---

## 6. Results (2026-11-02, same box)

### The prefill stall was the model living on a spinning disk — fixed

A 2,655-token **code** prompt killed the engine twice, with its own log naming the cause:

```
threads waiting on the disk (state D): 64 of 91 (blk_io_schedule x60) - the engine is waiting for the drive
strata serve: no progress for 60 s during a request (reading the prompt (batched): layer 1 … )  [issue #29]
```

The shards were symlinks onto `/dev/sda` (12.7 TB, ROTA=1) and the PLE n-gram table is read with `--ple-io direct`
(unbuffered). Natural-language prompts of 6,073 tokens passed in 4 s because repeated tokens hit the row cache —
which is why this stayed invisible.

Done: 7.0 GB of unused weights moved to the big drive (verified, symlinked), then the **two shards (55 GB) copied to
the NVMe** (`~/strata-nvme/IQ1_M`, size + head/tail checksums verified; the HDD copy kept as the cold backup) and the
`~/strata-models/IQ1_M/*.gguf` symlinks repointed. Same prompt now: **3.0 s wall, first token 2.2 s, 64 tokens** —
from "killed the engine" to a normal request.

| | before | after |
|---|---|---|
| 2,655-token code prompt | >60 s → watchdog kill | **3.0 s, TTFT 2.2 s** |
| decode, code-continuation, warm | — | 85.7–85.8 tok/s |
| NVMe free | 84 GB | 36 GB (55 GB of shards added, 7 GB freed) |

### Experiment 1: `--suffix-draft` is inert here

Four arms, one engine load each, same two workloads, warm median of 2:

| `--suffix-draft` | copy tok/s | code tok/s | copy TTFT |
|---:|---:|---:|---:|
| 0 | 71.56 | 85.68 | 0.21 s |
| 15 | 72.74 | 85.71 | 0.20 s |
| 31 | 72.42 | 85.84 | 0.20 s |
| 63 | 71.72 | 85.84 | 0.18 s |

Copy within 1.6 % across all four arms (noise), code within 0.2 %. **No Infernix-style +113 %, and no verification
cost either.** So the flag is not the lever Infernix's n-gram copies are: its win comes from a drafter woven into its
speculation chain, and this model's MTP head may already cover what a suffix window would propose. Recorded as a
measured negative, which is worth as much as a win — it removes a candidate from the tuning set.

Side observation: the same engine that measured 35 tok/s on a prose prompt reads **85.7 tok/s** on code continuation,
with 79 % draft acceptance. Decode rate on this box is a property of the prompt, not of the engine.


---

## 7. Three calibration modes, and running the fine one against a live box

The cuts in section 6 are fast but they *skip* measurements, so they need a mode that never does. There are now
three, selected by flag, config key or environment variable:

| mode | how | engine loads | what it measures | wall (this box) |
|---|---|---|---|---|
| **fast** (default) | — | 1 | the sweeps, and a confirmation only when the sweep did not already settle it | **276 s (measured)** |
| **fine** | `--fine`, `"calibrate_fine": true`, `STRATA_CAL_FINE=1` | 1 | **every** confirmation and the tier's second pass, always | ~394 s |
| **stock** | `"calibrate_stock": true`, `STRATA_CAL_STOCK=1` | 6–10 | the pre-TUNE path: a restart per candidate | 20–30 min |

`fine` is the honest middle: the same one-load warm path, but nothing is skipped. It is what to run when a box's
numbers will be quoted or when a fast run chose a winner inside 3 % of the default. `stock` exists to reproduce what
the tool did before any of this work — useful when a question is "did the warm path change the answer?", which a
warm run cannot answer about itself.

### Can fine mode run against a live, idle server?

Yes in principle, and the warm path is what makes it possible: every knob `fine` measures is changed by a request
field or a `POST /v1/tune`, so a running engine can be measured without being stopped or reloaded. What is missing is
an engine object that speaks HTTP instead of a pipe.

The adapter is small — the calibrator only asks four things of an engine:

| what the calibrator calls | what a remote engine would do |
|---|---|
| `generate(ids, max_new, sampling)` | `POST /v1/chat/completions` with `strata_tune` from `sampling`, `max_tokens=max_new`, `stream: true`, counting streamed tokens |
| `tune(dict)` | `POST /v1/tune` — the reply's `applied`/`refused` is the acknowledgement the pipe gives |
| `info` | `GET /props` + the engine's start line (`pcie_frac`, `spec_min_p`, `pool_workers`, `prefill_stages`) |
| `child_env` / launch | not needed: nothing is started |

Four guards would have to come with it, and none is optional:

1. **Idle, proven rather than assumed.** `GET /v1/tuning` reports `seen` (requests the tuner has observed); the
   adapter records it before a sweep and refuses to start if it moves during one. A live box that starts serving a
   user mid-sweep would otherwise be measured through somebody else's prompt.
2. **Restore, always.** The server's defaults are read at the start and written back in a `finally`, exactly as the
   warm path already leaves the tier as it found it. A calibration that ends with a live server on a candidate nobody
   adopted is a regression shipped by a measurement tool.
3. **A budget, not an opinion.** The sweeps cost ~5 minutes of a live engine's capacity. That is acceptable on an idle
   desktop box and not acceptable on one serving a phone; the adapter should take a deadline and abandon cleanly.
4. **No silent partial answer.** If any sample is lost to traffic, that candidate is *unknown*, not *slow* — the
   existing rule that a mismatch yields no pair, applied to a sweep.

The prize is real: on a box that serves all day, a fine calibration could run nightly and never drop a request. The
reason to hold off is that guards 1 and 4 need the request-count telemetry to be exact, and today `seen` counts only
what the auto-tuner has observed since start, not every request the server served.


---

## 8. The drift check: when to pay for fine, without guessing

`scripts/strata-drift-check.py` answers the only question that should trigger a fine run — *has this box moved away
from the config it was tuned for?* It measures decode rate and TTFT on a fixed prompt, compares them with a stored
baseline per model+hardware, and exits 0 (same), 2 (not measurable) or 3 (drifted, with the command to fix it).

    ~/bin/strata-drift-check.py                # measure, compare, exit 0/2/3
    ~/bin/strata-drift-check.py --record       # make today's numbers the baseline
    ~/bin/strata-drift-check.py --run-fine     # drifted? run the fine calibration itself

Two guards make the number trustworthy, and both are the new request counter's work:

- **it refuses a busy box.** `GET /v1/tuning` must report `busy == 0`, and the script knows how many requests it
  issued itself — any *other* arrival during the measurement voids it (a sample taken through somebody else's prompt
  is not a sample).  This caught its own first bug on the first run: it counted its own traffic as interference.
- **recording a baseline is not "fixing" drift.**  Rewriting the baseline after a 16 % drop hides a regression.
  `--record` is a deliberate act, and `--run-fine` re-measures every setting before it records anything.

Measured on this box: baseline recorded at **58.1 tok/s / 0.19 s TTFT**; a repeat read 58.8 (+1.2 %) -> `same`; a
baseline doctored to 70 tok/s read 58.48 (**-16.5 %**) -> `DRIFTED`, exit 3.
