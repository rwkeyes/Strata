#!/usr/bin/env python3
"""mixed-probe: does the cache router's threshold cost anything OUTSIDE the single-shot lane?

The router flips the engine's `prompt_cache` to 0 after `batch_after` consecutive never-seen single-shot prompts
and back to the shipped value when a conversational request arrives.  The question this probe answers is not
"is a single shot faster" (that is strata-shortrun-probe.py) but "does flipping the pool off throw away work a
later request would have reused":

  R1  a repeat of one prompt                      -> the repeat lane (router must keep this on)
  C1  a long conversation, turn 1                 -> full prefill
  C2  the same conversation, turn 2               -> must be a cache hit
  S   N unique single-shot prompts                -> this is what moves the router to BATCH
  C3  the same conversation, turn 3               -> if the parked context was dropped, this re-prefills
  R2  the same prompt as R1 again                 -> is R1's parked context still there?

Every step reports its streaming TTFT, so an eviction shows up as a step that jumps from ~0.3 s to seconds.
  strata-mixed-probe.py --url http://127.0.0.1:8097 --label a4 --singles 2 --out ~/mixed-a4.json
"""
import argparse, json, random, statistics as st, time, urllib.request

def post_stream(url, model, messages, cap, tools=None):
    body = {"model": model, "messages": messages, "max_tokens": cap, "temperature": 0, "stream": True,
            # thinking would spend the whole cap before any answer text: this probe measures the CACHE, and a
            # reasoning-only stream has no content piece at all (first iteration got finish=length, 0 chars)
            "chat_template_kwargs": {"enable_thinking": False}}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    t0 = time.monotonic(); ttft = None; out = []; finish = None
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            p = line[5:].strip()
            if p == "[DONE]":
                break
            try:
                d = json.loads(p)
            except json.JSONDecodeError:
                continue
            ch = (d.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            if ttft is None and (delta.get("content") or delta.get("reasoning_content")):
                ttft = time.monotonic() - t0          # first token of any kind: that is when the client sees life
            piece = delta.get("content")
            if piece:
                out.append(piece)
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    return {"ttft": round(ttft, 4) if ttft else None, "wall": round(time.monotonic() - t0, 3),
            "chars": len("".join(out)), "finish": finish, "text": "".join(out)[:120]}

def doc(seed, n_rules=120):
    rnd = random.Random(seed)
    lines = [f"RULE {i:03d}: field {rnd.choice(['alpha','beta','gamma','delta'])}{rnd.randrange(100,999)} "
             f"is {'required' if rnd.random() < .5 else 'optional'} and maps to column {rnd.randrange(1,90)}."
             for i in range(n_rules)]
    return "Handbook excerpt:\n" + "\n".join(lines)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8097")
    p.add_argument("--model", default="qwen3.8-flash-next-coder-iq1_m")
    p.add_argument("--label", required=True)
    p.add_argument("--singles", type=int, default=2, help="unique single-shot prompts inserted between C2 and C3")
    p.add_argument("--cap", type=int, default=64)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    rnd = random.Random(a.seed)
    text = doc(a.seed)
    steps = []

    def run(name, messages, tools=None):
        r = post_stream(a.url, a.model, messages, a.cap, tools)
        r["step"] = name
        steps.append(r)
        print(f"  {a.label} {name:<3} ttft {r['ttft'] if r['ttft'] is not None else -1:.3f} s  "
              f"wall {r['wall']:6.2f} s  chars {r['chars']:>3}  {r['finish']}")
        return r

    # R1 / R2: one prompt, sent twice, with everything else in between
    r_prompt = f"Summarise rule 007 of this handbook in one line.\n{text}"
    first = run("R1", [{"role": "user", "content": r_prompt}])

    # the conversation: turn 1 and 2
    conv = [{"role": "user", "content": f"Here is a handbook. Read it.\n{text}"}]
    c1 = run("C1", conv)
    conv.append({"role": "assistant", "content": c1["text"] or "Understood."})
    conv.append({"role": "user", "content": "Which rule maps to column 42?"})
    c2 = run("C2", conv)
    conv.append({"role": "assistant", "content": c2["text"] or "Not stated."})

    # the single-shot run that moves the router
    for i in range(a.singles):
        q = f"Extract the status from record R{rnd.randrange(10**6)}; owner {rnd.choice(['a','b','c'])}; " \
            f"amount {rnd.randrange(100,999)}.{rnd.randrange(10,99)} EUR; date 2024-0{rnd.randrange(1,9)}-1{rnd.randrange(0,9)}."
        run(f"S{i+1}", [{"role": "user", "content": q}])

    # C3: does the conversation's parked context survive the flip?
    conv.append({"role": "user", "content": "And which rule mentions gamma?"})
    c3 = run("C3", conv)

    # R2: is the R1 context still parked?
    run("R2", [{"role": "user", "content": r_prompt}])

    json.dump({"label": a.label, "singles": a.singles, "steps": steps}, open(a.out, "w"), indent=1)
    c2t, c3t = steps[2]["ttft"], steps[3 + a.singles]["ttft"]
    r2t = steps[-1]["ttft"]
    # absolute, not relative to C2: comparing C3 with C2 calls a warm C2 followed by a warm C3 "re-prefilled", and
    # a cold C2 followed by a warm C3 "still warm" (the a1 arm of 2026-11-09 read exactly that way)
    label = lambda t: "hit" if t is not None and t < 1.0 else "RE-PREFILL"
    print(f"  {a.label}: C2 {c2t:.3f} s [{label(c2t)}]  C3 {c3t:.3f} s [{label(c3t)}]  R2 {r2t:.3f} s [{label(r2t)}]"
          f"  after {a.singles} single shot(s)")

if __name__ == "__main__":
    main()
