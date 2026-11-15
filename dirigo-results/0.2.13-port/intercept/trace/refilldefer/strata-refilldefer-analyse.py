#!/usr/bin/env python3
"""Compare two refilldefer arms: TTFT, the refill line, and decode speed.

    ~/bin/strata-refilldefer-analyse.py DIR TAG_A TAG_B

Each arm is a ladder JSON (from strata-intercept-probe.py) plus the engine log slice its window wrote. The interesting
question is not only the median: with STRATA_REFILL_DEFER the cache keeps the prompt's experts, so the NEXT request may
pay for it. That shows up as a rising trend across the six samples per size in one arm and not the other.
"""
import json
import re
import statistics as st
import sys
from collections import defaultdict

RE_PROMPT = re.compile(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([0-9.]+) tok/s\), "
                       r"(\d+) generated in ([\d.]+) ms \(([0-9.]+) tok/s\)")
RE_REFILL = re.compile(r"strata trace: refilled (\d+) slots on \d+ stage\(s\) in ([\d.]+) ms")


def load(dirpath, tag):
    rows = json.load(open(f"{dirpath}/ladder-{tag}.json"))
    # pair the engine log's prompt lines with the ladder rows in order
    gen = []
    try:
        for line in open(f"{dirpath}/engine-{tag}.log", errors="replace"):
            m = RE_PROMPT.search(line)
            if m:
                gen.append(dict(tokens=int(m.group(1)), read_n=int(m.group(3)), prefill_ms=float(m.group(4)),
                                decode_tok_s=float(m.group(8)), generated=int(m.group(6))))
            m = RE_REFILL.search(line)
            if m and gen:
                gen[-1]["refill_ms"] = float(m.group(2))
                gen[-1]["refill_slots"] = int(m.group(1))
    except FileNotFoundError:
        pass
    for i, r in enumerate(rows):
        if i < len(gen):
            r["decode_tok_s"] = gen[i].get("decode_tok_s")
            r["refill_ms"] = gen[i].get("refill_ms")
            r["refill_slots"] = gen[i].get("refill_slots")
    return rows


def report(title, rows):
    by = defaultdict(list)
    for r in rows:
        by[r["prompt_tokens"]].append(r)
    print(f"\n{title}")
    print(f"  {'tokens':>7} {'n':>3} {'ttft med':>9} {'ttft by sample (s)':<28} {'refill':>7} {'decode tok/s':>13}")
    out = {}
    for size in sorted(by):
        g = by[size]
        seq = " ".join(f"{r['ttft_ms']/1000:.3f}" for r in g)
        ref = [r["refill_ms"] for r in g if r.get("refill_ms") is not None]
        dec = [r["decode_tok_s"] for r in g if r.get("decode_tok_s")]
        ref_txt = f"{st.median(ref):.1f}" if ref else "none"
        dec_txt = f"{st.median(dec):.1f}" if dec else "-"
        out[size] = dict(ttft=st.median([r["ttft_ms"] for r in g]),
                         refill=st.median(ref) if ref else None,
                         decode=st.median(dec) if dec else None, seq=seq)
        print(f"  {size:>7} {len(g):>3} {out[size]['ttft']:>9.0f}   {seq:<28} {ref_txt:>7} {dec_txt:>13}")
    return out


def main():
    d, a, b = sys.argv[1], sys.argv[2], sys.argv[3]
    ra, rb = load(d, a), load(d, b)
    A, B = report(f"{a} (refill kept)", ra), report(f"{b}", rb)
    print("\nper-size medians")
    print(f"  {'tokens':>7} {'A ttft':>8} {'B ttft':>8} {'delta':>8} {'A refill':>9} {'B refill':>9} {'A dec':>7} {'B dec':>7}")
    for size in sorted(set(A) & set(B)):
        x, y = A[size], B[size]
        f = lambda v, u="": f"{v:.1f}{u}" if v else "-"
        print(f"  {size:>7} {x['ttft']:>8.0f} {y['ttft']:>8.0f} {y['ttft']-x['ttft']:>+8.0f} "
              f"{f(x['refill']):>9} {f(y['refill']):>9} {f(x['decode']):>7} {f(y['decode']):>7}")


if __name__ == "__main__":
    main()
