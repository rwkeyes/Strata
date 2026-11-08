#!/usr/bin/env python3
"""Does a per-request `spec_min_p` help CODE prompts more than PROSE prompts?  Paired, on one loaded engine.

The engine accepts `spec_min_p` as a PER-REQUEST key (`src/program/generate.cpp`, "req_spec_min_p"), so this
needs no restart per value: one engine, every prompt measured at every value, paired by prompt.  That is the
only honest shape for a knob whose effect the skill already records as prompt-dependent (the calibrator's code
shape read 0.7 as 2.7% slower while the serving prompt read it 12% faster).

Timings come from the engine's own completion line, not from the client:
    prompt N tokens = R reused + F read in P ms (X tok/s), G generated in D ms (Y tok/s)
so the decode column is the engine's accounting.  Judge on DECODE (speculation), and note that prefill for the
same prompt is served from the conversation cache after its first request - that is why decode is the lane.

    .venv/bin/python strata-codetune-probe.py --model qwen3.8-flash-next-coder-iq1_m \\
        --engine-log <tree>/strata-coder-iq1_m-sycl-tuned.log --out ~/strata-codetune-probe.json

UNDO: rm ~/bin/strata-codetune-probe.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
import urllib.request
from pathlib import Path

PATTERN = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([0-9.]+) tok/s\), "
                     r"(\d+) generated in (\d+) ms \(([0-9.]+) tok/s\)")
FIELDS = ("prompt_tokens", "reused", "fresh", "prefill_ms", "prefill_tps", "generated", "decode_ms", "decode_tps")

CODE = [
    "Write a Python function that returns the k largest elements of a list, with type hints and a docstring.",
    "Refactor this handler so the file is closed even when read raises:\n\n"
    "```python\ndef load(p):\n    f = open(p)\n    return f.read()\n```",
    "Fix the failing test and say in one sentence what the bug was:\n\n"
    "```python\ndef test_sum():\n    assert add(2, 2) == 5\n```",
    "Write a bash one-liner that lists the ten largest .gguf files under /data with their sizes.",
    "Implement a token-bucket rate limiter class in Python and a test for it.",
    "Why does this query return duplicates? SELECT a.id FROM a JOIN b ON a.id = b.id -- one row per id expected",
]
EXTRACT = [
    "In the memo below, reply with the invoice number only.\n\nMemo: INV-4471 was issued on Tuesday; the total is 1 240 dollars.",
    "Extract the email address from this text and return only it: contact sam at someone@example.com before Friday.",
    "Reply with yes or no: is 2^31 - 1 prime?",
    "Convert this JSON to YAML and output nothing else: {\"build\": \"green\", \"tests\": 56}",
    "Translate this sentence into French, only the translation: the build is green and the tests pass.",
    "What is the value of timeout_s in this config? Reply with the number only.\n\n[server]\nport = 8095\ntimeout_s = 45\nretries = 3",
    "Reply with only the port number from this line: the server listens on 127.0.0.1:8095.",
    "Extract the date and output it as ISO 8601 only: published on Tuesday, 4 November 2026.",
    "Answer with a single word, positive or negative: is 91 divisible by 7?",
    "Return only the number of tests that failed: 56 tests ran, 2 failed, 54 passed.",
    "Translate into German, output only the translation: the card is idle.",
    "Reply with the three-letter extension of file 'archive.tar.gz' and nothing else.",
    "Convert to a JSON object only: name=buildbox, gpu=B70, port=8095.",
    "Reply with yes or no: does the string 'abc' contain the letter 'd'?",
    "Extract the version string only: engine 0.1.56 was built on 4 November.",
    "Output only the sum: 17 + 25 + 8.",
    "Reply with the last word of this sentence, nothing else: the logs are clean today.",
    "Return only the key names in this config, comma separated: port=8095, timeout=45, retries=3.",
    "Reply with the capital of Portugal and nothing else.",
    "Extract the SHA prefix only: commit 67cc893e fixed the policy.",
]
COPY = [
    "Rewrite the paragraph below in the same structure, changing every mention of \"buildbox\" to \"the build box\":\n\n"
    "buildbox serves the engine on :8095. buildbox runs a B70 and a 7700X. buildbox keeps its packs on a USB volume. "
    "When buildbox reboots the unit waits for that volume.",
    "Translate the passage below into French, keeping the line breaks:\n\nthe build is green\nthe tests pass\n"
    "the card is idle\nthe logs are clean",
    "Convert this YAML to JSON exactly, keys and all:\n\nserver:\n  port: 8095\n  timeout_s: 45\n  retries: 3",
    "Apply this patch by returning the whole file with the change made:\n\n```diff\n-    return x + 1\n+    return x - 1\n```\n\n"
    "```python\ndef step(x):\n    return x + 1\n```",
    "Copy the table below and add one row for 2026 with the same columns:\n\n| year | builds | failures |\n|---|---|---|\n"
    "| 2024 | 41 | 2 |\n| 2025 | 63 | 1 |",
    "Restate the rules below as a numbered list, same wording, no additions:\n\nOne request runs at a time. "
    "The card holds one model. A stop must free VRAM before a start. A failed start is not retried on a timer.",
]
PROSE = [
    "Explain why the sky is blue, in one paragraph.",
    "Summarize the trade-offs between consistency and availability in distributed systems.",
    "Write a short email telling a customer their refund will take five business days.",
    "Which is more persuasive in an argument, a story or a statistic, and why?",
    "Plan a five-day trip to Lisbon focused on food markets and walking.",
    "Why do some people prefer analog photography when digital is cheaper and more convenient?",
]


def one(url, model, text, tune, log_path, key="spec_min_p", effort="none", max_tokens=128, timeout=360):
    """One greedy request; returns the engine's own timing line as a dict."""
    offset = log_path.stat().st_size
    body = {"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0, "top_k": 1, "top_p": 1, "min_p": 0, "seed": 42}
    if effort != "auto":
        body["reasoning_effort"] = effort
    if tune is not None:
        body["strata_tune"] = {key: (float(tune) if key != "spec" else int(tune))}
    headers = {"Content-Type": "application/json"}
    if os.environ.get("STRATA_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["STRATA_API_KEY"]
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    wall = time.monotonic() - t0
    deadline = time.monotonic() + 2
    matches = []
    while True:
        with log_path.open("rb") as fh:
            fh.seek(offset)
            matches = PATTERN.findall(fh.read().decode(errors="replace"))
        if matches or time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    if len(matches) != 1:
        raise RuntimeError(f"expected one engine timing line, saw {len(matches)} (idle server? wrong log?)")
    metrics = dict(zip(FIELDS, map(float, matches[0])))
    metrics["wall_s"] = round(wall, 2)
    choice = data["choices"][0]
    metrics["finish"] = choice.get("finish_reason")
    msg = choice.get("message") or {}
    metrics["content"] = (msg.get("content") or "")[:400]
    metrics["reasoning"] = (msg.get("reasoning_content") or "")[:200]
    return metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8097")
    ap.add_argument("--model", required=True)
    ap.add_argument("--engine-log", type=Path, required=True)
    ap.add_argument("--key", default="spec_min_p", help="the per-request key to sweep (spec_min_p | spec)")
    ap.add_argument("--family", default="serving", choices=["serving", "extract", "copy"],
                    help="serving: 6 code + 6 prose; extract: 6 short-direct-answer prompts")
    ap.add_argument("--effort", default="none", choices=["none", "auto"],
                    help="none: send reasoning_effort none (the old behaviour); auto: send none - let the server's "
                         "thinking policy decide")
    ap.add_argument("--values", default="0.3,0.5,0.7,0.9")
    ap.add_argument("--served", type=float, default=None, help="the value the deployment currently uses")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=128, help="answer budget per request (default 128)")
    ap.add_argument("--out", type=Path, default=Path.home() / "strata-codetune-probe.json")
    a = ap.parse_args()
    cast = int if a.key in ("spec", "lookup") else float
    values = [None] if a.values.strip().lower() in ("none", "", "-") else [None] + [cast(v) for v in a.values.split(",")]
    if a.max_tokens != 128:
        print(f"max_tokens {a.max_tokens}", flush=True)
    if a.served is None:
        a.served = cast(6) if a.key == "spec" else 0.7
    print(f"sweeping {a.key}: values {values} (None = the engine's own setting)", flush=True)
    rows = []
    for rnd in range(1, a.rounds + 1):
        for value in values:
            # the code and prose prompts alternate inside each value, so a thermal/cache drift cannot favour one
            families = {"serving": (("code", CODE), ("prose", PROSE)),
                        "extract": (("extract", EXTRACT),),
                        "copy": (("copy", COPY),)}[a.family]
            for kind, prompts in families:
                for i, text in enumerate(prompts, 1):
                    try:
                        m = one(a.url, a.model, text, value, a.engine_log, a.key, a.effort, a.max_tokens)
                    except Exception as e:                       # noqa: BLE001 - a failed request is a row, not a crash
                        rows.append({"round": rnd, "value": value, "kind": kind, "prompt": i, "error": str(e)})
                        print(f"  round {rnd} value {value} {kind}#{i}: ERROR {e}", flush=True)
                        continue
                    rows.append({"round": rnd, "value": value, "kind": kind, "prompt": i, **m})
                    print(f"  round {rnd} value {value} {kind}#{i}: decode {m['decode_tps']:.1f} "
                          f"prefill {m['prefill_tps']:.0f} gen {m['generated']:.0f}", flush=True)
    a.out.write_text(json.dumps(rows, indent=1))
    print(f"\nwrote {a.out} ({len(rows)} rows)")

    # per kind and value: median decode, and the paired win rate against the served value
    by = {}
    for r in rows:
        if "error" in r:
            continue
        by.setdefault((r["kind"], r["value"]), []).append(r["decode_tps"])
    print("\n== decode tok/s, median of all rows (engine's own line)\n")
    header = "kind  " + "".join(f"{'native' if v is None else v:>9}" for v in values)
    print(header)
    kinds_seen = sorted({k for k in {r["kind"] for r in rows if "error" not in r}})
    for kind in kinds_seen:
        cells = []
        for v in values:
            vals = by.get((kind, v), [])
            cells.append(f"{statistics.median(vals):>9.1f}" if vals else f"{'-':>9}")
        print(f"{kind:<6}" + "".join(cells))

    print("\n== paired by prompt: the best value against the served one\n")
    pairs = {}
    for r in rows:
        if "error" in r:
            continue
        pairs.setdefault((r["kind"], r["prompt"], r["round"]), {})[r["value"]] = r["decode_tps"]
    for kind in kinds_seen:
        for v in values:
            if v is None or v == a.served:
                continue
            deltas, wins = [], 0
            for (k, p, rnd), d in pairs.items():
                if k != kind or v not in d or a.served not in d:
                    continue
                deltas.append(100.0 * (d[v] - d[a.served]) / d[a.served])
                wins += d[v] > d[a.served]
            if deltas:
                print(f"  {kind:<6} {a.key}={v:<4} vs {a.served:g}: median {statistics.median(deltas):+6.1f} % "
                      f"(range {min(deltas):+6.1f}..{max(deltas):+6.1f}), wins {wins}/{len(deltas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
