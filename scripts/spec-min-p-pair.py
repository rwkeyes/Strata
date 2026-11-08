#!/usr/bin/env python3
"""Settle one setting with paired samples on the SERVING prompt, at the SERVING window.

    python3 scripts/spec-min-p-pair.py [--a 0.5] [--b 0.7] [--pairs 10] [--max-tokens 128] [--port 8095]

Why it exists: on this box the calibration's prose prompt put spec_min_p 0.7 five percent ahead, the calibrator's
code prompt put it five percent behind, and two live reads at 64 and 128 tokens disagreed again.  Three shapes, three
answers — so the question is not "which number wins in the calibrator", it is "which number wins on the traffic this
box actually serves", asked with enough pairs to say.

Method: alternating order (A/B, then B/A), one pair per round, decode rate measured from the first streamed token to
the last (prefill excluded), tokens counted in content AND reasoning.  The ordering alternates so drift lands on both
values equally; the verdict is the median paired difference plus a sign test, because with ten pairs the sign count is
the honest statistic.

The box must be quiet: it reads GET /v1/tuning's request counters before and after and subtracts its own requests.
"""
import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serving_measure as sm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=float, default=0.5)
    ap.add_argument("--b", type=float, default=0.7)
    ap.add_argument("--setting", default="spec_min_p")
    ap.add_argument("--pairs", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--prompt-file", type=Path, default=sm.default_prompt_file())
    ap.add_argument("--chars", type=int, default=sm.DEFAULT_CHARS)
    ap.add_argument("--model", default=sm.DEFAULT_MODEL)
    a = ap.parse_args()

    base = f"http://127.0.0.1:{a.port}"
    before = sm.get(base, "/v1/tuning").get("requests") or {}
    if before.get("busy"):
        print(f"busy: {before['busy']} request(s) in flight — not measuring a box someone is using", file=sys.stderr)
        return 2

    text = sm.load_prompt(a.prompt_file, a.chars)
    mine = 0

    def once(value: float) -> float:
        nonlocal mine
        mine += 1
        return sm.stream_rate(base, text, max_tokens=a.max_tokens, model=a.model,
                              tune={a.setting: value})

    for _ in range(2):                      # discarded: the first answers after a setting change are slow
        once(a.a)
        once(a.b)

    deltas, ra, rb = [], [], []
    for i in range(a.pairs):
        order = [a.a, a.b] if i % 2 == 0 else [a.b, a.a]
        got = {value: once(value) for value in order}
        if not got[a.a] or not got[a.b]:
            continue
        ra.append(got[a.a])
        rb.append(got[a.b])
        deltas.append(got[a.b] - got[a.a])
        print(f"pair {len(deltas):2d}: {a.a} {got[a.a]:6.2f}  {a.b} {got[a.b]:6.2f}   "
              f"delta {got[a.b] - got[a.a]:+6.2f} ({(got[a.b] - got[a.a]) / got[a.a] * 100:+.1f}%)")

    after = sm.get(base, "/v1/tuning").get("requests") or {}
    foreign = (after.get("served", 0) - before.get("served", 0)) - mine
    if foreign:
        print(f"NOTE: {foreign} other request(s) arrived during the run — treat the numbers with suspicion",
              file=sys.stderr)

    if len(deltas) < 5:
        print("too few usable pairs", file=sys.stderr)
        return 2
    b_better = sum(1 for d in deltas if d > 0)
    med = statistics.median(deltas)
    med_pct = med / statistics.median(ra) * 100
    print()
    print(f"{a.setting}: {a.a} median {statistics.median(ra):.2f} tok/s, "
          f"{a.b} median {statistics.median(rb):.2f} tok/s over {len(deltas)} pairs")
    print(f"median paired delta {med:+.2f} tok/s ({med_pct:+.2f}%), {b_better} of {len(deltas)} pairs favour {a.b}")
    verdict = "no difference" if abs(med_pct) < 3 else (f"{a.b} wins" if med > 0 else f"{a.a} wins")
    if abs(b_better - len(deltas) / 2) < 2:
        verdict = "no difference (sign test split)"
    print(f"verdict: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
