#!/usr/bin/env python3
"""Has this box drifted away from its tuned baseline?  Cheap to run, cheap to trust.

The question fine calibration answers is "is the machine still what the config was tuned for".  Most of the time it
is, and 6½ minutes answers nothing.  This measures two numbers that move first — decode rate and TTFT — against the
stored baseline for this model and hardware, and only says "re-run fine" when they have moved more than a threshold.

    ~/bin/strata-drift-check.py                 # measure, compare, exit 0 (same) / 3 (drifted)
    ~/bin/strata-drift-check.py --record        # (re)record today's numbers as the baseline
    ~/bin/strata-drift-check.py --drift-pct 2   # a tighter threshold
    ~/bin/strata-drift-check.py --run-fine      # when it has drifted, run the fine calibration itself

Exit codes: 0 same (or baseline recorded), 2 the server is not answering, 3 drifted.

It refuses to measure a busy box: `GET /v1/tuning` reports requests{served,busy}, and a measurement taken while
someone else's prompt was in flight is not a measurement.  Idle means busy == 0 at the start AND `served` unchanged
across the measurement.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STATE = Path.home() / ".cache" / "strata-drift.json"
# The measurement prompt is fixed ON PURPOSE: a baseline is only comparable with the prompt it was taken on.  It is
# prose, not code, so a change in decode rate cannot be a change in how the model copies text.
PROMPT = "Explain in a few sentences why PCIe bandwidth matters for a mixture-of-experts model."
PORT = int(os.environ.get("STRATA_PORT", "8095"))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10) as r:
        return json.load(r)


def requests_now() -> dict:
    try:
        return get("/v1/tuning").get("requests") or {}
    except Exception:
        return {}


def key() -> str:
    """model + hardware: a baseline belongs to one box and one model, never to a vendor."""
    try:
        st = get("/v1/status")
    except Exception:
        return "unknown"
    hw = ""
    try:
        hw = get("/v1/tuning").get("hardware") or ""
    except Exception:
        pass
    return f"{st.get('model', '?')}@{hw or st.get('engine', '?')}"


def measure(max_tokens: int = 128, runs: int = 3) -> dict:
    """decode tok/s and TTFT, straight from the streaming response (no log parsing, no engine assumptions)."""
    body = json.dumps({"model": "m", "max_tokens": max_tokens, "stream": True, "temperature": 0.2,
                       "messages": [{"role": "user", "content": PROMPT}]}).encode()
    rates, ttfts = [], []
    attempts = runs + 1
    for _ in range(attempts):                     # the first answer after an idle spell is always slow
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
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
        if first and last and n > 3 and last > first:
            rates.append((n - 1) / (last - first))
            ttfts.append(first - t0)
    if not rates:
        return {}
    return {"tok_s": round(statistics.median(rates), 2), "ttft_s": round(statistics.median(ttfts), 2),
            "samples": len(rates), "attempts": attempts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--drift-pct", type=float, default=3.0)
    ap.add_argument("--run-fine", action="store_true")
    a = ap.parse_args()

    try:
        get("/v1/status")
    except Exception as e:
        print(f"the server is not answering on :{PORT} ({e})", file=sys.stderr)
        return 2

    before = requests_now()
    if before.get("busy"):
        print(f"busy: {before['busy']} request(s) in flight - not measuring a box someone is using", file=sys.stderr)
        return 2
    if (before.get("idle_s") or 0) < 3:
        time.sleep(3)                              # let the last request's caches settle

    k = key()
    got = measure()
    if not got:
        print("no usable samples", file=sys.stderr)
        return 2
    after = requests_now()
    mine = got.pop("attempts", 0)                  # the script's OWN requests: expected, not foreign traffic
    if before.get("served") is not None and after.get("served") is not None:
        foreign = (after["served"] - before["served"]) - mine
        if foreign:
            print(f"{foreign} other request(s) arrived during the measurement; the numbers are not comparable - "
                  "run it again when the box is quiet", file=sys.stderr)
            return 2

    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            state = {}
    base = state.get(k)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    if a.record or not base:
        state[k] = {**got, "recorded": stamp}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2))
        print(f"{'recorded' if a.record else 'no baseline for this model+hardware yet, so this becomes it'}: "
              f"{k} -> {got['tok_s']} tok/s, TTFT {got['ttft_s']} s")
        return 0

    drift = (got["tok_s"] - base["tok_s"]) / base["tok_s"] * 100
    ttft_drift = (got["ttft_s"] - base["ttft_s"]) / base["ttft_s"] * 100
    verdict = "DRIFTED" if abs(drift) >= a.drift_pct else "same"
    print(f"{k}\n  baseline {base['recorded']}: {base['tok_s']} tok/s, TTFT {base['ttft_s']} s\n"
          f"  now      {stamp}: {got['tok_s']} tok/s, TTFT {got['ttft_s']} s\n"
          f"  decode {drift:+.1f}%, TTFT {ttft_drift:+.1f}%  -> {verdict} (threshold {a.drift_pct:g}%)")

    if verdict != "DRIFTED":
        return 0
    print("\nA box that is slower than its baseline is either thermally changed, sharing the card, or on new "
          "software.  Recording a new baseline hides a regression; a fine calibration re-measures every setting:")
    if a.run_fine:
        print("running the fine calibration now (one engine load, the service stops for ~1 minute)...")
        rc = subprocess.call([str(Path.home() / "bin" / "fork-fastcal-run.sh")], env={**os.environ, "STRATA_CAL_FINE": "1"})
        if rc == 0:
            state[k] = {**got, "recorded": stamp}   # the calibration just proved these numbers
            STATE.write_text(json.dumps(state, indent=2))
        return rc
    print("  STRATA_CAL_FINE=1 tools/calibrate.py <config>      (or re-run this with --run-fine)")
    return 3


if __name__ == "__main__":
    sys.exit(main())
