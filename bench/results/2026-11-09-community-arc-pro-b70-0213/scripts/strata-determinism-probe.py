#!/usr/bin/env python3
"""determinism-probe: does the same greedy prompt give the same answer, run after run?

0.2.13's notes claim Intel Arc now returns identical answers (their bf16 prompt products moved off oneMKL onto a
fixed-order kernel; "50 of 50 identical on an A750, against 3-5 different in 5 runs before").  This measures that
on THIS card: N identical requests, temperature 0, fixed seed, thinking off so the answer is short and comparable,
and it counts distinct answers per prompt.

  determinism-probe.py --url http://127.0.0.1:8097 --label 0213 --runs 10 --out ~/det-0213.json
"""
import argparse, hashlib, json, time, urllib.request

PROMPTS = {
    "code": "Write a Python function word_freq(text) returning a dict of word counts, lower-cased, punctuation "
            "stripped. Return only the code.",
    "prose": "Explain in about 80 words why a B-tree keeps its height logarithmic under random insertions.",
    "extract": "Invoice 8841 dated 2 March 2024, total 5,120.75 EUR. Reply with only the ISO date (YYYY-MM-DD).",
}

# the bf16 products 0.2.13 moved to a fixed-order kernel are in the PROMPT path: a long prompt is where a per-launch
# difference in oneMKL's result would change the answer.  Added 2026-11-09 after the short prompts came back 1/10
# distinct on BOTH engines.
def long_prompt():
    code = "\n".join(f"export function rule{i}(x) {{ return x === {i} ? x + {i+1} : x - {i}; }}" for i in range(140))
    return ("Review this source and name the exact value rule77 returns for x=77, then for x=78. "
            "Answer with the two numbers only.\n" + code)


PROMPTS["long"] = None      # filled in main()

def ask(url, model, prompt, cap):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": cap, "temperature": 0, "top_p": 1, "top_k": 1, "min_p": 0, "seed": 1234,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    c = d["choices"][0]
    text = c["message"].get("content") or ""
    return {"text": text, "sha": hashlib.sha256(text.encode()).hexdigest()[:12],
            "chars": len(text), "wall": round(time.monotonic() - t, 2),
            "finish": c["finish_reason"], "tokens": (d.get("usage") or {}).get("completion_tokens")}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8097")
    p.add_argument("--model", default="qwen3.8-flash-next-coder-iq1_m")
    p.add_argument("--label", required=True)
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--cap", type=int, default=220)
    p.add_argument("--only", default="", help="comma-separated prompt names")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    out = {"label": a.label, "runs": a.runs, "prompts": {}}
    PROMPTS["long"] = long_prompt()
    for name, prompt in PROMPTS.items():
        if a.only and name not in a.only.split(","):
            continue
        rows = [ask(a.url, a.model, prompt, a.cap) for _ in range(a.runs)]
        shas = [r["sha"] for r in rows]
        uniq = sorted(set(shas))
        out["prompts"][name] = {"distinct": len(uniq), "shas": {s: shas.count(s) for s in uniq},
                                "chars": [r["chars"] for r in rows],
                                "walls": [r["wall"] for r in rows], "answers": rows[:3]}
        print(f"  {a.label} {name:<8} distinct answers: {len(uniq)}/{a.runs}   sizes {sorted(set(r['chars'] for r in rows))}")
    json.dump(out, open(a.out, "w"), indent=1)

if __name__ == "__main__":
    main()
