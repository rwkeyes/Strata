# Strata 0.2.12 on the B70: what else is there? (2026-11-08)

Measurements on the deployed pair (0.2.12 build, engine 0.1.58, Coder IQ1_M, Arc Pro B70), each in a card window
that stopped the service, started the same tree on :8097 with the deployed config, and restored the unit in one trap.
Instruments: `~/bin/strata-copy-probe.py`, `~/bin/strata-warm-ab.py`, `~/bin/strata-code-quality-ab.py`.
A/Bs alternate the arms request by request (`A B` then `B A`) so drift lands on both.

## 1. Quality: thinking buys nothing on short coding tasks, and costs 2.5x wall

Eight one-function tasks (fib with memo, palindrome, Roman numerals, dedupe, binary search, kv parser, retry
decorator, BFS), each answer executed against asserts. temperature 0, seed 11, thinking toggled per request with
`chat_template_kwargs.enable_thinking`.

| cap | thinking | passed | median wall | median tokens | reasoning chars |
|---:|---|---:|---:|---:|---:|
| 2,000 | on | 14/16 | **2.73 s** | 258 | 614 |
| 2,000 | off | 14/16 | **1.09 s** | 94 | 0 |
| 700 | on | 11/16 | 2.42 s | 228 | 650 |
| 700 | off | 14/16 | 1.09 s | 94 | 0 |

- The one task that fails in **both** arms at both caps is `roman`, and it is a real model error, checked rather than
  assumed: its symbol table uses `"IX"` where `"CM"` belongs and `"IV"` where `"CD"` belongs, so `to_roman(1994)` returns
  `MIXXCIV` and `to_roman(3999)` returns `MMMIXXCIX`. Identical in both thinking arms — thinking did not rescue a
  wrong-substitution mistake either. Excluded from the arm comparison because it carries no signal about the arm.
- The three extra failures at the 700 cap are **truncations**: `finish_reason: length` with 2,680-3,012 reasoning
  characters and the function never completed. Same mechanism as #5039: the scratchpad consumes the answer's budget.
- So on this workload thinking is **neutral on correctness** (14/14 excluding `roman`, both caps) and **2.5x wall**
  (2.73 vs 1.09 s). Under a modest cap it is **worse than neutral**, because it truncates code into something that
  does not compile.
- Scope: eight small self-contained tasks, one model/quant, one box, temperature 0. Multi-step debugging or
  architecture questions are not covered and are where thinking plausibly earns its tokens.

**What follows, in order of confidence:**
1. Nothing to change in the deployed config on this evidence: the box's policy keeps thinking for code, and this test
   is too small for a global flip.
2. A **narrow, measurable rule worth trying**: for a prompt that asks for code to be *returned* (code-only asks) with
   a client budget under ~1,200 tokens and thinking on, turn thinking off server-side — the existing policy's
   classifier already separates code, it is only used to keep thinking today. Needs a traffic-shaped measurement plus
   a correctness gate before it ships.
3. Zero-risk today: a client capping code answers under ~1,200 tokens should send `enable_thinking: false`. That needs
   no engine or server change and removes the truncation class entirely.

## 2. `--pcie-frac`: the probe-derived 0.19 is fine; 0.30 is noise

`pcie_frac` is per-request, so this needed no restart. Three interleaved runs (n=4 per arm):

| run | lane | 0.19 | 0.30 | delta |
|---|---|---:|---:|---:|
| 1 | code 3.9k | 79.2 | 82.0 | +3.4 % |
| 1 | fresh 9k | 83.0 | 84.0 | +1.1 % |
| 2 | code 3.9k | 81.8 | 78.8 | **−3.7 %** |
| 2 | prose | 75.5 | 78.8 | +4.4 % |

The sign flips between runs on the same lane at the same sizes, so the effect is noise inside ±4 %. `0.30 → 0.45`
measured −1.9 %. **No change recommended**: the engine's own PCIe probe (7.0 GB/s → 0.19) is as good as anything
measured here, and this matches the calibrator's earlier note (8 samples within 0.17 %).

## 3. `--spec-min-p` 0.7: the 0.1.55 result did not survive 0.1.58

Interleaved, thinking on (the serving regime), n=4: code 3.9k **83.4 → 77.5 tok/s (−7.0 %)** for 0.7; prose 75.7 →
75.5 (flat). The older "+12.35 % for 0.7" was measured on 0.1.55; on this engine the draft acceptance economics moved
and **0.5, the deployed value, is the better one**. No change.

## 4. Prompt-lookup drafting (the maintainer's ranked experiment #1): untested, and why

`suffix_draft` defaults to **3** (the deployed config does not set it), so lookup drafting is already on. With thinking
off (otherwise every answer is reasoning tokens and the lanes measure nothing):

| lane | lookup as started (3) | lookup=0 | delta |
|---|---:|---:|---:|
| copy 1.2k verbatim | 142.9 | 143.2 | −0.2 % |
| copy renumber 120 lines | 138.6 | 139.4 | −0.6 % |
| code 3.9k | 88.7 | 79.7 | +11.3 % (n=2, not interleaved — see below) |
| prose | 73.2 | 68.5 | +6.9 % (n=2, not interleaved) |

The copy lanes are where lookup should pay and they are **flat** — because this model does not produce long verbatim
copies: `copy_hit` = 20 of 120 source lines reproduced exactly (it rewrites the rest). The +11.3 % code figure above
came from two sequential runs, so it was re-taken as an interleaved A/B (`lookup=1` vs `lookup=0`, thinking as the
service runs it, n=4, order alternated):

| lane | lookup on | lookup off | delta |
|---|---:|---:|---:|
| code 3.9k, 256 cap | **85.2** | 79.4 | **lookup off is −6.9 %** |
| prose | 77.0 | 76.0 | −1.3 % |

So on engine 0.1.58 the prompt-lookup drafter **is** worth about **+7 % on the code lane** — the mechanism is not inert,
it just cannot help a copy-shaped prompt this model refuses to copy verbatim. This also corrects the older
"0/15/31/63 are all equal" reading: that sweep compared *depths*, never on against off, and on an older engine.

Deeper is not better here: a window started with `--suffix-draft 31` gave 82.7 tok/s on the same lane (interleaved
delta only +4.4 %), below the depth-3 absolute. **Depth 3, the engine default, is the right setting on this box.**

**What follows:** nothing to change — but do not switch `lookup` off per request to "save work": on this box it costs
~7 % of code decode.

## Summary

| candidate | verdict | action |
|---|---|---|
| thinking off for short code answers | **quality neutral, 2.5x faster; harmful under a small cap** | measure on real traffic, then a narrow server rule; clients can send `enable_thinking: false` today |
| `--pcie-frac` 0.30 | noise (±4 %, sign flips) | none |
| `--spec-min-p` 0.7 | **−7 % on code** | none — keep 0.5 |
| lookup drafting 31/63 | nothing to match on this traffic | retest only with copy-heavy load |
| queue-time/latency, batch slots, other quants | not measured | out of scope for this box (single client, one slot) |

Nothing here is applied to the deployment: the two config candidates measured worse, and the one win needs a
traffic-shaped measurement first. The box still serves 0.2.12 / engine 0.1.58 with spec 6, spec-min-p 0.5,
kv-resident 32768, conv_route on, thinking policy on.

## 5. Harder coding tasks: thinking still does not earn its tokens (same day, later)

Six harder tasks with executable checks — three multi-step builds (LRU cache with recency, expression evaluator with
parentheses and unary minus, flatten/unflatten round-trip) and three debugging tasks where the prompt carries buggy
code plus the failing assert and the answer must be the fix. Cap 2,000, temperature 0, seed 13, thinking toggled per
request, arms interleaved, 2 reps.

| arm | passed | median wall | median tokens | reasoning chars |
|---|---:|---:|---:|---:|
| thinking on | 9/12 | **17.59 s** | 1,521 | 5,514 |
| thinking off | **10/12** | **1.77 s** | 174 | 0 |

`expr_eval` fails in **both** arms because the task text was mine and ambiguous ("left to right for equal
precedence" — the model read it as no precedence at all, `2+3*4 = 20`); excluding it leaves **9/10 vs 10/10**, and the
one arm-difference (`walk_debug`) went against thinking. Per task: lru_build 2/2 vs 2/2, flatten 2/2 vs 2/2,
lru_debug 2/2 vs 2/2, walk_debug **1/2 vs 2/2**, memo_debug 2/2 vs 2/2.

So on harder work the picture does not change: thinking is ~10x wall time for no correctness gain, and the one
difference favours thinking OFF. It is still not proof that thinking never helps — none of these tasks is the kind
where a scratchpad is decisive (no long derivation, no hidden-state puzzle).

## 6. GuideLLM (the standard tool, 512-in/128-out): 0.2.12 is 12-22 % better on TTFT — and it is OUR lever

GuideLLM 0.8.1, synthetic_text 512 in / 128 out, concurrent profile streams 1/2/4/8, 45 s per point, same card, one
session, arms back to back (0.2.11 tree, then 0.2.12 tree), 0 errors at every point.

| concurrency | TTFT 0.2.11 → 0.2.12 | output tok/s 0.2.11 → 0.2.12 |
|---|---|---|
| 1 | 938 → **727 ms (−22.5 %)** | 59.7 → 62.6 (+4.8 %) |
| 2 | 3 016 → **2 640 ms (−12.4 %)** | 56.9 → 62.6 (+10.0 %) |
| 4 | 7 278 → **6 114 ms (−16.0 %)** | 59.7 → 68.3 (+14.3 %) |
| 8 | 9 807 → 9 758 ms (−0.5 %) | 56.9 → 59.7 (+5.0 %) |

**Attribution — the tag itself buys nothing; the conversation-cache routing buys all of it.** The same 0.2.12 build
with `conv_route: shadow` (labels and logs, never retunes) lands exactly on 0.2.11: 934 / 3 004 / 7 583 / 9 807 ms TTFT,
56.9-59.7 tok/s.

| concurrency | shadow | on (deployed) | on vs shadow |
|---|---:|---:|---:|
| 1 | 934 | 727 | **−22.2 % TTFT** |
| 2 | 3 004 | 2 640 | −12.1 % |
| 4 | 7 583 | 6 114 | −19.4 % (tok/s +20.0 %) |
| 8 | 9 807 | 9 758 | −0.5 % |

So the deployed `--conv-route on` is worth **12-22 % TTFT and 5-20 % output throughput** on a synchronous
single-shot sweep — measured by the standard tool, not our own probe — while upstream 0.2.12 with the router shadowed
is indistinguishable from 0.2.11. That is the strongest evidence yet for the lever the upstream PR #5516 carries.

## What is worth further testing now

1. **`conv_route`'s threshold** (`conv_route_batch_after`, 4): the sweep above shows the router firing on every run,
   so 2 vs 4 vs 6 is the natural next measurement — a config key, one restart per arm. And a *conversational* sweep
   (repeated multi-turn prompts) to confirm the switch-back does not cost the reuse case under load.
2. **thinking off for small-budget code asks** — still the open quality item; harder tasks did not rescue thinking
   (9/10 vs 10/10, and 10x the wall time).
3. Nothing else from today's list: `pcie_frac`, `spec_min_p 0.7`, deeper suffix windows all measured worse or noise.

Files: `~/strata-perf/results2/guidellm-0212.json` (first sweep), `~/strata-perf/results-ab/guidellm-ab2-{v0211,v0212,v0212-shadow}.json`
(with `-summary.json`), harness `~/bin/strata-guidellm-0211-vs-0212.sh`, harder-task harness
`~/bin/strata-code-hard-ab.py`.
