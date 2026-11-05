#!/usr/bin/env python3
"""Settle one setting with paired samples on the SERVING prompt, at the SERVING window.

    ~/bin/spec-min-p-pair.py [--a 0.5] [--b 0.7] [--pairs 10] [--max-tokens 128] [--port 8095]

Why it exists: on this box the calibration's prose prompt put spec_min_p 0.7 five percent ahead, the calibrator's
code prompt put it five percent behind, and two live reads at 64 and 128 tokens disagreed again.  Three shapes, three
answers — so the question is not "which number wins in the calibrator", it is "which number wins on the traffic this
box actually serves", asked with enough pairs to say.

Method: alternating order (A/B, then B/A), one pair per round, decode rate measured from the first streamed token to
the last (prefill excluded), tokens counted in content AND reasoning.  The ordering alternates so drift lands on both
values equally; the verdict is the median paired difference plus a sign test, because with ten pairs the sign count is
the honest statistic.

The box must be quiet: it reads GET /v1/tuning's request counter before and after and subtracts its own requests.
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

PROMPT_FILE = Path("/home/user/strata-fork-sync/tools/calibrate.py")   # a real file, continued: this box's shape
PROMPT_CHARS = 9000


def prompt() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8", errors="replace")[:PROMPT_CHARS]
    return ("Continue this file from where it stops, reproducing the same style and identifiers:\n\n"
            + text + "\n\ndef _next_part() -> None:\n")


def get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.load(r)


def once(port: int, setting: str, value: float, text: str, max_tokens: int) -> float:
    body = json.dumps({"model": "m", "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
                       "strata_tune": {setting: value},
                       "messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    first = last = None
    n = 0
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            if not raw.startswith(b"data: {"):
                continue
            try:
                d = json.loads(raw[6:])
            except Exception:
                continue
            delta = (d.get("choices") or [{}])[0].get("delta") or {}
            if delta.get("content") or delta.get("reasoning_content"):
                now = time.time()
                first = first or now
                last = now
                n += 1
    return (n - 1) / (last - first) if first and last and n > 3 and last > first else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=float, default=0.5)
    ap.add_argument("--b", type=float, default=0.7)
    ap.add_argument("--setting", default="spec_min_p")
    ap.add_argument("--pairs", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--port", type=int, default=8095)
    a = ap.parse_args()

    before = (get(a.port, "/v1/tuning").get("requests") or {})
    if before.get("busy"):
        print(f"busy: {before['busy']} request(s) in flight — not measuring a box someone is using", file=sys.stderr)
        return 2
    text = prompt()
    mine = 0

    for _ in range(2):                                     # discarded: the first answers after a setting change are slow
        once(a.port, a.setting, a.a, text, a.max_tokens); mine += 1
        once(a.port, a.setting, a.b, text, a.max_tokens); mine += 1

    deltas, ra, rb = [], [], []
    for i in range(a.pairs):
        order = [(a.a, "a"), (a.b, "b")] if i % 2 == 0 else [(a.b, "b"), (a.a, "a")]
        got = {}
        for value, tag in order:
            got[tag] = once(a.port, a.setting, value, text, a.max_tokens)
            mine += 1
        if not got["a"] or not got["b"]:
            continue
        ra.append(got["a"]); rb.append(got["b"]); deltas.append(got["b"] - got["a"])
        print(f"pair {len(deltas):2d}: {a.a} {got['a']:6.2f}  {a.b} {got['b']:6.2f}   "
              f"delta {got['b'] - got['a']:+6.2f} ({(got['b'] - got['a']) / got['a'] * 100:+.1f}%)")

    after = (get(a.port, "/v1/tuning").get("requests") or {})
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
    if b_better in (len(deltas) // 2,) or abs(b_better - len(deltas) / 2) < 2:
        verdict = "no difference (sign test split)"
    print(f"verdict: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
