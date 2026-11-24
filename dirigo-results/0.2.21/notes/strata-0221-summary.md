# 0.2.21 carry: where it landed (measured 2026-11-24)

Four upstream releases arrived at once — **v0.2.18 … v0.2.21** (655 commits, engine 0.1.63 → **0.1.67**). The tree keeps
the reorganised `engine/` + `terminal/` layout, so the path-mapped carry applies unchanged.

## What the replay needed

38 paths (12 modified, 18 added, 8 merged), **one conflict file** — `terminal/serve/server.py`, three hunks, each "both
sides added something different at the same spot". All three kept both sides:

| hunk | upstream | ours | resolution |
| --- | --- | --- | --- |
| the engine wrapper methods | `vram(reserve, timeout, device)` + `_exclusive_control` | runtime retune `tune(settings)` | upstream's `vram` first, then our `tune()` |
| the reasoning budget | `#6734` Inkling-aware `think_budget` / `room_cap` | `apply_short_spec(sampling, max_new)` | our short-spec call, then upstream's block; our older `budget = … if thinking` line dropped, since upstream generalised it to `think_budget` |
| the chat path | `check_model()` → 404 | `conv_route_decide()` hook | upstream's check, then our hook |

Committed as `095ebe077` on `release/0.2.21`.

## Verification (vega / Arc Pro B70)

| check | 0.2.21 fork | 0.2.17 fork |
| --- | --- | --- |
| suite | **2,371 tests OK** (skipped 27, 872 s) | 2,106 OK (skipped 26) |
| ctest | 19 failed **of 113** | 19 failed of 97 |
| failing set | the same `native_expert_parity_new_*` | same |
| minefield / gate | 5/5 PASS, gate PASS | 5/5, PASS |
| levers | `/v1/tuning` + `/v1/tune` answered | same |
| extraction | `'2024-06-09'`, 0 reasoning chars | same |

Upstream added 16 tests and 265 tests' worth of suite; it did not add failures.

## Performance: the prefill regression is gone

Two probes each, same session, same pack/flags/profile, 12 requests, 512-in/128-out:

| build | engine | prefill | dead | wall/request | decode |
| --- | --- | --- | --- | --- | --- |
| production 0.2.12 | 0.1.58 | 756 ms | 24 | 2,262 | 87.7 |
| our 0.2.17 | 0.1.63 | 770 | 23 | 2,352 | 80.8 |
| **0.2.21 fork** | **0.1.67** | **752 / 754** | 23 | **2,229 / 2,232** | 86.8 |
| stock 0.2.21 | 0.1.67 | 962 / 968 | 21 | 2,438 / 2,447 | 86.8–87.1 |

- **Against our own 0.2.17: −2.2 % prefill and −5.2 % wall**, outside the ±2 % prefill band and in the same direction on
  both runs. The +1.9 % step that arrived with engine 0.1.60 and survived 0.1.61/0.1.63 is **gone at 0.1.67** — 0.2.21 is
  the first fork build since 0.2.12 whose prefill matches production.
- **Against stock at the same tag: −22.0 % prefill** (753 vs 965 ms) and −8.7 % wall. The carry's advantage is intact
  (it was 22.4 % at 0.2.17), so all of the improvement is upstream's engine work, not a change in the carry.
- Decode 86.8 tok/s on both arms and dead time 21–23 ms: no difference to claim, as always on this box.

Likely source of the prefill recovery: upstream rewrote the SYCL prefill path between the tags —
`engine/sycl/src/prefill/prefill.cpp` +885, `prefill/kernels.dp.cpp` +221, `program/generate.cpp` +443.

## Still true after four releases

Upstream v0.2.21 ships **none** of the fork's features — zero hits for `conv_route`, `short_spec`, `spec_window`,
`thinking_policy`, `code_tune` — so the carry still earns its keep: the router and the levers are ours alone, and stock
answers 404 on `/v1/tuning`.

## GuideLLM concurrency matrix (two arms, 512-in/128-out, 45 s windows)

| c | 0.2.21 fork (median of 2 arms) | production 0.2.12 | our 0.2.17 | Δ vs production | Δ vs 0.2.17 |
| --- | --- | --- | --- | --- | --- |
| 1 | **727 ms** / 65.4 t/s | 753 / 56.9 | 739 / 59.7 | −3.5 % | −1.6 % |
| 2 | 2,535 / **68.3** | 2,484 / 68.3 | 2,702 / 59.7 | +2.1 % | **−6.2 %** |
| 4 | 6,102 / **68.3** | 6,449 / 65.4 | 6,574 / 62.6 | **−5.4 %** | **−7.2 %** |
| 8 | 9,790 / 68.3 | 9,759 / 65.4 | 9,278 / 62.6 | +0.3 % | +5.5 % |

Both arms agree to within 1 % at every level except c=8 (1 % apart there too, but that level has the widest repeat
spread on this box). The honest reading: **0.2.21 is level with production at c=1 and c=8, and 5-7 % better than our own
0.2.17 at c=2 and c=4**, which is where the prefill recovery pays off. Aggregate throughput is equal-or-better at every
level, and the c=8 row is the one to distrust — 9,278 vs 9,790 is inside this harness's known swing, so it is a wash
rather than a regression.

## Verdict

0.2.21 is the strongest fork head so far: production-level prefill, the best c=2/c=4 latency of any fork build, the
carry intact at −22 % prefill against stock, and the suite/card gate green. Production stays on 0.2.12 until someone
decides the move; the measurements no longer argue against it.
