#!/usr/bin/env python3
"""Split the per-request fixed cost using the engine's own STRATA_TRACE admission line.

The engine prints, per admitted request:

  strata trace: admission of slot 0: drain 0.0, prompt 402.1 (read 388.0, windows 11.2, 1 checkpoint(s) 2.1 =
    sync 1.0 + save 0.8 + evict 0.3, rest 0.0), first window 0.0, slot copy 0.4, slot freed 0.0, checkpoint copy 0.1

followed by the prompt line this probe already keys on. Pairing them by order gives the phases that make up the
"fixed cost": what is spent before the prompt read (drain + slot work), what the read itself costs, and what follows.

    ~/bin/strata-trace-split.py --log <engine log> [--since-bytes N] [--json out.json]

Use --since-bytes <size of the log before the run> to ignore earlier requests, or just parse the whole log and read
the tail of the table (the run's own requests are the last ones).
"""
import argparse
import json
import re
import statistics as st

RE_TRACE = re.compile(
    r"strata trace: admission of slot (\d+): drain ([\d.]+), prompt ([\d.]+) "
    r"\(read ([\d.]+), windows ([\d.]+), (\d+) checkpoint\(s\) ([\d.]+) = sync ([\d.]+) \+ save ([\d.]+) \+ "
    r"evict ([\d.]+), rest ([\d.]+)\), first window ([\d.]+), slot copy ([\d.]+), slot freed ([\d.]+), "
    r"checkpoint copy ([\d.]+)")
RE_PROMPT = re.compile(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms")
PHASES = ("drain", "prompt", "read", "windows", "ckpts", "sync", "save", "evict", "rest",
          "first_window", "slot_copy", "slot_freed", "ckpt_copy")


def parse(path, since=0):
    with open(path, "rb") as f:
        f.seek(since)
        text = f.read().decode("utf-8", "replace")
    rows, pending = [], None
    for line in text.splitlines():
        m = RE_TRACE.search(line)
        if m:
            g = m.groups()
            pending = dict(slot=int(g[0]), prompt_ms=float(g[2]), read=float(g[3]), windows=float(g[4]),
                           ckpts=int(g[5]), ckpt_ms=float(g[6]), sync=float(g[7]), save=float(g[8]),
                           evict=float(g[9]), rest=float(g[10]), first_window=float(g[11]),
                           slot_copy=float(g[12]), slot_freed=float(g[13]), ckpt_copy=float(g[14]))
            pending["drain"] = float(g[1])
            continue
        p = RE_PROMPT.search(line)
        if p and pending is not None:
            pending.update(prompt_tokens=int(p.group(1)), reused=int(p.group(2)), read_n=int(p.group(3)),
                           engine_prefill_ms=float(p.group(4)))
            rows.append(pending)
            pending = None
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--since-bytes", type=int, default=0)
    ap.add_argument("--json")
    args = ap.parse_args()
    rows = parse(args.log, args.since_bytes)
    if not rows:
        print("no trace admission lines paired with a prompt line - was STRATA_TRACE set for the ENGINE process?")
        return
    print(f"{len(rows)} request(s) traced\n")
    hdr = f"{'tokens':>7} {'reused':>7} {'prefill':>8} " + " ".join(f"{p:>11}" for p in PHASES)
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['prompt_tokens']:>7} {r['reused']:>7} {r['engine_prefill_ms']:>8.0f} " +
              " ".join(f"{r[p]:>11.1f}" for p in PHASES))
    # per-size medians, and the share of the fixed cost each phase owns
    print("\nper-size median of the admission phases (ms), for requests that read their prompt (reused == 0):")
    for size in sorted({r["prompt_tokens"] for r in rows}):
        grp = [r for r in rows if r["prompt_tokens"] == size and r["reused"] == 0]
        if len(grp) < 2:
            continue
        med = {p: st.median([r[p] for r in grp]) for p in PHASES}
        med["n"] = len(grp)
        print(f"  {size:>6} tok (n={med['n']}): " +
              "  ".join(f"{p} {med[p]:.1f}" for p in ("drain", "slot_copy", "slot_freed", "read", "windows",
                                                      "ckpts", "rest", "first_window", "ckpt_copy", "prompt")))
    if args.json:
        json.dump(rows, open(args.json, "w"), indent=1)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
