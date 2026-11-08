# GuideLLM results — Dirigo Strata 0.2.11 (engine 0.1.57)

Measured 2026-11-06 on Intel Arc Pro B70 32 GB (SYCL, AOT `bmg-g31`), model
`qwen3.8-flash-next-coder-iq1_m` (IQ1_M expert pack + MTP head), context 131,072.
Tool: **GuideLLM 0.8.1** — the OpenAI-endpoint benchmark Red Hat maintains for exactly this purpose. Workload:
synthetic 512-in / 128-out chat requests, 40–45 s per concurrency level, streaming so TTFT is measured rather than
inferred, `temperature 0`. Comparator: **stock Strata v0.2.11** (same engine, same tag, its own defaults) on the same
card, same session.

## Results — single-shot traffic (what this workload is)

Strata's deployment routes the prompt/conversation cache by traffic shape: a run of single-shot requests turns it off
(prefill +33 %), chat turns it back on (a repeated context reuses). GuideLLM's synthetic workload is all unique prompts,
so these numbers are the cache-off regime.

| concurrency | TTFT median, stock → ours | ITL median | requests/s | output tok/s |
| --- | --- | --- | --- | --- |
| 1 | 827 → **729 ms** (−11.8 %) | 10.4 → 8.9 ms | 0.4 → **0.5** | 56.6 → **62.6** (+11 %) |
| 2 | 2,831 → **2,570 ms** (−9.2 %) | 9.4 → 9.2 ms | 0.4 → **0.5** | 56.0 → **65.4** (+17 %) |
| 4 | 7,279 → **6,044 ms** (−17.0 %) | 10.5 → 8.8 ms | 0.4 → **0.5** | 54.0 → **65.6** (+21 %) |
| 8 | 9,872 → **8,932 ms** (−9.5 %) | 9.9 → 10.8 ms | 0.4 → **0.5** | 54.4 → **59.7** (+10 %) |

Errors: **0** in every level of both arms.

Prefill and generation, llama-bench-equivalent shapes (median of 3, router settled):

| test | stock | ours | |
| --- | --- | --- | --- |
| pp512 | 710.9 | **880.9** | +23.9 % |
| pp2048 | 1,250.1 | **1,364.0** | +9.1 % |
| pp4096 | 1,535.6 | **1,628.9** | +6.1 % |
| pp8192 | 1,526.8 | **1,629.8** | +6.7 % |
| tg128 | 131.6 | **139.7** | +6.2 % |
| tg512 | 97.4 | **106.5** | +9.3 % |

## Results — chat traffic (cache kept on)

| metric | stock | ours |
| --- | --- | --- |
| TTFT, first request on a context | 827 ms | 943 ms (+117 ms) |
| second request on the same context | 5.29 s | **0.29 s** (17×) |
| 12 short extractions, 8-token budget | 0/12 correct | **9/12 correct** |
| 26-prompt accuracy battery | 88.5 % | **92.3 %** |

## How to read this

- **Throughput does not scale with concurrency** in either build: this engine serves one request at a time by design,
  so c=8 buys nothing and queues instead (TTFT 729 ms → 8.9 s). Size deployments for one in-flight request.
- **The single-shot win is the cache router**, not a faster kernel: the two builds run the same engine binary from the
  same tag. Without the router the same sweep puts us at parity with a +117 ms TTFT penalty on all traffic.
- **The chat trade is deliberate**: +117 ms on a context's first request buys a 5 s saving on every repeat.
- Both arms are resident in full on this card (12,288 expert slots, **0 experts over PCIe**), so the numbers measure
  compute, not PCIe traffic.

## Reproduce

```
~/bench-venv/bin/guidellm run \
  --backend 'kind=openai_http,target=http://127.0.0.1:8095/v1,model=qwen3.8-flash-next-coder-iq1_m' \
  --tokenizer 'kind=huggingface_auto,model=<home>/bench-tokenizer' \
  --data 'kind=synthetic_text,prompt_tokens=512,output_tokens=128' \
  --profile 'kind=concurrent' --override 'profile.streams' 1,2,4,8 \
  --constraint 'kind=max_duration,seconds=45' --output 'kind=json,path=guidellm.json'
```

Raw rows and console tables: `~/strata-perf/results/guidellm-route-*.{json,log}`, `pptg-router.jsonl`,
`quality-ab.jsonl`; the full comparison with method and caveats: `~/strata-stock-vs-ours.md`.
