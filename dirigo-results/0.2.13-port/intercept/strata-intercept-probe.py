#!/usr/bin/env python3
"""Where a request's fixed ~330 ms goes: prompt read, conversation-cache park, or neither.

Sends unique prompts of a ladder of sizes and, for each one, reads the engine's own accounting out of its log:

    prompt N tokens = R reused + F read in X ms (Y tok/s), G generated in Z ms ...
    conversation cache: parked N tokens in X ms; ...

so three numbers can be separated per request: TTFT as the client sees it, the engine's prompt read, and the park
copy. The difference between TTFT and those two is everything else the request pays (queue, server, first-token
setup) — the term nobody has named.

    ~/bin/strata-intercept-probe.py --port 8097 --log <engine log> --out /tmp/ip.json [--label a]

Unique prompts only: an identical prefix hits the conversation cache and reports 14 000 tok/s "prefill", which
measures the cache instead of the prompt path.
"""
import argparse
import json
import os
import random
import re
import time
import urllib.request

FILLER = ("The engine streams expert weights over PCIe while the model is resident in RAM. ")
SIZES = [4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048]
RE_PROMPT = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([0-9.]+) tok/s\), "
                       r"(\d+) generated in (\d+) ms")
RE_PARK = re.compile(r"conversation cache: parked (\d+) tokens in ([0-9.]+) ms")
RE_KV = re.compile(r"KV streaming: ([0-9.]+)% of (\d+) block reads hit VRAM, ([0-9.]+) MiB read from RAM")


def prompt_of(tokens: int, salt: str) -> str:
    body = (FILLER * (tokens // 15 + 2))[: max(8, tokens * 4)]
    return salt + " " + body


def tail_since(path: str, offset: int) -> str:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", "replace")


def one(port: int, tokens: int, salt: str, log: str, wait_s: float = 60.0) -> dict:
    off = os.path.getsize(log)
    body = json.dumps({"model": "m", "max_tokens": 8, "temperature": 0.0, "stream": True,
                       "messages": [{"role": "user", "content": prompt_of(tokens, salt)}]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    first = None
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            if first is not None:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:].strip())
            except Exception:
                continue
            delta = (d.get("choices") or [{}])[0].get("delta") or {}
            # the first CONTENT token: the stream opens before the prompt is read, so the first byte is not TTFT
            if delta.get("content") or delta.get("reasoning_content"):
                first = time.time() - t0
    wall = time.time() - t0
    # the engine writes its accounting at the end of the request; poll for the prompt line
    seg = ""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        seg = tail_since(log, off)
        if RE_PROMPT.search(seg):
            break
        time.sleep(0.2)
    m = None
    for m in RE_PROMPT.finditer(seg):
        pass
    p = None
    for p in RE_PARK.finditer(seg):
        pass
    kv = None
    for kv in RE_KV.finditer(seg):
        pass
    row = dict(target=tokens, ttft_ms=None if first is None else round(first * 1000, 1),
               wall_ms=round(wall * 1000, 1))
    if m:
        row.update(prompt_tokens=int(m.group(1)), reused=int(m.group(2)), fresh=int(m.group(3)),
                   prefill_ms=int(m.group(4)), prefill_tps=float(m.group(5)),
                   decode_ms=int(m.group(7)))
    if p:
        row.update(park_tokens=int(p.group(1)), park_ms=float(p.group(2)))
    if kv:
        row.update(kv_hit_pct=float(kv.group(1)), kv_mib_from_ram=float(kv.group(3)))
    # what TTFT pays beyond the engine's own prompt read (server, queue, first-token setup).
    # The park copy is written AFTER the answer in both modes, so it is not subtracted here.
    if row.get("ttft_ms") is not None and "prefill_ms" in row:
        row["server_ms"] = round(row["ttft_ms"] - row["prefill_ms"], 1)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--log", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="arm")
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--sizes", default="", help="comma-separated ladder override, e.g. 75")
    args = ap.parse_args()
    rows = []
    # warm the server so the first request's cost is not the load
    one(args.port, 32, f"warm-{random.random():.9f}", args.log)
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()] or SIZES
    for size in sizes:
        for i in range(args.samples):
            salt = f"probe {args.label} {size} {i} {random.random():.9f}"
            r = one(args.port, size, salt, args.log)
            r["label"] = args.label
            rows.append(r)
            print(f"  {json.dumps(r)}", flush=True)
    json.dump(rows, open(args.out, "w"), indent=1)
    print(f"wrote {args.out}: {len(rows)} samples")


if __name__ == "__main__":
    main()
