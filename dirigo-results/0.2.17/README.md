# 0.2.17 evidence — Dirigo Strata, verified 2026-11-20

Everything in this directory was produced on an Intel Arc Pro B70 32 GB (vega), oneAPI 2026.2, SYCL AOT `bmg-g31`,
model Qwen3.8-Flash-Next Coder IQ1_M, pack `coder-iq1_m`, engine flags
`--spec 6 --spec-min-p 0.5 --kv int8 --kv-resident 32768 --vram-reserve-mib 700 --prompt-cache 6/4096 --batch 0`.

## The three questions this release had to answer

**1. Does the fork still win after upstream reorganised the tree?** Yes. The v0.2.17 tree moved the engine to `engine/`
and the server to `terminal/`; the carry was replayed with a path map (`scripts/strata-carry-replay.py`), 38 files,
+5,066 / −66. Against stock v0.2.17 the fork's first token arrives 24 % sooner at one stream (741 ms against 970 ms,
median of two runs each) and 9–10 % sooner at two and four streams.

**2. Did upstream's engine regress, and did the carry recover it?** Upstream regressed small-prompt prefill badly and the
carry recovers it. Engine-reported prefill of a 613-token prompt, sequential requests, two runs per tree:
ours **770 ms**, stock **992 ms** (−22 %). This is measured straight from the engine's own `read in X ms` line, so it is
not a server effect.

**3. Is the fork at its own previous speed?** Not quite, and the reports say where. Against our 0.2.12 production build
the 0.2.17 fork is ~1.9 % slower on that same prefill measurement (770 against 756 ms), a step that arrived with engine
0.1.60 and never went back. First token at one stream is level (741 ms against 753 ms, inside a 4 % same-arm spread).

## What was measured, and how

| file | what it is |
| --- | --- |
| `guidellm/guidellm-gl0217*.json` | the fork at v0.2.17, GuideLLM 0.8.1, 512-in / 128-out, 45 s per level, two runs |
| `guidellm/guidellm-s0217*.json` | stock v0.2.17, same runs, same session |
| `guidellm/guidellm-gl0212*.json`, `guidellm-gl0215.json` | our previous snapshots in the same session, for the "did it move" question |
| `turnaround/probe-*.json` | the sequential turnaround probe: 12 unique 613-token prompts, per-request engine prefill, dead time (TTFT minus prefill), decode and wall time |
| `suites/suite-fork.txt`, `suites/suite-stock.txt` | `unittest discover terminal/serve` on both trees (2,106 / 2,081 tests) |
| `suites/ctest-fork.txt`, `suites/ctest-stock.txt` | the 97-test kernel-parity battery: fork 78 pass, stock 76 |
| `scripts/` | the probe, the replay, the GuideLLM arm runners and the chart generator |

## The kernel-parity failures, in full

Both trees fail 19 of 97 `ctest` tests, the same 19: upstream's new `native_expert_parity_new_*` battery, which reports
the AVX-2 multi-token `down` kernel disagreeing with ggml by rel 2.9e+33 on `iq4_nl` and its siblings. It is identical in
stock, it is a CPU-side check the SYCL serving path does not use, and every end-to-end probe passes on both trees.
Stock additionally fails `ple_parity` and `qsa_prompt_attn_parity`, which this fork's carry fixes.

## Two defects the suite caught in the replay, both fixed here

- Upstream moved 19 module-level helpers (`ContextOverflow`, `origins_of`, `read_config`, `sampling_defaults_from_config`
  and 15 more) into `serve/server_parts/` and imports them. The replay had also kept the fork's pre-0.2.17 copies, which
  shadowed the imports; `ContextOverflow` then rejected upstream's new `min_room` argument and six tests failed. All 19
  copies removed; the one genuine fork change among them (`spec` and `lookup` as per-request tune keys) was re-applied
  to `server_parts/http_api.py`.
- The conversation-router call is now `getattr(svc, "conv_route_decide", None)`-guarded, because upstream's tests drive
  the request path with a `SimpleNamespace` stub.

## Reproducing

```
scripts/strata-turnaround-run.sh <tree> <label> 12        # engine prefill / dead time / decode, ~2.5 min
scripts/strata-gl-arms.sh <outdir> <label> <tree> <config> # one GuideLLM arm, ~4.5 min
scripts/strata-carry-replay.py                            # replay the carry onto a new upstream tag
python3 scripts/make-chart.py                             # regenerate chart.svg from the JSON here
```
