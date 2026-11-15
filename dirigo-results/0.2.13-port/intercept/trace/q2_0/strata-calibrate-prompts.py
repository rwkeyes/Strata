#!/usr/bin/env python3
"""Send a small varied corpus to a live server so the engine dumps the routing it needs for a profile.

tools/collect_profile_traces.py starts the engine once PER prompt (20-60 s each, and ~185 s of profile fill for the Q2_0
pack). This sends the same kinds of text to ONE running server with --dump-routing, which writes the same record format
that tools/make_profile_mix.py reads — so a profile costs one startup, not forty-six.

    ~/bin/strata-calibrate-prompts.py --url http://127.0.0.1:8097 [--max-new 120]
"""
import argparse
import json
import time
import urllib.request

CORPUS = [
    ("code", "Write a Python function that merges two sorted lists without using sorted(). Include a docstring and "
             "three assertions that exercise the empty, equal-length and unequal-length cases."),
    ("code", "Refactor this into idiomatic Go: a loop that reads lines from a file, parses each as JSON, and appends "
             "the ones with an 'id' field to a slice. Explain the error handling you chose."),
    ("code", "Explain what this shell pipeline does and where it fails on filenames with newlines: "
             "find . -name '*.log' -print0 | xargs -0 grep -l ERROR | sort | uniq -c"),
    ("chat", "I am planning a three-week trip to Japan in April with two teenagers. Suggest an itinerary that balances "
             "cities and nature, and say what I should book early."),
    ("chat", "My laptop fan runs constantly since an update last week. Walk me through what to check, in order, without "
             "telling me to reinstall the operating system."),
    ("math", "A tank fills in 6 hours with pipe A and 9 hours with pipe B. Both run for two hours, then A is closed. "
             "How long does B take to finish? Show each step."),
    ("math", "Prove that the sum of the first n odd numbers is n squared, then use it to compute 1+3+...+999."),
    ("summary", "Summarise in five bullet points: a release note describing a new cache that stores parked conversations "
                "in RAM, a flag that switches it off for one-shot traffic, measurements of 5.3 s to 0.29 s on repeats, "
                "and a caveat that a wrong switch costs one full prefill."),
    ("summary", "Extract the dates, owners and deadlines from: 'Marta will finish the migration by 3 March. The audit "
                "starts 10 February and is owned by Priya. Retest on 28 February, before the freeze.'"),
    ("writing", "Rewrite this sentence three ways, one formal, one plain, one for a child: 'Subsequent to the completion "
                "of the review, the team will initiate the deployment process.'"),
    ("science", "Why does a metal spoon feel colder than a wooden one at the same room temperature? Answer with the "
                "mechanism, not the analogy."),
    ("tool", "Call the weather tool for Seattle tomorrow, then, if it will rain, call the calendar tool to move my 9 am "
             "run to the same time on Friday."),
]


def ask(url, prompt, max_new):
    body = json.dumps({"model": "m", "max_tokens": max_new, "temperature": 0.0,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    text = msg.get("content") or msg.get("reasoning_content") or ""
    return time.time() - t0, len(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8097")
    ap.add_argument("--max-new", type=int, default=120)
    args = ap.parse_args()
    total = 0
    for i, (kind, prompt) in enumerate(CORPUS):
        wall, chars = ask(args.url, prompt, args.max_new)
        total += chars
        print(f"  {i+1:>2}/{len(CORPUS)} {kind:<8} {wall:5.1f} s  {chars:>4} chars", flush=True)
    print(f"corpus done: {len(CORPUS)} prompts, {total} characters of output routed")


if __name__ == "__main__":
    main()
