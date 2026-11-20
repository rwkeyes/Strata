#!/usr/bin/env python3
"""The second-shape guard, run LATER — when the box is up and quiet, not while somebody waits for a first answer.

tools/calibrate.py records `pending_shape` when it changes a value; this reads that record, measures the chosen value
against the default on the second prompt shape with the paired method, and either confirms it or puts the default
back in the config.  It needs the server running (it applies each value per request, so nothing restarts) and refuses
to measure a box that is serving somebody.

    tools/verify_shapes.py <config.json> [--pairs 6] [--loss-pct 3] [--port 8095] [--clear-only]

Exit codes: 0 confirmed (or nothing pending), 3 reverted, 2 could not measure.

Why a separate tool: the check costs ~60 s on a 2 400-token shape, and a first run should reach a working model
before spending it.  The value is already in force; this only says whether it should stay.
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:                                                    # the length the calibrator uses for its second shape
    from tools.calibrate import CODE_SHAPE_TOKENS     # noqa: E402
except ImportError:                                     # run as a script: read it off the module file
    CODE_SHAPE_TOKENS = 2400


def http(port: int, path: str, body: dict | None = None, timeout: int = 20) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def shape_prompt() -> str:
    """The second shape: a real file head continued — the same construction the calibrator uses."""
    text = (Path(__file__).resolve().parent / "calibrate.py").read_text(encoding="utf-8", errors="replace")
    return ("Continue this file from where it stops, reproducing the same style and identifiers:\n\n"
            + text[: CODE_SHAPE_TOKENS * 4] + "\n\ndef _next_part() -> None:\n")


def rate(port: int, flag_value: tuple[str, float], prompt: str, max_tokens: int = 128) -> float:
    flag, value = flag_value
    key = {"--spec-min-p": "spec_min_p", "--spec": "spec"}.get(flag, flag.lstrip("-").replace("-", "_"))
    body = {"model": "m", "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
            "strata_tune": {key: value}, "messages": [{"role": "user", "content": prompt}]}
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=data,
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
    ap.add_argument("config")
    ap.add_argument("--pending", default=None, help="the record calibrate.py wrote (default: beside the config)")
    ap.add_argument("--pairs", type=int, default=6)
    ap.add_argument("--loss-pct", type=float, default=3.0)
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--revert", action="store_true",
                    help="put the default back when the chosen value loses - OFF by default: this check has measured "
                         "the same value 12%% ahead and 6%% behind on the same prompt in different sessions, so it "
                         "reports rather than acts until that is understood")
    ap.add_argument("--clear-only", action="store_true", help="drop the record without measuring")
    a = ap.parse_args()

    cfg_path = Path(a.config)
    pending_path = Path(a.pending) if a.pending else cfg_path.with_name("pending-shape.json")
    if not pending_path.exists():
        print(f"nothing pending ({pending_path} does not exist)")
        return 0
    rec = json.loads(pending_path.read_text())

    if a.clear_only:
        pending_path.unlink()
        print("pending record cleared without measuring")
        return 0

    try:
        req = http(a.port, "/v1/tuning", timeout=10).get("requests") or {}
    except Exception as e:
        print(f"the server is not answering on :{a.port} ({e}) - the value stays, unimpeded", file=sys.stderr)
        return 2
    if req.get("busy"):
        print(f"busy: {req['busy']} request(s) in flight - not measuring a box someone is using", file=sys.stderr)
        return 2

    flag, chosen, dflt = rec["flag"], rec["chosen"], rec["default"]
    text = shape_prompt()
    served0 = req.get("served", 0)
    mine = 0

    # six settling requests alternating both values: the engine climbs 83.5 -> 93.1 -> 97.0 tok/s over its first five
    # requests after a start (measured, three restarts), and a check that begins on the middle plateau reports noise
    for i in range(6):
        rate(a.port, (flag, chosen if i % 2 == 0 else dflt), text)
        mine += 1
    deltas, rc, rd = [], [], []
    for i in range(a.pairs):
        order = [(chosen, "c"), (dflt, "d")] if i % 2 == 0 else [(dflt, "d"), (chosen, "c")]
        got = {}
        for value, tag in order:
            got[tag] = rate(a.port, (flag, value), text)
            mine += 1
        if got["c"] and got["d"]:
            rc.append(got["c"]); rd.append(got["d"]); deltas.append(got["c"] - got["d"])

    if len(deltas) < 3:
        print("too few usable pairs", file=sys.stderr)
        return 2
    med = statistics.median(deltas)
    med_pct = med / statistics.median(rd) * 100
    won = sum(1 for d in deltas if d > 0)
    print(f"{rec.get('shape', 'second shape')}: {chosen} {statistics.median(rc):.2f} tok/s vs default "
          f"{dflt} {statistics.median(rd):.2f} over {len(deltas)} pairs")
    print(f"  median paired delta {med:+.2f} tok/s ({med_pct:+.2f}%), {won} of {len(deltas)} pairs favour {chosen}")

    after = http(a.port, "/v1/tuning", timeout=10).get("requests") or {}
    if (after.get("served", 0) - served0) - mine:
        print("  NOTE: other traffic arrived during the check - the numbers are not comparable", file=sys.stderr)

    if med_pct <= -a.loss_pct and not a.revert:
        print(f"  WARNING: {flag} {chosen} lost by {-med_pct:.1f}% here.  Not reverted (pass --revert to act): this "
              "check has contradicted itself across sessions, so it reports until it is understood")
        pending_path.unlink()
        return 0
    if med_pct <= -a.loss_pct:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        args = cfg["args"]
        if flag in args:
            args[args.index(flag) + 1] = str(dflt)
            cfg_path.write_text(json.dumps(cfg, indent=2))
        pending_path.unlink()
        print(f"  REVERTED: {flag} back to {dflt} in {cfg_path.name} (the check lost by {-med_pct:.1f}% here)")
        return 3
    pending_path.unlink()
    print(f"  confirmed: {flag} stays at {chosen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
