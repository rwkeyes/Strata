# Why `--spec 4` survived: the tuning system's blind spots

Root-cause note, 2026-11-02. The question was "would the initial tuning have found `--spec 6`?" — it would not, and the
reason is not one bug but five links in a chain. Each link is fixable, and four of the fixes are small.

## The chain

1. **The swept set is a hardcoded list, not a description of what is tunable.**
   `tools/calibrate.py` sweeps `PCIE_FRACS`, `SPEC_MIN_PS` and `ADAPT_CANDIDATES` — three constants. `--spec` is an
   engine flag with a small integer range, and the file **never mentions it** (verified: 0 references). Nothing
   enumerates the engine's tunable surface, so a knob that exists in the engine and matters on a box has no path into
   tuning.

2. **The objective is one number on one prompt shape.**
   `Session.rate()` is decode tok/s on `doc_prompt()` — a prose document. The effect we were chasing is
   workload-dependent: prose 57.2 vs 58.1 tok/s (a tie), code continuation 88.7 vs 93.3 (**+5.2 %**). A sweep of
   `--spec` on that prompt would have measured a tie and, by its own 3 % rule, kept 4. The calibrator is
   workload-blind by construction, and every shape-dependent setting is invisible to it — `--spec` today, likely
   prompt-lookup drafting and the prefill chunk tomorrow.

3. **The rule that could have seen it was one-sided.**
   `serve/autotune.py` had `spec_depth`: acceptance below 40 % → propose a lower window. It was written from the
   failure someone had already met (wasted drafts). The signal on this box was the opposite — **59 of 75 accepted** on
   a code continuation — and no rule read that direction, so nothing could ever propose raising it. Fixed today:
   acceptance ≥ 80 % now proposes "raise".

4. **The proposal path had no outlet.**
   `apply_proposals()` writes `tuning-proposals.json` "for the next start"; **nothing called it**. Proposals lived in
   `self.proposals` (in the state file and in `GET /v1/tuning`) and the file a person or a start-up step is meant to
   read was never written. Fixed today: a proposal writes the file when it changes.

5. **Nothing validates a config after the fact.**
   The only re-measurement is drift against the config's *own* numbers, so a value that was never right cannot show
   drift — it is the baseline. `--spec 4` was an engine default that nobody on this box had measured; the config
   carried no record that it was unvalidated. Provenance is the missing piece: "adopted because measured at X" vs
   "carried because it was the default".

## What else should change

| # | change | why it follows from the chain | cost |
|---|---|---|---|
| A | **one table of tunable settings** — flag, class (warm/restart), candidates, measured cost — read by the calibrator, the tuner and the docs | link 1: adding a knob to the engine should be one row, not a code change in three places | medium refactor, no behaviour change |
| B | **measure on two prompt shapes** — **DONE**: `code_prompt()` reads `tools/*.py`, and any value the docs' sweep wants to CHANGE is then measured against the default on that second shape; a >3 % loss there keeps the default and says so | link 2: a single prompt cannot see a shape-dependent effect, and this box serves both | +2 samples, only when a change is proposed (~10 s) |
| C | **symmetric rules for every proposal** — the mirror of each "too little" signal ("too much" is a real case: `--kv-resident 65536` cost 6 %) | link 3, and the kv-resident result proves over-provisioning is its own failure mode | small, one rule at a time |
| D | **config provenance**: each value recorded with the measurement that justified it (gain, pairs, shape, date), and defaults shown as unvalidated | link 5: `--spec 4` was invisible as an unmeasured default | small; the tuner already keeps `{value, gain, pairs, from_stratum}` |
| E | **score TTFT as well as decode** in the calibration sweeps | a cache or window change trades one for the other; today only decode is scored, so a TTFT regression reads as a win | small: the accounting line already prints both |
| F | **surface pending proposals** in the drift check and the status line | link 4: a proposal nobody reads is the same as no proposal | small, and cheaper now that the file exists |

## The enabling fact

None of A–F was affordable while calibration cost 25 minutes, because a wider swept set meant more restarts. At
**4 m 36 s with one engine load**, a sweep that is 2–3× wider costs 10–14 minutes — still less than the old single
pass. The fast path is not only a shorter run; it is the budget that makes a wider one possible.

## B, as built

`run()` builds `CODE_IDS` from `code_prompt()` (the same three prompts' lengths, source text instead of the docs) and
`measure()` uses it at the one moment it matters — when the sweep proposes a *change*:

```
  Confirmation: (0.19, 0.7) led (0.19, 0.5) by 3% or more in every round of the sweep - accepted as measured
  Second shape agrees: (0.19, 0.7) reads 61.2 against the default's 58.4 tok/s on source code
```

If the second shape says the winner loses by 3 % or more, the default stands and the line says
"the change is NOT made (one shape's word is not a decision)". The seam is a module-level `CODE_IDS` rather than a new
`measure()` parameter, because upstream's tests and `setup.py` stub that signature — a lesson this session already paid
for once.

### Its first real use found the instrument itself wrong (kept as the record)

The shape rule refused `spec_min_p 0.7`; a ten-pair interleaved test on the serving prompt then said 0.7 wins by
**+12.35 %, 10 of 10**.  The rule's instrument, not its principle, was at fault twice: the shape was a concatenation
of whole files (not a continuation), and it took one sample per value right after switching (the cold-answer bias the
tier arms already needed a warm-up for). Both fixed; the calibrator now writes 0.7.

**The settled numbers** (`scripts`-adjacent `~/bin/spec-min-p-pair.py`, ten pairs, serving prompt, 128-token window):

| spec_min_p | median | pairs won |
|---|---:|---|
| 0.5 | 70.29 tok/s | — |
| **0.7** | **78.78 tok/s** | **10 of 10** (+12.35 %) |

And on the two serving workloads after adopting it (`--spec 6`, everything else unchanged):

| workload | at 0.5 | at 0.7 | |
|---|---:|---:|---|
| copy / file continuation | 71.24 | **82.61** | +16 % |
| a code question answered | 93.27 | 89.92 | −3.6 % |

So the value helps the continuation traffic this box actually carries and costs a few percent on question-answering —
a split the calibrator's single number cannot express, and the honest reason `spec_min_p` should eventually be chosen
per workload stratum rather than once.

### The earlier record: it refused a change, and the truth turned out to be "not resolvable"

On the live box the docs shape put `spec_min_p` 0.7 **5.0 % ahead** of 0.5 (60.9 vs 58.0), the fast rule accepted it,
and the second shape refused it: **0.7 read 67.4 against the default's 71.2** on source code (−5.3 %). Three
measurements of "code" then disagreed with each other:

| measurement | window | 0.5 | 0.7 |
|---|---:|---:|---:|
| calibrator's code shape (1 500-token prompt) | its own | **71.2** | 67.4 |
| live serving path, file-continuation prompt | 64 tokens | 47.7 | **49.9** |
| live serving path, same prompt | 128 tokens | 70.7 | 70.5 |

So on this workload the choice is **window-dependent and inside the noise at 128 tokens**. The right outcome is the
one the rule produced: no change written, and the report says why. A tuner that had trusted the docs shape would have
written 0.7 on 5 % of evidence that a second workload did not confirm — which is exactly what the previous five
calibration runs did.

Cost of the check on this box: 276 s → **301 s** (the second shape's prompts are 1 500 tokens, so its two samples cost
more than the fragment version's).

Pinned by `test_a_value_that_loses_on_the_second_shape_is_not_adopted` and
`test_the_second_shape_confirms_a_value_that_wins_on_both` (`tools/test_calibrate.py`, 38 passed). The fake engine
gained a shape hook that can invert a ranking — the first version only scaled both values equally and passed
vacuously, which is the failure mode the test exists to prevent.

## The trap underneath: the wrapper kept rewriting the answer

`~/bin/strata-b70-serve.sh` regenerates the config file every time it runs, so every calibration result was
overwritten by the wrapper's own default — **five calibrations wrote `--spec-min-p 0.70` and the file kept reading
`0.5`**. The wrapper now reads `--spec-min-p` and `--pcie-frac` back from the existing config before writing
(`KEEP_TUNED=0` restores the old behaviour), and carries the settled 0.7 with the ten-pair numbers beside it.

That is the same class as `--spec 6`: **a value that lives only in the file the tool rewrites is not a decision.**


## CORRECTION (later the same day): the spec_min_p claim does not reproduce

I reported "0.7 wins by +12.35 %, 10 of 10 pairs" and adopted it. **Withdrawn.** The same paired method, same prompt,
same box, three sessions on either side of a restart:

| config default | measured 0.5 | measured 0.7 | verdict of that session |
|---|---:|---:|---|
| 0.5 | 70.29 | 78.78 | 0.7 wins, +12.35 %, 10/10 |
| 0.7 | 80.57 | 75.62 | 0.5 wins, −6.2 %, 0/6 |
| 0.7 | **96.93** | 92.82 | 0.5 wins, −2.8 %, 1/6 |

Three things are visible in that table and only one of them is about `spec_min_p`:

1. **Run-to-run level moves by ~30 %** (70 → 97 tok/s for the same nominal settings on the same prompt), far more
   than the effect being hunted. Whatever else is happening, this instrument cannot resolve a few percent.
2. **The verdict follows the config's default**, not the value: the configuration the engine started with is the one
   that "wins". That is either a property of how a per-request override interacts with the startup value, or an
   artefact of the same drift.
3. The within-run consistency in the first session (10 of 10) was real *within that session* and meant nothing across
   one.

**What changed as a result:** the config is back on the engine's own default (0.5), `tools/verify_shapes.py` reports
rather than reverts unless `--revert` is passed, and the deferred-guard plumbing stays (it costs nothing when it only
reports). The lesson is the one this whole session keeps re-learning: **a paired comparison inside one session is not
a measurement across sessions**, and a number that does not reproduce must not be adopted — even when it is
convenient.

The one thing that survives: `--spec 6` was confirmed twice by two independent interleaved pairs with stable
baselines (68.90/69.09 vs 71.90/71.82 on copy, 88.67/88.73 vs 93.46/93.30 on code) and its effect reproduces in the
serving measurements. `spec_min_p` does not have that.


---

## The 30 % swing, explained: it is warm-up, not drift

Measured with the settings fixed, the same prompt, three engine restarts and twelve samples each
(`~/bin/warmup-swing.py`):

| samples after a start | tok/s | |
|---|---:|---|
| 1 | 83.5 / 83.4 / 83.3 | first request |
| 2–4 | 93.1 / 93.1 / 93.1 | one step up |
| 5–12 | 97.0 / 96.9 / 96.8 | settled |
| **settled vs first** | **+16.2 %** | within one run |
| **settled across the three restarts** | **0.2 %** | between runs |

So the box does **not** drift between runs. The rate climbs in three discrete plateaus over the first five requests —
the adaptive expert tier converging (`--adapt-every 4`, `--adapt-swaps 96`) — and any protocol that samples early
reads 4–16 % low. A sweep that straddles a step reads the candidate before it about 4 % slow, which is one way a
phantom 5 % "win" appears.

That single fact explains the contradictions earlier in this document: the `+12.35 %` session warmed two pairs (so
its 0.7 side sat on the top plateau while 0.5 sat on the middle one), and the `−6.2 %` session warmed one sample
each. Neither was measuring the setting; both were measuring where in the curve they started.

**Fixed in the harnesses** — all of them now settle with six requests before comparing: `warmup` in
`tools/calibrate.py` (`warm_up(2)` → `warm_up(6)`), `tools/verify_shapes.py`, `scripts/spec-min-p-pair.py`,
`decode-ab-probe.py`.

### Re-measured with the fixed protocol

Ten pairs, both values settled, serving prompt, 128-token window:

| spec_min_p | median | pairs won |
|---|---:|---|
| **0.5** (the engine's own default) | **96.97 tok/s** | **9 of 10** |
| 0.7 | 92.83 tok/s | 1 of 10 |

**−4.25 % for 0.7, 1 of 10 pairs.** The engine default stands, the earlier adoption is confirmed withdrawn, and this
time the number is stable enough to act on: the per-pair deltas are −4.2 %, −4.2 %, −4.3 %, −4.3 %, −4.3 %, −4.3 %,
−1.2 %… with **two single samples ~25 % low** (one on each value), which is a separate, still-unexplained periodic
dip that does not move a median but makes any single-sample comparison unreliable.
