#!/usr/bin/env python3
"""Where does the 30 % swing come from?  Same prompt, same settings, three engine restarts, twelve samples each.

The question this answers: is the spread BETWEEN runs (the box drifts) or WITHIN a run (the engine warms up and a
protocol that samples too early reads low)?  Every sample records the decode rate, the clock the Arc reports, and how
long the engine had been up, so the two can be told apart.

    python3 scripts/warmup-swing.py [--cycles 3] [--samples 12] [--settle 4] [--service NAME] [--port 8095]

Needs the config to be the one you want measured: nothing here changes a setting.
"""
import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serving_measure as sm  # noqa: E402


def clock_mhz() -> int | None:
    """The Arc's current GT clock, straight from sysfs (no tool, no sampling daemon)."""
    for p in sorted(Path("/sys/class/drm").glob("card*/device/gt_cur_freq_mhz")):
        try:
            return int(p.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def restart(service: str, base_url: str) -> float:
    t0 = time.time()
    subprocess.run(["systemctl", "--user", "restart", service], check=False)
    for _ in range(120):
        try:
            if sm.get(base_url, "/v1/status", timeout=3).get("loaded"):
                return time.time() - t0
        except Exception:                     # noqa: BLE001 - the server is expected to be down mid-restart
            pass
        time.sleep(2)
    raise SystemExit("the engine never reported loaded")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--samples", type=int, default=12)
    ap.add_argument("--settle", type=int, default=4, help="how many trailing samples count as settled")
    ap.add_argument("--service", default="strata-b70.service")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--prompt-file", type=Path, default=sm.default_prompt_file())
    ap.add_argument("--chars", type=int, default=sm.DEFAULT_CHARS)
    ap.add_argument("--model", default=sm.DEFAULT_MODEL)
    a = ap.parse_args()

    base = f"http://127.0.0.1:{a.port}"
    text = sm.load_prompt(a.prompt_file, a.chars)
    print(f"{'cycle':>5} {'sample':>6} {'s since load':>13} {'tok/s':>7} {'clock MHz':>9}")
    per_cycle = []
    for c in range(1, a.cycles + 1):
        load = restart(a.service, base)
        print(f"  (cycle {c}: engine loaded in {load:.0f} s)")
        t_load = time.time()
        rates = []
        for i in range(1, a.samples + 1):
            r = sm.stream_rate(base, text, model=a.model)
            rates.append(r)
            print(f"{c:>5} {i:>6} {time.time() - t_load:>13.1f} {r:>7.2f} {clock_mhz() or 0:>9}")
        per_cycle.append(rates)

    print()
    firsts = [r[0] for r in per_cycle]
    lasts = [statistics.median(r[-a.settle:]) for r in per_cycle]
    print(f"first sample of each cycle: {['%.1f' % x for x in firsts]}")
    print(f"settled (median of the last {a.settle}): {['%.1f' % x for x in lasts]}")
    if min(firsts) > 0:
        print(f"within-run warm-up: {100 * (statistics.median(lasts) / statistics.median(firsts) - 1):+.1f}% "
              f"(settled vs first sample)")
    if min(lasts) > 0:
        print(f"between-run spread:  {100 * (max(lasts) / min(lasts) - 1):.1f}% (settled across cycles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
