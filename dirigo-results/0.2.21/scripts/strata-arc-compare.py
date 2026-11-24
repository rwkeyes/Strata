#!/usr/bin/env python3
"""strata-arc-compare.py — put one probe run next to the recorded per-version baselines.

Built because every cycle starts by asking the same question ("is this build slower than what we run?") and
that question had been answered by hand, in markdown, from a pile of one-off arms.

  strata-arc-compare.py <probe.json> [--baselines ~/strata-baselines.json] [--label NAME]

Reads a spill/quality probe OR a turnaround probe result (the turnaround one carries `summary`), and prints:

  prefill   engine's own "read in X ms" — the one number that separates engine versions reliably (+/-2 ms on one build)
  dead      TTFT minus prefill — admission + slot turnaround; 21-24 ms in every build measured so far
  wall      per-request wall time, what GuideLLM's "fewer requests per window" reflects
  decode    the engine's own tok/s — flagged as NOISE, because one binary swings 74-92 from run to run

Exit 0 always; it is a report, not a gate. The bands it prints are the measured repeat spreads, not guesses.
"""
import argparse
import json
import os

NOISE = {"prefill": 0.02, "dead": 0.15, "wall": 0.05, "decode": 0.12}


def load_probe(path):
    d = json.load(open(path))
    s = d.get("summary") or d
    if "engine_prefill_ms" not in s:
        raise SystemExit(f"{path}: not a turnaround probe result (no engine_prefill_ms)")
    return dict(label=s.get("label", os.path.basename(path)), n=s.get("n"),
                prefill=s["engine_prefill_ms"], dead=s.get("dead_time_ms"),
                wall=s.get("wall_ms_per_request"), decode=s.get("engine_decode_tps"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("probe")
    ap.add_argument("--baselines", default=os.path.expanduser("~/strata-baselines.json"))
    ap.add_argument("--label")
    a = ap.parse_args()
    mine = load_probe(a.probe)
    if a.label:
        mine["label"] = a.label
    base = json.load(open(a.baselines))["versions"]

    print(f"this run: {mine['label']}  (n={mine['n']})\n")
    hdr = f"{'build':14} {'engine':7} {'prefill':>8} {'Δ':>7} {'dead':>6} {'wall':>7} {'Δ':>7} {'decode':>7}"
    print(hdr)
    print("-" * len(hdr))
    for name, v in base.items():
        p = v.get("probe") or {}
        if not p:
            continue
        dp = 100 * (mine["prefill"] / p["prefill_ms"] - 1)
        dw = 100 * (mine["wall"] / p["wall_ms"] - 1) if p.get("wall_ms") and mine.get("wall") else float("nan")
        flag = "" if dp / 100 <= NOISE["prefill"] else "  <-- slower than this baseline, outside noise"
        print(f"{name:14} {v['engine']:7} {p['prefill_ms']:8.0f} {dp:+6.1f}% {p['dead_ms']:6.0f} "
              f"{p['wall_ms']:7.0f} {dw:+6.1f}% {p['decode_median']:7.1f}{flag}")

    peers = [v["probe"] for v in base.values() if v.get("probe")]
    best = min(peers, key=lambda p: p["prefill_ms"])
    print(f"\nthis run: prefill {mine['prefill']:.0f} ms, dead {mine['dead']} ms, wall {mine['wall']:.0f} ms, "
          f"decode {mine['decode']:.1f} tok/s")
    print(f"fastest recorded prefill: {best['prefill_ms']} ms "
          f"({100*(mine['prefill']/best['prefill_ms']-1):+.1f}% for this run)")
    print(f"noise bands used: prefill ±{NOISE['prefill']*100:.0f} %, wall ±{NOISE['wall']*100:.0f} %, "
          f"dead ±{NOISE['dead']*100:.0f} %, decode ±{NOISE['decode']*100:.0f} % (a build lottery — never quote a "
          f"decode difference from one run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
