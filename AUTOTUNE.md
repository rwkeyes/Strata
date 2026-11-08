# Auto-tune: a Strata that keeps itself tuned

`serve/autotune.py` watches the requests a running Strata already serves, works out which settings suit this
machine *and this workload*, and - if you let it - changes them between requests with the engine's `TUNE` command.
No restart, no weight reload, no separate calibration run.

```
serve/server.py --config strata-<model>.json            # --auto-tune shadow is the default
serve/server.py --config strata-<model>.json --auto-tune on --auto-tune-budget 240
```

| mode | what it does | when to use |
|---|---|---|
| `off` | nothing at all | benchmarking, or you want a fixed config |
| `shadow` (**default**) | classifies every request, records what it would change and why, changes nothing | any live box; it is the data-collection mode |
| `on` | explores a second value on one request out of two, adopts what wins, restores it, writes the config | a box you are willing to have tuned |

![](nothing is written unless `mode = on` *and* no request is in flight.)

## Where the numbers come from

The engine can already account for every request, and it prints what it did. The tuner parses exactly these lines —
it never uses a client-side clock:

```
strata serve: prompt 4210 tokens = 0 reused + 4210 read in 3041 ms (1385.0 tok/s), 66 generated in
              1030 ms (64.1 tok/s), drafts accepted 2 of 3, 1 checkpoints
strata serve: decode expert cache hit rate: 99.5% (12519 hits / 12583 lookups; 64 sent over PCIe)
strata serve: KV streaming: 96.55% of 4872 block reads hit VRAM, 0.7 MiB read from RAM
strata serve: conversation cache: parked 3004 tokens in 126.1 ms; parked=1 ... evictions=0
```

From them a request becomes an **Observation**: prompt tokens, cache reuse, prompt and decode time, drafts
accepted/offered, expert hit rate and PCIe offloads, KV hit rate, parking cost. The quantity it optimises is
`1 / (prompt_ms + decode_ms)` — requests per second end to end, which is what a user waits for.

## Strata, not one global setting

Every observation lands in a **stratum**: cache state × prompt length × answer length, e.g. `cold/mid/terse` or
`warm/long/long`. This is not cosmetic. On the reference box (Arc Pro B70) cache-warm prompts read at **11,925 tok/s**
and cold ones at **1,387** — pooling them would swamp any setting effect by an order of magnitude. All statistics,
and all adopted values, are kept per stratum.

## How it decides

1. **Explore one arm at a time.** While a setting still needs evidence, request N is the baseline, N+1 carries a
   candidate value (applied with `TUNE` between requests), N+2 is the baseline again. ABAB, not "try it for a while".
2. **Compare only pairs.** The median of `arm/baseline − 1` over matched pairs in the same stratum. Drift (thermal,
   page cache, expert-tier warm-up) lands on both arms, so it cancels. This matters: the tier warm-up alone measured
   20 % on the reference box (46.9 tok/s then 58.0 on the same setting).
3. **Adopt on evidence, not on a hunch.** `gain ≥ 3 %`, `≥ 12` pairs, and the same sign in two sessions
   (`tuning-<model>-<hardware>.json` keeps the session count). Anything less stays an open question in
   `GET /v1/tuning`, with `enough_data: false`.
4. **One value per setting, and a strata disagreement is a refusal.** The engine has one option, so adoption
   records which stratum the evidence came from (`from_stratum`) rather than pretending the value is per-stratum.
   If one stratum with enough pairs says the same value is 3 % *worse*, the tuner declines and says
   `"why": "strata disagree"` — adopting on the colder stratum's word while the warm one contradicts it is how a
   tuner slows down the workload it was not watching.
5. **A value outside its range never leaves the process.** `setting_bounds_ok()` gates every TUNE the tuner
   sends; an out-of-range change is refused locally and noted under `errors`.
4. **Never touch what you set.** Any of these on the command line is pinned and never explored or overridden:
   `--pcie-frac`, `--spec-min-p`, `--adapt-every`, `--adapt-swaps`, `--adapt-decay`, `--adapt-min-gain`,
   `--prompt-cache`, `--short-read`.

## What it can change while running

Settings the engine reads at the moment it uses them — `TUNE k=v` between requests, no reload:

`pcie_frac`, `spec_min_p`, `adapt_every`, `adapt_swaps`, `adapt_decay`, `adapt_min_gain`, `prompt_cache`,
`prompt_cache_every`, `short_read`, `prefill_chunk` (downward only — its buffers were sized at load).

## What it can only propose

These are sized when the model loads, so the tuner reports them with the evidence that produced them and writes
them for the next start; it never changes them under a running engine.

| setting | trigger it watches for |
|---|---|
| `--kv-resident` / `--kv int8` | KV block-read hit rate below 90 % on prompts ≥ 8k |
| `--expert-cache` | expert hit rate below 95 % (experts going over PCIe) |
| `--spec` depth | fewer than 40 % of drafts accepted (the window is wider than the text) |
| `--conversation-cache-mib` | parking over 250 ms while reuse is high, or evictions |

`tuner.apply_proposals()` writes `tuning-proposals.json` with the flags and the evidence.

## Endpoints

| | |
|---|---|
| `GET /v1/tuning` | mode, hardware key, per-stratum medians, adopted values, open questions with their evidence, proposals, the last decisions |
| `POST /v1/tune` | `{"settings": {"adapt_min_gain": 3.0}}` — the operator's own retune; 409 while a request is in flight |

```bash
curl -s localhost:8095/v1/tuning | jq '.strata, .adopted, .proposals'
curl -s -X POST localhost:8095/v1/tune -d '{"settings":{"spec_min_p":0.6}}'
```

## Safety rules, in order

- A setting outside its bounds is refused, never clamped silently into the config.
- Nothing is written while `busy` is set or a request is queued: `tune()` reads the engine's shared reply stream, and
  a request streaming at the same time would lose a line to it.
- A request longer than `latency_guard_tokens` (12k) is never an exploration arm — it is dropped and noted as such.
  Requests in flight do not carry a value that is being flipped per request.
- The first `cold_requests` (8) observations after a start are recorded but never acted on: that is the tier and
  prompt-cache warm-up, and it is the single largest source of false wins.
- Every failure is caught and reported in `GET /v1/tuning` under `errors`; the tuner cannot take serving down.

## Limits, stated

- **Exploration costs requests.** With `on`, one request in two carries a candidate while a setting is being
  explored, up to `--auto-tune-budget` requests per session. With one user that is the price of knowing.
- **Applying is single-flight.** `TUNE` applies between requests, so with `--parallel > 1` the tuner restricts
  itself to `GET`-visible reporting and the two per-request keys the engine already takes (`pcie_frac`,
  `spec_min_p`) rather than flipping persistent values on shared traffic.
- **It cannot change what is sized at load** (KV type and size, expert-cache size, `--spec` depth) — that is the
  proposals table, and the next thing worth building is a session rebuild that keeps the weights.
- **It will not find structural wins.** On the reference box the big levers are cache reuse (8.6×) and prompt-read
  chunking (2× in earlier measurements) — configuration defaults, not continuous tuning. Expect 3-8 % per axis and
  the property that a long-running install stays at its measured optimum.

## Verified on the reference box (2026-11-02)

- 14 unit tests pass: parsing, strata, size-matched pairing, the latency guard, pinned settings, the busy gate,
  shadow-never-writes, proposals, state round-trip, report shape.
- `GET /v1/tuning` on the served engine: `mode shadow, session 1`, strata populated with medians, state file written.
- `POST /v1/tune` four ways: valid key applied, unknown key returned under `refused`, `adapt_swaps` applied,
  `adapt_min_gain` **refused on the SYCL build** (the DPCT-migrated tree has no such field) — and the tuner then
  pins it and says so under `errors`, instead of retrying it forever.
- `--auto-tune on` driven with 10 requests: arms applied and restored around each request, and the loop rotated
  across `adapt_every`, `adapt_swaps`, `pcie_frac`, `prompt_cache`, `short_read`. **No adoption fired** — with one
  user and size-matched pairing, 12 pairs per stratum needs more traffic than 10 requests, which is the honest
  threshold rather than a tuning failure.

Two defects found and fixed by that run, both worth knowing about:
1. **An arm must be sticky.** The first version started a new arm on every 2 s tick, so arms flipped without a
   request in between and the tag landed on whatever was served next. Now an arm is only replaced once a request
   has actually been served since it was applied.
2. **Pair by request size, not just by stratum.** The first paired comparison reported `+200 %` because a 55-token
   chat turn was paired against a 1,900-token prompt. Pairing is now nearest-size within 25 %, one to one.

## Tests

```
.venv/bin/python -m pytest serve/test_autotune.py -q      # 13 tests: parsing, strata, pairing, guards, state
```

## UNDO

```
rm serve/autotune.py serve/test_autotune.py
# and in serve/server.py: the `--auto-tune*` arguments, the `svc.tuner = ...` block, the GET /v1/tuning and
# POST /v1/tune handlers, and (if the fork should not carry it) the TUNE command itself in generate.cpp
```
