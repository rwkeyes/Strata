"""Tune the engine's hardware-dependent settings on this PC (setup's --calibrate).

Three settings depend on the machine more than on the model, and the defaults are right for the PC they were
measured on (a Ryzen 5 7600 + RTX 5070 on PCIe 5):
  --pcie-frac     the share of the experts missing from VRAM that are copied over PCIe and run on the GPU instead of
                  on the CPU.  A fast PCIe link and a slow CPU want more; a laptop's x8 link or a fast CPU want less.
  --spec-min-p    how sure the draft layer must be to extend a verify window by another guess.  A slower CPU pays more
                  per extra window row (more experts per window), so it wants a higher floor.
  --pool-workers  the CPU threads that compute experts.  Every physical core is not always best: on hybrid CPUs the
                  efficiency cores can make the whole window wait for them.
  --adapt-every / --adapt-swaps / --adapt-decay
                  how the VRAM expert tier follows the conversation: how often, how many experts it swaps in, how fast
                  its use counts fade.  A PC whose CPU reads the missed experts slowly (DDR3, ~24 GB/s) gains from
                  swapping more: 160 swaps every window measured +7.9% over the default tier on a Xeon E5-2673 v3 (DDR3) with an
                  RTX 4060 Ti on PCIe 3.0 x8 (Q2_0; with the swaps taking effect a window later, #764).
  --prefill-pipe  a layer split only: the prompt chunks that keep the stages reading at once.  The best chunk count
                  depends on b/a, the fixed cost of a chunk over that of a token, which differs by an order of
                  magnitude between rigs (about 700 tokens on four GP100 dies, 5000 on two RTX 3090 Ti) and costs
                  when it is wrong (a fixed 1024 read 22-36% slower on two RTX 4090) - so it is measured here, once,
                  as the prompt read speed of fresh prompts, rather than guessed or learned while the PC serves.
  STRATA_SH_STREAM (AMD only)
                  the shared expert on its own stream.  Off by default on HIP (#816: an RX 6800 on Windows and 4x R9700
                  decode 12-45% faster without it), but the same card family on another PC goes the other way (#1272:
                  2x RX 6900 XT on Linux, 6-11% faster with it), so it is measured here, per PC, and written into the
                  config's "env" only when it wins.  The output is identical either way (same token ids).

The first two and the prompt chunks are measured through one engine (per-request `strata_tune` keys); the worker
count, the adaptive tier and the shared-expert stream need a restart per value.  Those are decode speed: the prompt
path streams every expert whatever they say.

A setting is kept only when it beats the default by more than MIN_GAIN in an interleaved re-measurement - the
adaptive expert tier and the OS make single measurements noisy by a few percent.

    python tools/calibrate.py strata-q2_0.json        # measure and print; setup.py --calibrate also saves it
"""
from __future__ import annotations

import os
import json
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

MIN_GAIN = 0.03                    # a setting must beat the default by this much to be kept
PCIE_FRACS = (0.0, 0.2, 0.35, 0.55, 0.75, 0.9, 1.0)   # 1.0: every miss over PCIe, the CPU pool gets no expert
SPEC_MIN_PS = (0.3, 0.5, 0.7)
SWEEP_ROUNDS = 3                   # how many times the sweep visits each value (see `sweep`)
# the adaptive tier's candidates (every, swaps, decay) against the engine's own (None): swapping more and remembering
# longer, the rest of the set as the engine has it
# HIP: the shared-expert stream fork, off (the HIP default) against on; an engine environment variable, not an argument
SH_STREAM = "STRATA_SH_STREAM"
ADAPT_CANDIDATES = (None, ("1", "80", "0.97"), ("1", "160", "0.97"))
ADAPT_FLAGS = ("--adapt-every", "--adapt-swaps", "--adapt-decay")
MAX_NEW = 128
# the expert tier follows a text over some windows: 128-token answers end before it shows (on a Xeon E5-2673 v3 with
# DDR3, every 1 / 160 / 0.97 measured +0.7% with 128 tokens and +7.9% with 512-token answers), so its step uses these
TIER_MAX_NEW = 512
# --prefill-pipe's values: 0 the buffers' chunks (the engine default), 1 their count evened out, then b/a in tokens
PIPE_VALUES = (0, 1, 768, 1536, 3072, 6144)
PIPE_TOKENS = (2048, 8192)        # fresh prompts read per value (their text: the install's docs/*.md)
PROMPTS = (
    "Write a Python function that merges two sorted lists into one sorted list, with a docstring and two tests.",
    "Explain in two paragraphs how a refrigerator moves heat from inside to outside.",
    "List twelve European capitals with one sentence about each.",
)


class CalibrationCancelled(RuntimeError):
    """An operator requested a stop; no partial sample may enter the report."""


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise CalibrationCancelled("calibration cancelled")


def _progress(callback, stage: str, completed: int, total: int):
    if callback is not None:
        callback(stage, completed, total)


def chat_ids(tok, text: str) -> list[int]:
    return tok.encode(f"<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n",
                      parse_special=True)


def arg_value(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def with_arg(args: list[str], flag: str, value: str | None) -> list[str]:
    """`args` with `flag value` set (replaced if present), or removed when value is None."""
    out = list(args)
    if flag in out:
        i = out.index(flag)
        del out[i:i + 2]
    if value is not None:
        out += [flag, value]
    return out


def derived_workers() -> int | None:
    """The engine's own plan for --pool-workers, from this PC's topology: every physical core but the one the host
    thread sits on, counting ONE socket on a 2-socket PC.  It is the rule cpu::plan_pool_workers applies at startup
    (pool_plan_test.cpp: 8c/16t -> 7 workers, 6c/12t -> 5), which is why the default is usually the winner and the
    sweep only confirmed it -- at 3-5 engine restarts."""
    try:
        base = Path("/sys/devices/system/cpu")
        cores, sockets = set(), set()
        for d in sorted(base.glob("cpu[0-9]*")):
            top = d / "topology"
            try:
                core = (top / "core_id").read_text().strip()
                pkg = (top / "physical_package_id").read_text().strip()
            except OSError:
                continue
            sockets.add(pkg)
            cores.add(pkg + "/" + core)
        if not cores or not sockets:
            return None
        per_socket = len(cores) // len(sockets)
        n = per_socket - 1
        return n if n >= 2 else None
    except Exception:  # noqa: BLE001 - no sysfs (Windows): stay with the engine's own count
        return None


def worker_candidates(default: int, extra=()) -> list[int]:
    """The engine's own count, and fewer: two thirds, a half and a quarter (at least 2), plus `extra` (the P-cores
    minus one on a hybrid CPU, one socket's cores minus one on a 2-socket PC), without repeats.  Bench #780 #815
    #707 #674: 4 beat 15 by 28.7% on 8P + 16E; 17-18 beat 35 by ~20% on 2-socket Xeons."""
    c = [default]
    for w in (round(default * 2 / 3), round(default / 2), round(default / 4), *extra):
        if 2 <= w < default and w not in c:
            c.append(w)
    return c


def host_worker_extras() -> list[int]:
    """P-cores - 1 (hybrid CPU) and one socket's cores - 1 (several sockets) of this PC: more counts to try."""
    try:
        import setup
        out = []
        cores, sockets = setup.cpu_cores(), setup.cpu_sockets()
        if cores:
            out.append(cores[0] - 1)
        if sockets and sockets[0] >= 2:
            out.append(sockets[1] - 1)
        return out
    except Exception:
        return []


def code_prompt(tok):
    """`make(i, n)`: the same as doc_prompt, over SOURCE files instead of the prose docs.

    A setting can win on one text shape and lose on the other: --spec 4 and 6 measured 57.2 vs 58.1 tok/s on the docs
    and 88.7 vs 93.3 on code.  The calibrator had one shape, so any such setting read as a tie."""
    # A REAL file head, not a concatenation of whole files behind a header: the first version of this shape joined
    # tools/*.py and asked to continue "this file", which is not the continuation traffic the box serves.  Measured
    # against the paired gold standard on the serving prompt (10 of 10 pairs, +12.35% for spec_min_p 0.7) the joined
    # shape reported -5.3% for the same value - a false negative that refused a real win.
    text = (ROOT / "tools" / "calibrate.py").read_text(encoding="utf-8", errors="replace")[:400000]
    body = tok.encode(text, parse_special=False) or [0]
    tail = tok.encode("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n", parse_special=True)

    def make(i: int, n: int) -> list[int]:
        head = tok.encode(f"<|im_start|>user\n[calibration code read {i}] Continue this file:\n", parse_special=True)
        return (head + (body * (n // len(body) + 1))[:n] + tail)[:n + len(head) + len(tail)]

    return make


def doc_prompt(tok):
    """`make(i, n)`: a fresh prompt of about n tokens - the same stretch of the docs' text for every i, after a header
    that differs for every i, so no read reuses an earlier one's cache and the reads differ only in their chunks (where
    experts are streamed, the read speed depends on the text: a different stretch per read measured the text)."""
    text = "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted((ROOT / "docs").glob("*.md")))
    body = tok.encode(text, parse_special=False) or [0]
    tail = tok.encode("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n", parse_special=True)

    def make(i: int, n: int) -> list[int]:
        head = tok.encode(f"<|im_start|>user\n[calibration read {i}] Summarize:\n", parse_special=True)
        return head + (body * (n // len(body) + 1))[:n] + tail
    return make


def measure_pipe(eng, make, say=print) -> tuple[float, dict]:
    """--prefill-pipe on one running engine (a layer split): the prompt read speed of fresh prompts per value, then the
    best against the default (0) in SWEEP_ROUNDS paired rounds (back to back, the order alternating).  It is kept only
    when it is faster in every round and by more than MIN_GAIN in the median: on two RTX 3090 Ti one read can be 20%
    off, and a b/a too small for the rig reads 19-24% slower there.  One pass over the values, not `sweep`'s rounds:
    every value reads the same text (`doc_prompt`), and on four GP100 dies one pass already takes about 4 minutes."""
    reads = [0]

    def rate(v: float) -> float:
        ms = toks = 0.0
        for n in PIPE_TOKENS:
            reads[0] += 1
            sampling = {"temperature": 0, "strata_checkpoint": False, "strata_tune": {"prefill_pipe": v}}
            for _ in eng.generate(make(reads[0], n), 1, sampling, threading.Event()):
                pass
            last = eng.last or {}
            ms += float(last.get("prompt_ms") or 0.0)
            toks += float(last.get("prompt_read") or (last.get("prompt_tokens") or 0) - (last.get("reused") or 0))
        return toks / (ms / 1000.0) if ms > 0 and toks > 0 else 0.0

    rate(0)                                            # the first long read after a start faults the weights in
    by_value = {}
    for v in PIPE_VALUES:
        by_value[v] = [rate(v)]
        say(f"    prompt chunks {'off' if v == 0 else 'evened out' if v == 1 else f'b/a {v}'}: {by_value[v][0]:.0f} tok/s")
    best = max(by_value, key=lambda k: by_value[k][0])
    confirm = {0: [], best: []}
    chosen = 0
    if best != 0:
        for r in range(SWEEP_ROUNDS):
            for k in ((0, best) if r % 2 == 0 else (best, 0)):
                confirm[k].append(rate(k))
        gains = [c / d - 1.0 for d, c in zip(confirm[0], confirm[best]) if d > 0]
        if len(gains) == SWEEP_ROUNDS and min(gains) > 0.0 and statistics.median(gains) > MIN_GAIN:
            chosen = best
    return chosen, {"pipe_sweep": {str(k): v for k, v in by_value.items()},
                    "pipe_confirm": {str(k): v for k, v in confirm.items()}}


def pick(measured: dict, default_key, min_gain: float = MIN_GAIN):
    """The key with the best median tok/s, or `default_key` unless the best beats it by more than min_gain."""
    med = {k: statistics.median(v) for k, v in measured.items() if v}
    if not med or default_key not in med:
        return default_key
    best = max(med, key=med.get)
    return best if med[best] > med[default_key] * (1.0 + min_gain) else default_key


class Session:
    """One running engine: measure decode tok/s for a setting (the median of the prompts' rates)."""

    def __init__(self, engine, ids_list, cancel_event=None):
        self.engine = engine
        self.ids_list = ids_list
        self.cancel_event = cancel_event

    def rate(self, tune: dict | None = None, max_new: int | None = None) -> float:
        rates = []
        for ids in self.ids_list:
            _check_cancel(self.cancel_event)
            sampling = {"temperature": 0}
            if tune:
                sampling["strata_tune"] = tune
            n = sum(1 for t in self.engine.generate(ids, max_new or MAX_NEW, sampling,
                                                   self.cancel_event or threading.Event())
                    if t is not None)
            _check_cancel(self.cancel_event)
            ms = (self.engine.last or {}).get("decode_ms") or 0.0
            if n > 8 and ms > 0:
                rates.append(n / (ms / 1000.0))
        return statistics.median(rates) if rates else 0.0

    def warm_up(self, rounds: int = 6):
        """Six rounds, not two: the rate after a start climbs in three plateaus - 83.5 tok/s on the first request,
        93.1 on the second to fourth, 97.0 from the fifth (measured on this box, three restarts, settled level
        repeating to 0.2%).  Two rounds stop ON the middle plateau, and a sweep that straddles the step reads the
        candidate before it 4% slow - which is one way a phantom 5% win appears."""
        for _ in range(rounds):
            self.rate()


VERIFY_WORKERS = False  # set by run() from the config: measure the CPU worker count instead of deriving it
FINE = False            # FINE mode: the full confirmation and the tier's second pass, never skipped
STOCK = False           # STOCK mode: no warm TUNE at all - a restart per candidate, as the tool worked before it
CODE_IDS = None         # set by run(): the same prompts as SOURCE text, the second shape a change must also win
CODE_SHAPE_TOKENS = 2400   # the length this box's continuation traffic actually has (measured on the live prompt)
DEFER_SHAPE = True     # the second-shape guard runs LATER (tools/verify_shapes.py) unless --verify-now is given:
                       # it costs ~60 s, and a first run must not spend that before the model has answered anything
_CAL_T0 = time.monotonic()   # STRATA_CAL_TIMING=1 prints each step's elapsed seconds, measured from import


def run(cfg: dict, say=print, start_engine=None, *, progress=None, cancel_event=None) -> dict:
    """Measure on the engine `cfg` describes; returns {"settings": {flag: value}, "report": {...}}.
    `start_engine(args)` returns a started engine (serve.server.StrataEngine or a stand-in in tests)."""
    if start_engine is None:
        from serve.server import StrataEngine, child_env

        def start_engine(args, env=None):
            e = child_env(cfg)
            e.update(env or {})
            return StrataEngine(cfg["exe"], args, cwd=cfg.get("cwd"), log=cfg.get("log"), env=e)
    global VERIFY_WORKERS, FINE
    VERIFY_WORKERS = bool(cfg.get("verify_workers", False))
    # FAST (default) skips a confirmation the sweep has already settled; FINE never does.  A live, idle box that is
    # being tuned in the background can afford the extra rounds; a first install should not wait for them.
    FINE = bool(cfg.get("calibrate_fine", False)) or os.environ.get("STRATA_CAL_FINE", "").strip() == "1"
    global STOCK
    STOCK = bool(cfg.get("calibrate_stock", False)) or os.environ.get("STRATA_CAL_STOCK", "").strip() == "1"
    import strata_tokenizer as ST
    tpath = Path(cfg["tokenizer"])
    vocab = json.loads((tpath / "vocab.json").read_text(encoding="utf-8"))
    toks = [None] * len(vocab)
    for t, i in vocab.items():
        toks[i] = t
    tok = ST.Tokenizer(toks, (tpath / "merges.txt").read_text(encoding="utf-8").split("\n"),
                       json.loads((tpath / "token_type.json").read_text()))
    ids_list = [chat_ids(tok, p) for p in PROMPTS]
    _code = code_prompt(tok)
    global CODE_IDS
    # 1500 tokens, not 120: a short code fragment is a DIFFERENT workload from the multi-thousand-token file
    # continuations this box serves, and the two disagree (measured: 120-token code favoured spec_min_p 0.5 by 5.5%,
    # the real 2,500-token continuation favoured 0.7 by 4.6%).
    CODE_IDS = [chat_ids(tok, _code(i, CODE_SHAPE_TOKENS)) for i in range(len(PROMPTS))] if _code else None
    if progress is None and cancel_event is None:
        return measure(engine_args(cfg), ids_list, start_engine, say, host_worker_extras(), doc_prompt(tok),
                       hip=cfg.get("backend") == "hip")
    return measure(engine_args(cfg), ids_list, start_engine, say, host_worker_extras(), doc_prompt(tok),
                   hip=cfg.get("backend") == "hip", progress=progress, cancel_event=cancel_event)


def engine_args(cfg: dict) -> list[str]:
    """The arguments the server starts this config's engine with (serve.server.engine_args), so the tuning measures
    the engine as it runs.  #447: this used to read any "gpu" list as a layer split and add --layer-split auto, which
    broke a one-card config with a helper card for the expert tier ("gpu": [0] + --expert-cache-device1: the engine
    refuses a split that leaves no card without a stage), missed the "0,2" spelling, and ignored split_skip_if_fits."""
    from serve.server import engine_args as server_args
    return server_args(cfg)


def engine_error(log: str | None, since: int = 0) -> str | None:
    """#447: the engine's own reason for a failed start - the last line of its log written after byte `since` that is
    its own ("strata ..." or "ERR ..."), else the last line there; None without a log or a new line."""
    if not log:
        return None
    try:
        with open(log, "rb") as f:
            f.seek(since)
            lines = [x.strip() for x in f.read()[-16384:].decode("utf-8", "replace").splitlines() if x.strip()]
    except OSError:
        return None
    return next((x for x in reversed(lines) if x.startswith(("strata", "ERR"))), lines[-1] if lines else None)


def sweep(s, values: list, base_tune: dict, key: str, label: str, say,
          progress=None, stage: str | None = None) -> dict:
    """Every value, `SWEEP_ROUNDS` times, one round visiting all of them and every other round in reverse order.

    A single rate per value cannot order neighbours.  #1332 has 0.75 to 0.95 inside each other's noise at 72.7,
    69.0, 71.0 and 71.9, and that box moved 8-28% between engine starts at an unchanged setting; a 5080/Xeon box
    reports +9.57 tok/s between two blocks at an unchanged setting (p = 0.00004), larger than the effect being
    hunted.  Rotating the values inside one engine start puts that drift on every value the same number of times,
    and the median over the rounds keeps a single bad sample from deciding.  The caller takes the winner from the
    medians, so every value keeps the same chance the others had.
    """
    out = {v: [] for v in values}
    if stage is not None:
        _progress(progress, stage, 0, len(values) * SWEEP_ROUNDS)
    completed = 0
    for r in range(SWEEP_ROUNDS):
        for v in (values if r % 2 == 0 else values[::-1]):
            out[v].append(s.rate({**base_tune, key: v}))
            completed += 1
            if stage is not None:
                _progress(progress, stage, completed, len(values) * SWEEP_ROUNDS)
        say(f"    round {r + 1}/{SWEEP_ROUNDS} {label}: " +
            "  ".join(f"{v:g} {out[v][-1]:.1f}" for v in values))
        # EARLY EXIT: after one round, if every value is inside 1% of the fastest, no later round can separate
        # them -- MIN_GAIN is 3%, and the drift this rotation exists for is smaller than the effect being hunted.
        # The caller then keeps its default instead of spending two more rounds on a phantom.  Measured on the build box
        # (Arc Pro B70, 11,741 of 11,743 experts resident): the PCIe share spanned 56.4-56.5 across all eight values.
        if r == 0 and len(values) > 2:
            first = [out[v][-1] for v in values]
            lo, hi = min(first), max(first)
            if lo > 0 and (hi - lo) / lo < 0.01:
                say(f"    {label}: all {len(values)} values within {100 * (hi - lo) / lo:.2f}% after one round - "
                    "they cannot be told apart at the 3% it takes to beat the default, so the sweep stops here")
                rounds_run = 1
                if stage is not None:      # a progress bar must not stall at a third of the way when the sweep ends
                    _progress(progress, stage, len(values) * SWEEP_ROUNDS, len(values) * SWEEP_ROUNDS)
                break
    else:
        rounds_run = SWEEP_ROUNDS
    med = {v: statistics.median(out[v]) for v in values}
    say(f"    {label}, median of {rounds_run}: " +
        ", ".join(f"{v:g}: {med[v]:.1f} ({min(out[v]):.1f}-{max(out[v]):.1f})" for v in values) +
        f" -> best {max(med, key=lambda k: med[k]):g}")
    return out


def measure(base_args: list[str], ids_list, start_engine, say=print, extra_workers=(), long_prompt=None, hip: bool = False,
            *, progress=None, cancel_event=None) -> dict:
    t0 = time.time()
    report: dict = {}
    _check_cancel(cancel_event)
    _progress(progress, "loading", 0, 1)
    say("  Loading the model for the measurements ...")
    base_args = apply(base_args, {})                   # the product defaults: what the measurements must beat
    eng = start_engine(base_args)
    try:
        info = dict(getattr(eng, "info", {}) or {})
        d_pcie = float(info.get("pcie_frac", 0.55))
        d_minp = float(info.get("spec_min_p", 0.5))
        d_workers = int(info.get("pool_workers", 0)) or None
        s = Session(eng, ids_list, cancel_event)
        s.warm_up()
        _progress(progress, "loading", 1, 1)
        # 0. a layer split's prompt chunks (prompt read speed; independent of the decode settings below)
        pipe = 0
        # the engine's own count of prompt stages where it reports one (a split can leave one, e.g.
        # --split-skip-if-fits), else the flag
        stages = info.get("prefill_stages")
        split = stages > 1 if isinstance(stages, int) else "--layer-split" in base_args
        if long_prompt is not None and split:
            say("  Measuring the prompt chunks of the layer split ...")
            pipe, rep_pipe = measure_pipe(eng, long_prompt, say)
            report.update(rep_pipe)
        # 1. the PCIe share, at the default draft floor
        by_pcie = sweep(s, sorted(set(PCIE_FRACS) | {round(d_pcie, 2)}), {"spec_min_p": d_minp}, "pcie_frac",
                        "PCIe share", say, progress, "pcie_share")
        # "they cannot be told apart" means KEEP THE ENGINE'S OWN SHARE, not take the first value: after the early
        # exit the medians are ties, and max() would hand back 0.0 (no experts over PCIe) as if it had won.
        _v = [statistics.median(by_pcie[v]) for v in by_pcie]
        if _v and max(_v) > 0 and (max(_v) - min(_v)) / max(_v) < 0.01:
            say(f"  PCIe share: inside noise ({(max(_v) - min(_v)) / max(_v) * 100:.2f}%), keeping the engine's {round(d_pcie, 2)}")
            best_pcie = round(d_pcie, 2)
        else:
            best_pcie = max(by_pcie, key=lambda k: statistics.median(by_pcie[k]))
        # 2. the draft floor, at that share
        by_minp = sweep(s, sorted(set(SPEC_MIN_PS) | {round(d_minp, 2)}), {"pcie_frac": best_pcie}, "spec_min_p",
                        "draft floor", say, progress, "draft_floor")
        _v = [statistics.median(by_minp[v]) for v in by_minp]
        if _v and max(_v) > 0 and (max(_v) - min(_v)) / max(_v) < 0.01:
            best_minp = round(d_minp, 2)          # inside noise: keep the product default, do not crown a tie
        else:
            best_minp = max(by_minp, key=lambda k: statistics.median(by_minp[k]))
        # 3. the winner against the default, interleaved, the same number of rounds -- UNLESS the sweep already
        # said it in every round.  Measured on the build box: the draft floor put 0.7 ahead of 0.5 by 4.4-4.5% in all three
        # rounds, the confirmation repeated it to the tenth, and it cost 48 s of the 394 s run.
        dflt, cand = (round(d_pcie, 2), round(d_minp, 2)), (best_pcie, best_minp)
        confirm = {dflt: [], cand: []}
        confirm_total = SWEEP_ROUNDS * 2 if cand != dflt else 1
        _progress(progress, "confirmation", 0, confirm_total)
        clear_lead = False
        if not FINE and cand != dflt and by_minp.get(best_minp) and by_minp.get(round(d_minp, 2)):
            mine, theirs = by_minp[best_minp], by_minp[round(d_minp, 2)]
            if len(mine) == len(theirs) and len(mine) > 1:
                clear_lead = all(a >= b * 1.03 for a, b in zip(mine, theirs))
        if clear_lead:
            say(f"  Confirmation: {cand} led {dflt} by 3% or more in every round of the sweep - accepted as measured")
            confirm[cand].append(statistics.median(mine))
            confirm[dflt].append(statistics.median(theirs))
            chosen = cand
            _progress(progress, "confirmation", confirm_total, confirm_total)
        elif cand != dflt:
            for _ in range(SWEEP_ROUNDS):
                for k in (dflt, cand):
                    confirm[k].append(s.rate({"pcie_frac": k[0], "spec_min_p": k[1]}))
                    _progress(progress, "confirmation", sum(map(len, confirm.values())), confirm_total)
        else:
            _progress(progress, "confirmation", 1, 1)
        chosen = pick(confirm, dflt) if cand != dflt else dflt
        if chosen != dflt and CODE_IDS and DEFER_SHAPE:
            # deferred: record what has to be checked once the box is up and quiet.  tools/verify_shapes.py reads
            # this and either confirms the value or puts the default back - no user waits on it.
            report["pending_shape"] = {"flag": "--spec-min-p", "chosen": chosen[1], "default": dflt[1],
                                       "shape": "source code", "tokens": CODE_SHAPE_TOKENS,
                                       "note": "run tools/verify_shapes.py when the server is idle"}
            say(f"  Second shape DEFERRED: {chosen} is NOT applied yet - it is checked against the default on "
                "source code later (tools/verify_shapes.py), and applied then if it holds")
            chosen = dflt            # unverified: the default stays in the config until the check comes back
        elif chosen != dflt and CODE_IDS:
            # A second shape before the change: a value that wins on the docs and loses on source text is not a win,
            # it is a setting tuned to one workload (measured: --spec 4 vs 6 tie on prose, +5.2% on code - and the
            # reverse case is just as possible).  Two samples each side, only when a change is actually proposed.
            s_code = Session(eng, CODE_IDS, cancel_event)
            tune_a = {"pcie_frac": chosen[0], "spec_min_p": chosen[1]}
            tune_b = {"pcie_frac": dflt[0], "spec_min_p": dflt[1]}
            try:
                # WARM both values first and discard those samples, then measure each twice interleaved.  One sample
                # taken right after switching reads low - the same cold-answer bias the tier arms needed a warm-up
                # for.  Measured: one sample each put spec_min_p 0.7 ~3% BEHIND the default on this shape, while ten
                # interleaved pairs on the same prompt put it 12.35% ahead (10 of 10).
                s_code.rate(tune_a)
                s_code.rate(tune_b)
                a = statistics.median([s_code.rate(tune_a), s_code.rate(tune_a)])
                b = statistics.median([s_code.rate(tune_b), s_code.rate(tune_b)])
            except Exception as e:  # noqa: BLE001 - a shape check that cannot run is not a failed calibration
                say(f"    the second shape could not be measured ({first_line(e)}); the docs' answer stands")
                a = b = 0.0
            report["other_shape"] = {"winner": round(a, 2), "default": round(b, 2)}
            if a and b and a < b * 0.97:
                say(f"  Second shape disagreeing: {chosen} reads {a:.1f} against the default's {b:.1f} tok/s on source "
                    "code - the change is NOT made (one shape's word is not a decision)")
                chosen = dflt
            elif a and b:
                say(f"  Second shape agrees: {chosen} reads {a:.1f} against the default's {b:.1f} tok/s on source code")
        report.update(default={"pcie_frac": dflt[0], "spec_min_p": dflt[1], "pool_workers": d_workers},
                      pcie_sweep={str(k): v for k, v in by_pcie.items()},
                      min_p_sweep={str(k): v for k, v in by_minp.items()},
                      confirm={f"{k[0]}/{k[1]}": v for k, v in confirm.items()})
    except BaseException:      # steps 0-3 raise: the engine goes; a completed run keeps it for the warm steps below
        close(eng)
        raise
    settings = {}
    if pipe:
        settings["--prefill-pipe"] = str(int(pipe))
    if chosen != dflt:
        settings["--pcie-frac"] = f"{chosen[0]:.2f}"
        settings["--spec-min-p"] = f"{chosen[1]:.2f}"
    base_rate = statistics.median(confirm[chosen]) if confirm.get(chosen) else None
    # 4. the CPU worker count -- DERIVED, not swept.  The engine already plans this number from the topology and
    # starts with it, so the sweep only ever rediscovered the default, at the price of 3-5 engine restarts and one
    # model load each.  --verify-workers runs the old sweep for anyone who wants it measured rather than derived.
    # (#1337's rule still covers that path: a death during a measurement is that candidate losing, the rest stays.)
    derived = derived_workers()
    _progress(progress, "cpu_workers", 0, 1)      # the stage still reports, even when nothing is measured in it
    if d_workers:
        # the engine's own count is what it planned from this topology; the derivation is a cross-check, NOT a
        # reason to write a value nobody measured (a config change on the strength of a rule is how a clock gets
        # gained in theory and lost in practice).  Reported, and only acted on through VERIFY_WORKERS below.
        agree = "" if derived in (None, d_workers) else f"; the topology rule alone would say {derived}"
        say(f"  CPU workers: {d_workers} (the engine's own plan, not swept){agree}")
        report["workers_derived"] = dict(engine=d_workers, rule=derived)
    if VERIFY_WORKERS and d_workers and len(worker_candidates(d_workers, extra_workers)) > 1:
        tuned = with_arg(with_arg(base_args, "--pcie-frac", f"{chosen[0]:.2f}"), "--spec-min-p", f"{chosen[1]:.2f}")
        by_workers = {}
        workers = worker_candidates(d_workers, extra_workers)
        _progress(progress, "cpu_workers", 0, len(workers))
        for index, w in enumerate(workers, 1):
            _check_cancel(cancel_event)
            say(f"  Measuring with {w} CPU workers (restarts the engine) ...")
            measure_candidate(start_engine, with_arg(tuned, "--pool-workers", None if w == d_workers else str(w)),
                              f"{w} workers", say, report, by_workers, w, ids_list, cancel_event=cancel_event)
            _progress(progress, "cpu_workers", index, len(workers))
        report["workers"] = {str(k): v for k, v in by_workers.items()}
        w_best = pick(by_workers, d_workers) if by_workers else d_workers
        if w_best != d_workers:
            settings["--pool-workers"] = str(w_best)
            base_rate = statistics.median(by_workers[w_best])
        elif by_workers.get(d_workers):
            base_rate = statistics.median(by_workers[d_workers])
    else:
        _progress(progress, "cpu_workers", 1, 1)
    # 5. the adaptive expert tier -- TUNED IN PLACE on the engine that is already loaded.  adapt_every, adapt_swaps
    # and adapt_decay are read out of the engine's options where it swaps experts, so a TUNE line between requests
    # changes them with no restart and no model reload (handle_tune in src/program/generate.cpp).  This step used to
    # cost a load per candidate; it now costs none.  An engine without the TUNE command falls back to the restarts.
    tuned = apply(base_args, settings)
    by_adapt: dict = {}
    warm = callable(getattr(eng, "tune", None)) and not VERIFY_WORKERS and not STOCK
    d_adapt = {k: info.get(k) for k in ("adapt_every", "adapt_swaps", "adapt_decay")
               if isinstance(info.get(k), (int, float))}
    if not d_adapt:            # the engine reports none of them: the config's own flags, else the product defaults
        d_adapt = {"adapt_every": 4, "adapt_swaps": 96, "adapt_decay": 0.7}
        for flag, key in zip(ADAPT_FLAGS, ("adapt_every", "adapt_swaps", "adapt_decay")):
            v = arg_value(base_args, flag)
            if v not in (None, ""):
                try:
                    d_adapt[key] = float(v) if key == "adapt_decay" else int(v)
                except ValueError:
                    pass
    if warm:                  # one thrown-away 512-token sample: the first long answer after the sweeps is always the
        # slow one (measured on the build box: 46.9 then 57.8-58.1 for every later arm, with any tier setting), and it would
        # otherwise land on whichever arm is measured first and invent a 10% win for the others.
        try:
            s_w = Session(eng, ids_list, cancel_event)
            s_w.rate(max_new=TIER_MAX_NEW)
        except Exception:     # noqa: BLE001 - a failed warm-up sample is not a result
            pass
    _progress(progress, "expert_tier", 0, len(ADAPT_CANDIDATES))
    for index, cand in enumerate(ADAPT_CANDIDATES, 1):
        _check_cancel(cancel_event)
        key = "default" if cand is None else "/".join(cand)
        what = ("(the engine default)" if cand is None
                else "every " + cand[0] + ", " + cand[1] + " swaps, decay " + cand[2])
        if warm:
            tune = (dict(d_adapt) if cand is None else
                    dict(zip(("adapt_every", "adapt_swaps", "adapt_decay"),
                             (int(cand[0]), int(cand[1]), float(cand[2])))))
            say(f"  Measuring the expert tier {what} (TUNEd in place: no restart) ...")
            try:
                eng.tune(tune)
            except Exception as e:  # noqa: BLE001 - a refused TUNE is this candidate losing, not a failed run
                why = first_line(e)
                say(f"    the engine refused the expert tier {key}: {why} - this candidate is dropped")
                report.setdefault("failed_starts", {})["the expert tier " + key] = why
                _progress(progress, "expert_tier", index, len(ADAPT_CANDIDATES))
                continue
            s_t = Session(eng, ids_list, cancel_event)
            # the FIRST arm pays the tier's own warm-up (measured on the build box: 46.7 then 58.0 on the same arm, so a
            # single warm-up sample was not enough and the default looked 10% slow); later arms inherit a warm tier
            s_t.warm_up(3 if not by_adapt else 1)
            by_adapt[key] = [s_t.rate(max_new=TIER_MAX_NEW)]
        else:
            args = tuned
            for flag, v in zip(ADAPT_FLAGS, cand or (None,) * len(ADAPT_FLAGS)):
                args = with_arg(args, flag, v)
            say(f"  Measuring the expert tier {what} (restarts the engine) ...")
            measure_candidate(start_engine, args, "the expert tier " + key, say, report, by_adapt, key, ids_list,
                              TIER_MAX_NEW, cancel_event=cancel_event)
        _progress(progress, "expert_tier", index, len(ADAPT_CANDIDATES))
    if warm:                  # a second pass in the OPPOSITE order: the tier's learned counts carry from one arm to
        # the next, so whichever arm is measured first pays the warm-up.  One sample per pass, taken in both
        # orders, charges every arm that cost once, and the caller takes the median of the two.
        seen = [k for k in by_adapt if by_adapt[k]]
        # ...unless the first pass already shows a tie: arms within 0.5% cannot be separated by a second sample, and
        # the pass exists to cancel the carry-over bias, which shows up as a spread well above this.  Measured on
        # the build box: 58.19 / 58.21 / 58.19 across the three arms, and the second pass cost 40 s to repeat it.
        firsts = [by_adapt[k][0] for k in seen]
        spread = (max(firsts) - min(firsts)) / max(firsts) if firsts and max(firsts) else 0.0
        if not FINE and len(seen) > 1 and spread < 0.005:
            say(f"  The expert tier: the arms are within {spread * 100:.2f}% of each other after one pass - a "
                "second pass would re-measure a tie, so the engine's default stands")
            pass   # keep every arm in the report: the caller's 3% rule will not switch on a tie
        elif len(seen) > 1:
            say("  The expert tier: a second pass in the opposite order (the tier carries over between arms) ...")
            for cand in list(ADAPT_CANDIDATES)[::-1]:
                _check_cancel(cancel_event)
                key = "default" if cand is None else "/".join(cand)
                if not by_adapt.get(key):
                    continue
                tune = (dict(d_adapt) if cand is None else
                        dict(zip(("adapt_every", "adapt_swaps", "adapt_decay"),
                                 (int(cand[0]), int(cand[1]), float(cand[2])))))
                try:
                    eng.tune(tune)
                except Exception:   # noqa: BLE001 - already reported in the first pass
                    continue
                s_t = Session(eng, ids_list, cancel_event)
                by_adapt[key].append(s_t.rate(max_new=TIER_MAX_NEW))
    if warm and d_adapt:      # leave the tier as the engine started it, for step 6 and the caller
        try:
            eng.tune(d_adapt)
        except Exception:     # noqa: BLE001
            pass
    a_best = pick(by_adapt, "default") if by_adapt else "default"
    report["adapt"] = by_adapt
    if a_best != "default":
        for flag, v in zip(ADAPT_FLAGS, a_best.split("/")):
            settings[flag] = v
        base_rate = statistics.median(by_adapt[a_best])
    elif by_adapt.get("default"):
        base_rate = statistics.median(by_adapt["default"])
    # 6. HIP: the shared-expert stream fork (a restart each), with everything chosen so far.  Both values are set
    # explicitly, so a value the config carried before does not decide the baseline.
    env = {}
    tuned = apply(base_args, settings)
    if hip and fork_is_unsafe(tuned):
        # #3895: one Windows RX 6000 PC faulted the engine on the first request with the fork and KV streaming together. The fork is off by
        # default on AMD, so skipping the measurement changes nothing; whoever wants it sets the variable by hand.
        say("  Not measuring the shared expert on its own stream: on Windows with KV streaming (--kv-resident) it made the engine fail on the first "
            f"request on one PC (#3895). To try it anyway, put {SH_STREAM}=1 in the config's env.")
        report["sh_stream_skipped"] = "windows with --kv-resident"
    elif hip:
        by_fork = {}
        for v in ("0", "1"):
            _check_cancel(cancel_event)
            say(f"  Measuring the shared expert {'on its own stream' if v == '1' else 'on the main stream (the AMD default)'}"
                " (restarts the engine) ...")
            measure_candidate(lambda a: start_engine(a, {SH_STREAM: v}), tuned, f"{SH_STREAM}={v}", say, report,
                              by_fork, v, ids_list, cancel_event=cancel_event)
        report["sh_stream"] = by_fork
        if pick(by_fork, "0") == "1":
            env[SH_STREAM] = "1"
            base_rate = statistics.median(by_fork["1"])
        elif by_fork.get("0"):
            base_rate = statistics.median(by_fork["0"])
    report["seconds"] = round(time.time() - t0)
    report["tok_s"] = round(base_rate, 1) if base_rate else None
    report["engine_loads"] = 1
    _check_cancel(cancel_event)
    close(eng)
    return {"settings": settings, "env": env, "report": report}


def first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__

def fork_is_unsafe(args) -> bool:
    """The shared-expert fork (STRATA_SH_STREAM) with KV streaming on Windows: the one combination that has made an engine fail on its first
    request (#3895, an RX 6900 XT). Not measured there; a person can still set the variable."""
    return sys.platform == "win32" and arg_value(args, "--kv-resident") is not None


def restart(start_engine, args, what, say, report):
    """#1337: a later step's engine start that fails is a candidate that loses, not a failed run: what the earlier
    steps measured (and the settings they chose) must survive it.  Returns the engine, or None after saying why and
    noting it in report["failed_starts"].  (The first start, for steps 1-3, is not covered: with no engine there is
    nothing measured.)"""
    try:
        return start_engine(args)
    except Exception as e:  # noqa: BLE001 - whatever the start raised (RuntimeError, EngineDied, OSError ...)
        why = first_line(e)
        say(f"    the engine did not start for {what}: {why} - this candidate is dropped, the earlier results are kept")
        report.setdefault("failed_starts", {})[what] = why
        return None


def measure_candidate(start_engine, args, what, say, report, by: dict, key, ids_list, max_new: int | None = None,
                      cancel_event=None):
    """One restart-and-measure candidate (#1337): a start that fails and an engine that exits mid-measurement are
    both that candidate losing - `by[key]` gets its two rates, or report["failed_starts"] its reason.  Everything
    measured earlier and the other candidates survive.  An operator cancel is not a candidate death: it raises."""
    e = restart(start_engine, args, what, say, report)
    if e is None:
        return
    try:
        s = Session(e, ids_list, cancel_event)
        s.warm_up(1)
        by[key] = [s.rate(max_new=max_new), s.rate(max_new=max_new)]
        say(f"    {what}: {statistics.median(by[key]):.1f} tok/s")
    except CalibrationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - the engine died mid-measurement: this candidate loses (#1337)
        why = first_line(exc)
        say(f"    the engine died while measuring {what}: {why} - this candidate is dropped, "
            "the earlier results are kept")
        report.setdefault("failed_starts", {})[what] = why
    finally:
        close(e)


def close(eng):
    proc = getattr(eng, "proc", None)
    if proc is None:
        return
    try:
        proc.stdin.write("QUIT\n")
        proc.stdin.flush()
        proc.stdin.close()
        proc.wait(60)
    except Exception:
        proc.kill()


DEFAULTS = {"--pcie-frac": None, "--spec-min-p": "0.5", "--pool-workers": None,   # None: the engine's own choice
            "--adapt-every": None, "--adapt-swaps": None, "--adapt-decay": None, "--prefill-pipe": None}


def apply(args: list[str], settings: dict) -> list[str]:
    """`args` with the calibrated settings; a setting the calibration did not change goes back to the product
    default (setup's --spec-min-p 0.5, the engine's own PCIe share and worker count), so an older calibration's
    values never linger."""
    out = list(args)
    for flag, default in DEFAULTS.items():
        out = with_arg(out, flag, settings.get(flag, default))
    return out


ENV_KEYS = (SH_STREAM,)


def apply_env(env: dict | None, calibrated: dict | None) -> dict:
    """The config's "env" with a calibration's engine variables: the ones it chose are set, the others in ENV_KEYS are
    removed (back to the engine's default), so an older calibration's choice never lingers."""
    out = {k: v for k, v in (env or {}).items() if k not in ENV_KEYS}
    out.update(calibrated or {})
    return out


def exclusive(path) -> bool:
    """Take an exclusive lock for this calibration; False when another one already holds it.

    Two overlapping runs fight over the card and the tuning port, and the loser's engine is killed mid-load
    (measured: SIGTERM during a load, reported as "the engine exited before it was ready").  A card holds one engine,
    so a second calibration is a mistake, not a request to queue."""
    import fcntl
    try:
        fh = open(path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        globals()["_LOCK_FH"] = fh          # held for the life of the process; the kernel drops it on exit
        return True
    except OSError:
        return False


def _timed_say(line: str) -> None:
    """Progress with elapsed seconds: `STRATA_CAL_TIMING=1 tools/calibrate.py cfg.json` answers "where did the six
    minutes go" - the sweeps or the load.  The stamp and the text go to the SAME stream on purpose: split across
    stdout and stderr they interleaved into timestamps with no message under `tee`, which is how the first timed run
    read."""
    stamp = f"[{time.monotonic() - _CAL_T0:7.1f}s] " if os.environ.get("STRATA_CAL_TIMING") else ""
    print(stamp + line, flush=True)


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--fine"]
    if len(argv) != 1:
        sys.exit("usage: calibrate.py <strata-*.json> [--fine]")
    if "--fine" in sys.argv:
        os.environ["STRATA_CAL_FINE"] = "1"      # the full confirmation + the tier's second pass
    lock = Path(argv[0]).with_suffix(".calibrating")
    if not exclusive(lock):
        sys.exit(f"another calibration is running ({lock} is held) - wait for it, or remove a stale lock")
    try:
        res = run(json.loads(Path(argv[0]).read_text(encoding="utf-8-sig")), say=_timed_say)
    finally:
        lock.unlink(missing_ok=True)
    print(json.dumps(res, indent=1))
