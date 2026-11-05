#!/usr/bin/env python3
"""Where does the 30 % swing come from?  Same prompt, same settings, three engine restarts, twelve samples each.

The question this answers: is the spread BETWEEN runs (the box drifts) or WITHIN a run (the engine warms up and a
protocol that samples too early reads low)?  Every sample records the decode rate, the clock the Arc reports, and how
long the engine had been up, so the two can be told apart.

    ~/bin/warmup-swing.py [--cycles 3] [--samples 12] [--gap 3]

Needs the config to be the one you want measured: nothing here changes a setting.
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROMPT_FILE = Path("/home/user/strata-fork-sync/tools/calibrate.py")
SERVICE = "strata-b70.service"
PORT = 8095


def prompt() -> str:
    return ("Continue this file from where it stops, reproducing the same style and identifiers:\n\n"
            + PROMPT_FILE.read_text(encoding="utf-8", errors="replace")[:9000] + "\n\ndef _next_part() -> None:\n")


def clock_mhz() -> int | None:
    """The Arc's current GT clock, straight from sysfs (no tool, no sampling daemon)."""
    for p in sorted(Path("/sys/class/drm").glob("card*/device/gt_cur_freq_mhz")):
        try:
            return int(p.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def one(text: str, max_tokens: int = 128) -> float:
    body = json.dumps({"model": "m", "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
                       "messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
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


def restart() -> float:
    t0 = time.time()
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=False)
    for _ in range(120):
        try:
            s = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/status", timeout=3))
            if s.get("loaded"):
                return time.time() - t0
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit("the engine never reported loaded")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--samples", type=int, default=12)
    a = ap.parse_args()
    text = prompt()
    print(f"{'cycle':>5} {'sample':>6} {'s since load':>13} {'tok/s':>7} {'clock MHz':>9}")
    per_cycle = []
    for c in range(1, a.cycles + 1):
        load = restart()
        print(f"  (cycle {c}: engine loaded in {load:.0f} s)")
        t_load = time.time()
        rates = []
        for i in range(1, a.samples + 1):
            r = one(text)
            rates.append(r)
            print(f"{c:>5} {i:>6} {time.time() - t_load:>13.1f} {r:>7.2f} {clock_mhz() or 0:>9}")
        per_cycle.append(rates)
    print()
    firsts = [r[0] for r in per_cycle]
    lasts = [statistics.median(r[-4:]) for r in per_cycle]
    print(f"first sample of each cycle: {['%.1f' % x for x in firsts]}")
    print(f"settled (median of the last 4): {['%.1f' % x for x in lasts]}")
    if min(firsts) > 0:
        print(f"within-run warm-up: {100 * (statistics.median(lasts) / statistics.median(firsts) - 1):+.1f}% "
              f"(settled vs first sample)")
    if min(lasts) > 0:
        print(f"between-run spread:  {100 * (max(lasts) / min(lasts) - 1):.1f}% (settled across cycles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
