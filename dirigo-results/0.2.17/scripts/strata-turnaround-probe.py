#!/usr/bin/env python3
"""strata-turnaround-probe.py - measure per-request deadline overhead, not throughput.

Why this exists: GuideLLM's 45 s windows are too noisy on this box to resolve a 5-9 % difference
(the same 0.2.12 build read 6,433 and 7,859 ms at c=4 an hour apart). This probe sends a FIXED
number of sequential requests and reports, per request, how much wall time is spent OUTSIDE the
engine's own prefill + decode - the admission / slot-turnaround cost. That is the quantity the
0.1.58 -> 0.1.63 comparison is about, and it is stable because every request is the same shape
and nothing runs concurrently.

Per request:  ~512-token unique prompt, 128 output tokens, streamed so the first chunk is timed.
Dead time  :  time-to-first-token minus the engine's own "read in X ms" for that prompt.
Order      :  the engine prints one "strata serve: prompt ..." line per request, in order, so the
              last N such lines pair with the N requests this probe sent.

Usage: strata-turnaround-probe.py --url http://127.0.0.1:8097 --engine-log PATH [--n 16] [--label x]
Exit 0 on success; prints one JSON line and a human summary.
"""
import argparse, json, statistics, sys, time, urllib.request

FILLER = ("The following is an excerpt from the maintenance manual of an industrial pressure gauge. "
          "Record the serial number, the calibration date and the certificate reference for each unit. ")


def prompt_for(i: int, target_tokens: int = 512) -> str:
    # ~4 chars per token for this kind of prose; unique first line so no two prompts share a prefix
    head = f"Batch {i} of the calibration review, unit serial SN-{7000 + i}:\n"
    body = FILLER * max(1, target_tokens // 30)
    return head + body + "\nIn one short sentence, say what the certificate reference field is for."


def one_request(url: str, model: str, prompt: str, max_tokens: int = 128):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "stream": True}).encode()
    req = urllib.request.Request(url + "/v1/chat/completions", data=body,
                                headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    ttft = None
    first_kind = None
    content_chars = 0
    think_chars = 0
    tokens = 0
    usage = None
    finish = None
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                d = json.loads(payload)
            except Exception:
                continue
            if "usage" in d and d["usage"]:
                usage = d["usage"]
            for ch in d.get("choices") or []:
                delta = ch.get("delta") or {}
                piece = delta.get("content")
                think = delta.get("reasoning_content") or delta.get("reasoning")
                if (piece or think) and ttft is None:
                    # the FIRST token the engine emits may be a reasoning token: that is what the engine's
                    # own prefill is followed by, so it is the honest end of prefill
                    ttft = (time.monotonic() - t0) * 1000.0
                    first_kind = "content" if piece else "reasoning"
                if piece:
                    content_chars += len(piece)
                if think:
                    think_chars += len(think)
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
            tokens += 1
    wall = (time.monotonic() - t0) * 1000.0
    return dict(ttft_ms=ttft, wall_ms=wall, chunks=tokens, usage=usage, finish=finish,
                first=first_kind, content_chars=content_chars, think_chars=think_chars)


def engine_prefills(path: str, n: int):
    """The last n 'strata serve: prompt ... read in X ms ... generated in Y ms' tuples."""
    import re
    pat = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\), "
                     r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\)")
    out = []
    with open(path, errors="replace") as f:
        for line in f:
            m = pat.search(line)
            if m:
                out.append(dict(prompt=int(m.group(1)), reused=int(m.group(2)), read=int(m.group(4)),
                                prefill_tps=float(m.group(5)), gen=int(m.group(6)),
                                decode_ms=int(m.group(7)), decode_tps=float(m.group(8))))
    return out[-n:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", default="qwen3.8-flash-next-coder-iq1_m")
    ap.add_argument("--engine-log", required=True)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--label", default="probe")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    # one throwaway request so the first timed request is not paying session start-up
    one_request(a.url, a.model, prompt_for(9999), max_tokens=8)
    time.sleep(1.0)

    rows = []
    for i in range(a.n):
        r = one_request(a.url, a.model, prompt_for(i))
        rows.append(r)
        print(f"  {i+1:2d}/{a.n} ttft {r['ttft_ms'] or 0:6.0f} ms  wall {r['wall_ms']:6.0f} ms  "
              f"finish={r['finish']}", flush=True)

    eng = engine_prefills(a.engine_log, a.n)
    paired = list(zip(rows, eng))
    if len(eng) != len(rows):
        print(f"  WARNING: {len(rows)} requests but {len(eng)} engine prefill lines - dead time is unreliable")
    dead = [r["ttft_ms"] - e["read"] for r, e in paired if r["ttft_ms"]]
    wall_per_req = [r["wall_ms"] for r in rows]
    dec = [e["decode_tps"] for e in eng]
    pre = [e["read"] for e in eng]
    res = dict(label=a.label, n=a.n,
               ttft_ms=round(statistics.median(r["ttft_ms"] for r in rows if r["ttft_ms"])),
               wall_ms_per_request=round(statistics.median(wall_per_req)),
               requests_per_min=round(60000.0 / statistics.median(wall_per_req), 2),
               engine_prefill_ms=round(statistics.median(pre)) if pre else None,
               engine_decode_tps=round(statistics.median(dec), 1) if dec else None,
               dead_time_ms=round(statistics.median(dead)) if dead else None,
               dead_time_pairs=len(dead),
               first_token_kinds=sorted({r["first"] for r in rows if r["first"]}),
               reused_prompts=sum(1 for e in eng if e["reused"] > 32))
    print("\nRESULT " + json.dumps(res))
    if a.out:
        json.dump(dict(summary=res, rows=rows, engine=eng), open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
