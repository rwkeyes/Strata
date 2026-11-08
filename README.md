# Strata_Dirigo — release 0.2.11

Dirigo Agents' fork of [Strata](https://github.com/Niko1221/Strata) (MIT), based on upstream tag **`v0.2.11`**
(engine **0.1.57**). Source only — no engine binaries, no model weights. Upstream's own README (build, hardware,
setup) is preserved at [`docs/UPSTREAM-README.md`](docs/UPSTREAM-README.md).

## How it differs from stock v0.2.11

The engine, kernels and model format are upstream's. The fork adds server-level behaviour and one opt-in engine
knob; with none of them enabled, the server behaves as stock.

| difference | what it does | control |
| --- | --- | --- |
| conversation cache routed by traffic shape | a run of single-shot prompts switches the cache **off** (faster prefill); a conversation switches it back **on** (prefix reuse) | `serve/conv_router.py`, `--conv-route off\|shadow\|on` (default `shadow` = classify and log only) |
| thinking policy | suppresses the scratchpad for short/factual asks, keeps it for anything that computes or orders | `serve/prompt_kind.py` + the compute/ordering guard |
| retune between requests | change engine settings on a live server — no restart, no reload | `POST /v1/tune`, `GET /v1/tuning`, `--auto-tune off\|shadow\|on` |
| single-load calibration | one engine load instead of six for FAST / FINE / STOCK | `tools/calibrate.py` |
| per-request draft depth | a client can set its own speculation depth for one request | `spec=N`, inert unless sent |
| build fixes carried | HIP `alignas` fix (upstream #778); oneMKL link narrowed to the BLAS domain | in the tree |

## GuideLLM — each test, both arms

Same engine version built from the same tag in both arms. **GuideLLM 0.8.1**, Intel Arc Pro B70 32 GB, model
`qwen3.8-flash-next-coder-iq1_m`, synthetic 512-in / 128-out chat requests, streaming (TTFT measured, not
inferred), `temperature 0`, 45 s per concurrency level, both arms in one session on the same card. Both arms
fully resident — 12,288 expert slots, **0 experts over PCIe**. **0 errors** at every level in both arms.

| concurrency | TTFT median | ITL median | requests/s | output tok/s |
| --- | --- | --- | --- | --- |
| 1 — stock | 827 ms | 10.4 ms | 0.4 | 56.6 |
| 1 — this fork | **729 ms** (−11.8 %) | **8.9 ms** | **0.5** | **62.6** (+11 %) |
| 2 — stock | 2,831 ms | 9.4 ms | 0.4 | 56.0 |
| 2 — this fork | **2,570 ms** (−9.2 %) | **9.2 ms** | **0.5** | **65.4** (+17 %) |
| 4 — stock | 7,279 ms | 10.5 ms | 0.4 | 54.0 |
| 4 — this fork | **6,044 ms** (−17.0 %) | **8.8 ms** | **0.5** | **65.6** (+21 %) |
| 8 — stock | 9,872 ms | 9.9 ms | 0.4 | 54.4 |
| 8 — this fork | **8,932 ms** (−9.5 %) | 10.8 ms | **0.5** | **59.7** (+10 %) |

GuideLLM's workload is all unique prompts, so the router settles into cache-off for it. The fork wins every column
except ITL at c=8. Neither arm scales with concurrency — the engine serves one request at a time by design, so c=8
queues. The pp512 figure behind the c=1 gain is 880.9 vs stock 710.9 tok/s **settled**; measured cold, the first
four requests of a burst read 463.8 while the cache is still on.

### What GuideLLM cannot show

Its workload never repeats a context, so the reuse the router buys is invisible to it. Measured separately, same
card, own harness:

| chat-shaped traffic | stock | this fork |
| --- | --- | --- |
| second request on the same context | 5.29 s | **0.29 s** (17×) |
| first request on a context | 827 ms | 943 ms (+117 ms) |
| 12 short extractions, 8-token budget | 0/12 correct | **9/12 correct** |
| 26-prompt accuracy battery | 88.5 % | **92.3 %** |

The 117 ms is paid once per fresh context and returned on the first repeat.

## Licence

MIT — upstream Strata plus the Dirigo changes; see [`LICENSE`](LICENSE).
