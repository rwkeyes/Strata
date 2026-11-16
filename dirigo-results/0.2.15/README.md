# Dirigo 0.2.15 port vs stock 0.2.15 — same battery, Arc Pro B70

Engine **0.1.61** on both sides — upstream `v0.2.15` (commit `c7757280`) in the stock arm, the same tag plus this
fork's carry in the Dirigo arm. Both trees were built from source for the Intel Arc with the SYCL AOT target
`bmg-g31`, and every test below ran against those two binaries on one card, in one session.

## Headline

- **Engine speed: parity.** Seven measured legs (prefill + decode, straight to the engine, medians of two
  interleaved passes, plus an over-window refusal both arms answer identically), largest difference **1.1 %** —
  inside this rig's run-to-run spread.
- **Server: the first token arrives 11–21 % sooner** on fresh prompts — 749 ms against 954 ms at one stream,
  and the same 45-second window finishes 22 / 22 / 23 / 22 answers against 21 / 21 / 20 / 21.
- **Control, same build, one setting different:** with the router in `shadow` (classify but never switch — the
  stock behaviour) the same build reads **961 ms** at one stream; with it `on`, **737 ms**. The A/B's two arms
  (749 / 954) sit inside those two positions, so the switch is what moves the number.
- **Unit tests:** 1,567 green in the fork tree against 1,542 in the stock tree (both `OK`, 23 skipped) — the
  25-test gap is the carry's own test files.
- **Kernel-parity battery (ctest):** **85/85** in the fork tree, **83/85** stock, whose two failures
  (`ple_parity`, `qsa_prompt_attn_parity`) are the upstream gaps this carry closes.
- **0 errored requests** in both arms at every concurrency level.

## Hardware and software

| | |
| --- | --- |
| Card | Intel Arc Pro B70 32 GB (BMG-G31), `level_zero:0` |
| CPU | Ryzen 7 7700X, 16 threads |
| Model | Qwen3.8-Flash-Next Coder `IQ1_M` (native pack `coder-iq1_m`, 12,288 experts, 12,288 slots resident, 0 over PCIe) |
| Engine | 0.1.61, SYCL AOT `bmg-g31`, oneAPI 2026.2 (`icx`/`icpx`), ggml pin `3cf0325` |
| Server | `serve/server.py` from each tree, one tuned config each — identical apart from paths |
| Settings | `--spec 6 --spec-min-p 0.5 --kv int8 --kv-resident 32768 --vram-reserve-mib 700 --mtp-window 8192 --max-context 131072` |
| Harness | GuideLLM 0.8.1 (512 in / 128 out, streaming, `temperature 0`, 45 s per point); `tools/hip/bench_prefill.py` for the engine legs |
| Date | 2026-11-16 |

`--spec 6 --spec-min-p 0.5` on both sides and neither arm sent an override, so drafting is not part of the gap.

## 1. Unit suites

| tree | tests | time | result |
| --- | ---: | ---: | --- |
| this fork (published snapshot `dirigo/0.2.15-verified`) | 1,567 | 615.9 s | `OK (skipped=23)` |
| this fork (`release/0.2.15`, the tree the engine was built from) | 1,567 | 616.1 s | `OK (skipped=23)` |
| stock `v0.2.15` | 1,542 | 615.4 s | `OK (skipped=23)` |

Run with the tree's own `.venv/bin/python -m unittest discover -s serve -p 'test_*.py'`. **Stock is green here
too** — the one failure the 0.2.13 comparison reported (`test_hip_ordinal`, this fork's
[#5602](https://github.com/Niko1221/Strata/pull/5602)) is fixed upstream and present in v0.2.15. The `ctest`
battery below is the suite where the two trees still differ.

## 2. Behaviour probes

| probe | this fork | stock |
| --- | --- | --- |
| dequant minefield (5 traps: capital, decimal, non-empty, count, reverse) | 5/5 | 5/5 |
| card gate — one-token 200, code answer, 28,113-token needle, over-window refusal | PASS | PASS |
| needle retrieval, marker `KX-9182` | correct, 16.1 s | correct, 16.6 s |
| 163,599-token request | 400, "requests are never truncated" | identical wording |
| date in a 24-token budget ("9 June 2024" → ISO) | `2024-06-09`, 0 reasoning chars, `stop` | `content=None`, 84 reasoning chars, `finish=length` |
| `GET /v1/tuning` | 200 | 404 |
| `POST /v1/tune` | 200, applied | 404 |

## 3. Prefill / decode battery (medians of 2 interleaved passes, order d s s d)

| leg | prompt | fork pre / dec | stock pre / dec | Δ prefill | Δ decode |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh | 4,210 | 1,330.5 / 81.5 | 1,315.3 / 81.5 | −1.1 % | −0.1 % |
| fresh | 8,830 | 1,402.1 / 86.1 | 1,399.1 / 86.0 | −0.2 % | −0.1 % |
| follow-up | 4,451 | 326.8 / 76.3 | 326.8 / 76.2 | −0.0 % | −0.1 % |
| follow-up | 9,071 | 274.2 / 86.5 | 273.0 / 86.5 | −0.4 % | +0.0 % |
| code | 32 | 61.2 / 114.8 | 60.8 / 114.5 | −0.7 % | −0.3 % |
| needle | 7,855 | 1,434.9 / 98.0 | 1,431.0 / 98.0 | −0.3 % | +0.0 % |
| long | 30,178 | 1,817.2 / 49.0 | 1,814.3 / 49.3 | −0.2 % | +0.6 % |
| over-window | — | HTTP 400 | HTTP 400 | — | — |

Δ is stock relative to the fork. The 15-token warm-up request is excluded (first request after load, not a
measurement). Same legs on the 0.2.14 build landed within 0.5 % of these.

## 4. GuideLLM

| streams | TTFT fork | TTFT stock | fork arrives sooner | ms | read the other way | tok/s fork | tok/s stock | finished fork / stock |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | **749 ms** | 954 ms | **21.4 %** | 204 | stock needs 27.3 % longer | 62.6 | 59.7 | 22 / 21 |
| 2 | **2,617 ms** | 2,957 ms | **11.5 %** | 340 | 13.0 % longer | 62.6 | 59.6 | 22 / 21 |
| 4 | **6,346 ms** | 7,403 ms | **14.3 %** | 1,057 | 16.7 % longer | 65.4 | 56.9 | 23 / 20 |
| 8 | 9,314 ms | 9,453 ms | 1.5 % | 138 | 1.5 % longer | 62.6 | 59.7 | 22 / 21 |

Both arms: 0 errored requests. A `max_duration` of 45 s cancels requests still in flight when the window closes
(4–14 per point, the same on both sides) — those are window-boundary artefacts, not failures.

**The mechanism, isolated.** The same build, the same card, one setting different:

| setting | first token, 1 stream | requests finished in 45 s | output tok/s |
| --- | ---: | ---: | ---: |
| `conv_route: shadow` — classify and log, never switch (stock behaviour) | 961 ms | 19 | 54.0 |
| `conv_route: on` — switch the cache per traffic shape | **737 ms** | 22 | 62.6 |

The A/B's arms (749 / 954 ms) sit inside these two positions, so the router accounts for the gap; nothing else
in the build was changed.

## 5. What this does and does not show

- **Does:** the engine is at parity; the server-side difference is real, repeatable across 0.2.13 / 0.2.14 /
  0.2.15 measurements, and traceable to one setting.
- **Does not:** a faster engine; a win on conversations (the cache comes back on and the two converge); anything
  about multi-slot serving — both arms ran `--batch 0`, one request at a time, so at eight streams the two
  servers are mostly queueing. A throughput win at one stream is not claimed: one extra request in a 45-second
  window is the measurement's own granularity (4.8 %).
- The advantage is largest for one user at one keyboard. With several clients, size the box with
  `--batch`/`--slots` and treat the 21 % as the single-user figure.
- Drift: one pass per arm in one session. The 0.2.12→0.2.13 pair put positional drift at 2–4 %, and the
  0.2.14 build re-measured the same legs within 0.5 % on another day, so the 11–21 % gaps sit outside it.

## Reproducing

```sh
dirigo-results/0.2.15/scripts/strata-guidellm-0215-ab.sh   # both GuideLLM arms, one session
dirigo-results/0.2.15/scripts/strata-0215-ab.sh            # engine battery, 2 interleaved passes
dirigo-results/0.2.15/scripts/strata-0215-cardtest.sh      # unit ctest, behaviour probes, levers
python3 dirigo-results/0.2.15/scripts/make-chart.py        # recomputes every figure on the front page
```

`guidellm/` holds the four GuideLLM JSONs (two arms + the two control positions), `battery/` the eight engine
JSONs, `suites/` the suite and ctest summaries. Every number in the top-level README is recomputed from these
files by `make-chart.py`.
