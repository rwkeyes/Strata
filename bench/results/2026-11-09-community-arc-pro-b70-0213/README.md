# Dirigo 0.2.13 port vs stock 0.2.13 — same battery, Arc Pro B70

**Date:** 2026-11-09 · **Operator:** rwkeyes (Dirigo Agents LLC) · **Report kind:** community benchmark, fork-vs-stock

This is not a stock benchmark run: it measures **our Dirigo port of 0.2.13 against the tagged 0.2.13 tree**, using the
same tests on both. The point is to show (a) that carrying the fork forward onto 0.2.13 did not cost anything on the
engine, and (b) what the fork's `serve/` carry is actually worth on this hardware.

If you are looking for the standard-protocol number on a stock 0.2.1x tree, see
`../2026-11-08-standard-arc-pro-b70-ae1a5657/`.

## Hardware and software

| | |
|---|---|
| GPU | Intel Arc Pro B70 32 GB (Battlemage G31, `8086:e223`), AOT target `bmg-g31` |
| Display | AMD Raphael iGPU — the Arc is compute-only |
| CPU | AMD Ryzen 7 7700X (8 cores) |
| Kernel | 7.0.0-42-generic |
| Toolkit | oneAPI compiler 2026.2 |
| Model | `qwen3.8-flash-next-coder-iq1_m` (IQ1_M + mine expert pack) |
| Engine | **0.1.59** on both sides, ggml pin `3cf0325` |

Both trees were built with the same script, same flags and the same pinned ggml:

| tree | ref | commits over tag | engine binary |
|---|---|---|---|
| dirigo | `release/0.2.13` @ `3512b4cce511` | 2 (38 files, +5,062/−264) | 99,889,376 B (`a44a41938f2aeda0…`) |
| stock | `v0.2.13` @ `fb57f7472c2a` | — | 99,897,992 B (`7b5439980466a569…`) |

Only one engine ran at a time, with the card to itself; every window verified 12,288 of 12,288 slots pre-filled and
0 experts over PCIe. Full details in `provenance.json`.

## 1. Unit suites

| | dirigo | stock |
|---|---|---|
| tests | **1,537** | 1,512 |
| result | **`OK (skipped=17)`** | `FAILED (failures=1, skipped=17)` |
| failure | — | `test_hip_ordinal`: `'0,1' != '1,0'` |

Both were run with `python -m unittest discover -s serve -p 'test_*.py'` (logs in `suites/`).

The stock failure is a test that reads the host: when the machine has an integrated Radeon, `HIP_IGPU_ARCHS` makes
`ordered_gpus()` return `0,1` where the test expects `1,0`. It is fixed in the dirigo tree through the existing seam
(stub the topology probes, pin the reorder) — upstream PR
[Niko1221/Strata#5602](https://github.com/Niko1221/Strata/pull/5602). The 25-test gap is the fork's own test files
(`test_spec_window`, `test_thinking_policy`, `test_conv_router`, `test_tune`, …): 12 files, +2,579 lines under `serve/`.

## 2. Behaviour probes

| probe | dirigo | stock |
|---|---|---|
| minefield, 5 traps (capital, decimal, nonempty, count_r, reverse) | **5/5** | 5/5 |
| 24-token extraction — "Invoice INV-7 dated 9 June 2024 → ISO date" | **`2024-06-09`**, 0 reasoning chars | **`''`**, `finish_reason: length` |
| `GET /v1/tuning` | 200 | 404 |
| `POST /v1/tune {"spec_min_p": 0.5}` | 200 `{"applied": {"spec_min_p": 0.5}, "refused": []}` | 404 |
| needle at 7,855 tokens, marker `ZQ-7731-KX` | 2/2 correct | 2/2 correct |
| 165,779-token request | 400, `request exceeds the context window … requests are never truncated` | 400, identical wording |
| code leg (list reversal) | complete, closing fence, `finish: stop` | complete, closing fence, `finish: stop` |

The extraction row is the one with user-visible weight: given 24 tokens, stock spends all of them on reasoning and
returns nothing, while the fork's short-answer policy answers directly. Same engine, same weights, same request.

## 3. Prefill / decode battery

Medians of two interleaved passes per arm (order dirigo, stock, stock, dirigo) so drift lands on both:

| leg | prompt tokens | dirigo prefill / decode (tok/s) | stock prefill / decode | Δ prefill | Δ decode |
|---|---|---|---|---|---|
| warmup | 15 | 76.0 / 25.4 | 75.8 / 25.2 | +0.3 % | +0.8 % |
| code | 32 | 59.6 / 114.8 | 60.1 / 114.7 | −0.8 % | +0.1 % |
| fresh | 4,210 | 1332.8 / 81.2 | 1333.6 / 81.0 | −0.1 % | +0.2 % |
| fresh | 8,830 | 1411.9 / 85.5 | 1409.2 / 85.4 | +0.2 % | +0.1 % |
| follow-up | 4,451 | 326.9 / 75.8 | 327.1 / 75.7 | −0.1 % | +0.1 % |
| follow-up | 9,071 | 274.4 / 85.9 | 275.2 / 85.7 | −0.3 % | +0.2 % |
| needle | 7,855 | 1435.6 / 97.5 | 1438.0 / 97.3 | −0.2 % | +0.2 % |
| long | 30,178 | 1819.4 / 48.8 | 1818.0 / 48.2 | +0.1 % | +1.2 % |

Engine throughput is the same on both sides to within 1 %. That is the expected result — the carry's engine change is
the `TUNE` control line, which is not on the measured path — and it is the reason this report can claim the fork costs
nothing to run.

## 4. GuideLLM

GuideLLM 0.8.1, `synthetic_text` 512 in / 128 out, concurrent profile `streams 1,2,4,8`, 45 s per point, dirigo then
stock in the same session:

| streams | TTFT dirigo | TTFT stock | Δ | tok/s dirigo | tok/s stock | Δ | req/s dirigo / stock | errored |
|---|---|---|---|---|---|---|---|---|
| 1 | 739 ms | 949 ms | **+28.4 %** | 62.6 | 59.7 | −4.5 % | 0.49 / 0.47 | 22 / 21 |
| 2 | 2,622 | 3,163 | **+20.7 %** | 62.6 | 56.9 | −9.1 % | 0.49 / 0.44 | 22 / 20 |
| 4 | 6,688 | 7,500 | **+12.1 %** | 62.6 | 54.0 | −13.6 % | 0.49 / 0.42 | 22 / 19 |
| 8 | 9,641 | 9,658 | +0.2 % | 65.4 | 59.7 | −8.7 % | 0.51 / 0.47 | 23 / 21 |

Δ is stock relative to dirigo. The gap at low concurrency is the fork's cache routing: a single-shot request runs with
the conversation prompt cache off (no pool work before the first token), a repeat or a multi-turn chat runs with it on.
Stock has one setting; the pool is a load-time decision there.

Caveat carried honestly: this is one pass per arm in one session. The companion 0.2.12-vs-0.2.13 measurement showed
2–4 % positional drift in the same rig, so treat +12…28 % as "clearly ahead", not as a precise figure. `errored` counts
requests that hit the 45 s cap mid-stream, and is similar on both arms.

## 5. Determinism (why the fork did not move to 0.2.13's headline fix)

v0.2.13's notes advertise that Intel Arc now returns the same answer for the same prompt (their bf16 prompt products
moved off oneMKL onto a fixed-order kernel: "50 of 50 identical on an A750, against 3–5 different in 5 runs before").
On this card that was **already true on 0.2.12**: with greedy decoding, three prompts repeated 10× and a 4,200-token
prompt repeated 6× gave **1 distinct answer each** on both engines (`determinism/`). So the release's headline Arc win
is not reproducible here, and the report does not claim it.

## Reproducing

`scripts/` holds the three harnesses used, unmodified apart from path placeholders:

- `strata-0213-ab.sh` — the four-window battery (`bench_prefill` + longprobe per window, order d s s d)
- `strata-guidellm-0213-ab.sh` — the GuideLLM two-arm run
- `strata-determinism-probe.py` — the repeated-greedy-prompt probe

Paths that were absolute on the operator's box appear as `<data>` and `<home>` in the copies here. The engine logs and
configs are not shipped: a config file names the pack and the launch flags, and the pack is a build artifact.

## What this does and does not show

Shows: the fork's 0.2.13 tree builds and passes its suite where the stock tree has one host-dependent failure; at
identical engine throughput it answers short requests that stock truncates, exposes two runtime levers stock does not
have, and is 12–28 % faster to first token at low concurrency on this card.

Does not show: anything about other GPUs or other engines; a full statistical treatment of the GuideLLM gap (single
pass per arm); or any benefit from 0.2.13's bf16 determinism change, which this hardware did not need.
