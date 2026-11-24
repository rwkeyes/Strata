"""Hot tuning for a running Strata, from the engine's own per-request telemetry.

Why this exists: a Strata install has a handful of settings whose best value depends on the machine it runs on
(PCIe share, draft floor, adaptive tier) and on the WORKLOAD it serves (a 30k-token agentic replay and a 40-token
chat turn want different ones).  `setup.py --calibrate` measures the machine ones once, by restarting the engine
6-10 times; this measures them (and the workload ones) from requests that are already being served, and can apply
them between requests with the engine's TUNE command - no restart, no weight reload.

Where the data comes from: the engine already prints, for every request, its own accounting.  Nothing here reads a
client clock or guesses:

    strata serve: prompt 4210 tokens = 0 reused + 4210 read in 3041 ms (1385 tok/s), 66 generated in 1030 ms
                  (64 tok/s), drafts accepted 2 of 3, 1 checkpoints
    strata serve: decode expert cache hit rate: 99.5% (12519 hits / 12583 lookups; 64 sent over PCIe)
    strata serve: KV streaming: 96.55% of 4872 block reads hit VRAM, 0.7 MiB read from RAM
    strata serve: conversation cache: parked 3004 tokens in 126.1 ms; parked=1 ... evictions=0

The three decisions it makes, in the order they pay:

  1. PER-STRATUM BASELINES.  A request is classified by cache state (cold / warm), prompt length bucket and answer
     length, and settings are kept per stratum.  Pooling them is a measurement error of 8x on this hardware:
     cache-warm prompts read at ~11,900 tok/s and cold ones at ~1,390.
  2. PAIRED EXPLORATION.  Two values of one setting are alternated on ADJACENT requests of the same stratum, so
     drift (thermal, page cache, tier warm-up) lands on both arms equally, and only pairs are compared.  A setting
     is adopted only on a paired median gain >= gain_bar with enough pairs and the same sign in two sessions.
  3. PROPOSALS FOR WHAT CANNOT MOVE LIVE.  KV size/type, expert-cache size, conversation-cache size and --spec
     depth are sized at load; they are reported as proposals with their evidence, written for the next start, and
     never changed under a running engine.

Modes:
    off     - nothing
    shadow  - classify, decide, record what it WOULD do; change nothing (the default; safe on a live box)
    on      - apply adopted values (and budgeted exploration) through TUNE, only while no request is in flight

Safety, in this order: an explicit CLI flag is never touched (they arrive as `pinned`); a setting outside its
bounds is refused; the tuner never writes while `service.status["busy"]` or a request is queued; a request longer
than `latency_guard_tokens` is never an exploration arm; the first `cold_requests` observations after a start are
recorded but never acted on; every failure is swallowed and noted in the report rather than raised into serving.

UNDO: rm serve/autotune.py   (and the three wiring lines in serve/server.py)
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------------------------------- the settings
#
# kind: "tune"    - changed between requests with the engine's TUNE command (no reload)
#       "propose" - sized at load; reported, written for the next start, never changed live
#
# gain_bar: how much better a value must measure before it replaces the baseline (fraction)
# min_pairs: how many paired comparisons before any adoption is considered
# bounds:   (low, high) inclusive, or a tuple of allowed values
SETTINGS: dict[str, dict] = {
    "pcie_frac":      dict(kind="tune", bounds=(0.0, 1.0), default=None, gain_bar=0.03, min_pairs=12,
                           why="the share of missed experts copied over PCIe instead of computed on the CPU"),
    "spec_min_p":     dict(kind="tune", bounds=(0.0, 1.0), default=0.5, gain_bar=0.03, min_pairs=12,
                           why="how sure the draft layer must be to extend a verify window"),
    "adapt_every":    dict(kind="tune", bounds=(0, 64), default=4, gain_bar=0.03, min_pairs=12,
                           why="how often the VRAM expert tier follows the conversation"),
    "adapt_swaps":    dict(kind="tune", bounds=(0, 512), default=96, gain_bar=0.03, min_pairs=12,
                           why="how many experts it swaps per round"),
    "adapt_decay":    dict(kind="tune", bounds=(0.0, 1.0), default=0.7, gain_bar=0.03, min_pairs=12,
                           why="how fast the routing counts fade"),
    "adapt_min_gain": dict(kind="tune", bounds=(0.0, 16.0), default=1.5, gain_bar=0.03, min_pairs=12,
                           why="how much an expert must beat the one it replaces"),
    "prompt_cache":   dict(kind="tune", bounds=(0, 64), default=6, gain_bar=0.03, min_pairs=12,
                           why="how many conversation checkpoints are kept"),
    "short_read":     dict(kind="tune", bounds=(0, 4096), default=64, gain_bar=0.03, min_pairs=12,
                           why="the prompt path's short-read threshold"),
    "kv_resident":    dict(kind="propose", bounds=(0, 1 << 20), default=0, gain_bar=0.0, min_pairs=8,
                           why="KV cells held in VRAM per QSA layer (the rest stream) - sized at load"),
    "kv_type":        dict(kind="propose", bounds=("fp16", "int8"), default="fp16", gain_bar=0.0, min_pairs=8,
                           why="KV storage width - sized at load"),
    "expert_cache":   dict(kind="propose", bounds=(0, 1 << 20), default=0, gain_bar=0.0, min_pairs=8,
                           why="experts held in VRAM - sized at load"),
    "spec_depth":     dict(kind="propose", bounds=(0, 8), default=4, gain_bar=0.0, min_pairs=8,
                           why="the verify window depth - sized at load"),
    "conv_cache_mib": dict(kind="propose", bounds=(0, 1 << 20), default=0, gain_bar=0.0, min_pairs=8,
                           why="host RAM for parked conversations - sized at load"),
}

# the values a tuner may try, in a stable order (the baseline itself is always arm 0)
ARMS: dict[str, tuple] = {
    "pcie_frac":      (0.0, 0.2, 0.35, 0.55, 0.75, 0.9, 1.0),
    "spec_min_p":     (0.3, 0.5, 0.7),
    "adapt_every":    (1, 2, 4, 8),
    "adapt_swaps":    (0, 48, 96, 160),
    "adapt_decay":    (0.5, 0.7, 0.97),
    "adapt_min_gain": (1.0, 1.5, 3.0),
    "prompt_cache":   (0, 6, 16),
    "short_read":     (0, 64, 256),
}

# The command-line flag each setting is spelled as: one table, because both the proposals written for the next
# start and the "pinned by the operator" check read it (serve/server.py imports this).
FLAG_OF = {
    "pcie_frac": "--pcie-frac", "spec_min_p": "--spec-min-p", "adapt_every": "--adapt-every",
    "adapt_swaps": "--adapt-swaps", "adapt_decay": "--adapt-decay", "adapt_min_gain": "--adapt-min-gain",
    "prompt_cache": "--prompt-cache", "short_read": "--short-read", "kv_resident": "--kv-resident",
    "kv_type": "--kv", "expert_cache": "--expert-cache", "spec_depth": "--spec", "conv_cache_mib":
    "--conversation-cache-mib",
}

ACCOUNTING = re.compile(
    r'prompt (?P<prompt>\d+) tokens = (?P<reused>\d+) reused \+ (?P<read>[\d]+(?: of \d+)?) read in '
    r'(?P<prompt_ms>[\d.]+) ms \((?P<prompt_tps>[\d.]+) tok/s\), (?P<gen>\d+) generated in '
    r'(?P<decode_ms>[\d.]+) ms \((?P<decode_tps>[\d.]+) tok/s\), drafts accepted (?P<acc>\d+) of '
    r'(?P<off>\d+), (?P<ckpt>\d+) checkpoints(?P<cancel> \(cancelled\))?')
EXPERT_HITS = re.compile(r'decode expert cache hit rate: (?P<pct>[\d.]+)% \((?P<hits>\d+) hits / '
                         r'(?P<lookups>\d+) lookups(?:; (?P<pcie>\d+) sent over PCIe)?')
KV_HITS = re.compile(r'KV streaming: (?P<pct>[\d.]+)% of (?P<reads>\d+) block reads hit VRAM, '
                     r'(?P<mib>[\d.]+) MiB read from RAM')
CACHE_PARK = re.compile(r'conversation cache: parked (?P<tokens>\d+) tokens in (?P<ms>[\d.]+) ms.*?'
                        r'evictions=(?P<evictions>\d+)')


def setting_bounds_ok(name: str, value) -> bool:
    spec = SETTINGS.get(name)
    if spec is None:
        return False
    lo, hi = spec["bounds"]
    if isinstance(lo, str):                       # a choice, not a range
        return value in (lo, hi)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return lo <= v <= hi


def stratum_of(reuse_frac: float, prompt_tokens: int, generated: int) -> str:
    """The workload class a request belongs to.  This is the unit everything is measured and kept per: the cache
    state alone is an 8x difference on the prompt path, so pooling it would swamp every setting's effect."""
    cache = "warm" if reuse_frac >= 0.9 else ("cold" if reuse_frac < 0.2 else "mixed")
    length = "short" if prompt_tokens < 2000 else ("mid" if prompt_tokens < 8000 else "long")
    answer = "terse" if generated < 256 else "long"
    return f"{cache}/{length}/{answer}"


@dataclass
class Observation:
    """One served request, as the engine reported it."""
    ts: float
    stratum: str
    prompt_tokens: int
    reused: int
    read_tokens: int
    prompt_ms: float
    generated: int
    decode_ms: float
    accepted: int
    offered: int
    expert_hit: float | None = None
    expert_lookups: int = 0
    pcie_experts: int = 0
    kv_hit: float | None = None
    park_ms: float | None = None
    evictions: int = 0
    cancelled: bool = False
    settings: dict = field(default_factory=dict)     # the values in force for THIS request
    arm: str | None = None                            # which explored value it carried, if any

    @property
    def reuse_frac(self) -> float:
        return (self.reused / self.prompt_tokens) if self.prompt_tokens else 0.0

    @property
    def prompt_tps(self) -> float:
        return (1000.0 * self.read_tokens / self.prompt_ms) if self.prompt_ms > 0 else 0.0

    @property
    def decode_tps(self) -> float:
        return (1000.0 * self.generated / self.decode_ms) if self.decode_ms > 0 else 0.0

    @property
    def accept_rate(self) -> float | None:
        return (self.accepted / self.offered) if self.offered else None

    def cost(self) -> float:
        """Seconds this request took, prompt read plus decode - the quantity a tuner minimises."""
        return (self.prompt_ms + self.decode_ms) / 1000.0


class LogTail:
    """Follow the engine's log file from where it is now, handing back whole request blocks."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.offset = 0
        self.pending: list[str] = []
        if self.path and self.path.exists():
            self.offset = self.path.stat().st_size

    def new_blocks(self) -> list[list[str]]:
        if not self.path:
            return []
        try:
            if not self.path.exists():
                return []
            with self.path.open("rb") as f:
                f.seek(self.offset)
                chunk = f.read()
                self.offset = f.tell()
        except OSError:
            return []
        if not chunk:
            return []
        blocks: list[list[str]] = []
        for line in chunk.decode("utf-8", "replace").splitlines():
            if not line.startswith("strata "):
                continue
            self.pending.append(line)
            if ACCOUNTING.search(line):          # the accounting line closes a request's block
                blocks.append(self.pending)
                self.pending = []
            elif len(self.pending) > 200:        # a runaway block (a crash loop): drop it
                self.pending = []
        return blocks


def parse_block(lines: list[str], settings: dict, arm: str | None) -> Observation | None:
    acc = None
    for line in lines:
        acc = ACCOUNTING.search(line) or acc
    if acc is None:
        return None
    read = acc.group("read").split(" of ")[0]
    obs = Observation(ts=time.time(), stratum="", prompt_tokens=int(acc.group("prompt")),
                      reused=int(acc.group("reused")), read_tokens=int(read),
                      prompt_ms=float(acc.group("prompt_ms")), generated=int(acc.group("gen")),
                      decode_ms=float(acc.group("decode_ms")), accepted=int(acc.group("acc")),
                      offered=int(acc.group("off")), cancelled=bool(acc.group("cancel")),
                      settings=dict(settings), arm=arm)
    for line in lines:
        if m := EXPERT_HITS.search(line):
            obs.expert_hit = float(m.group("pct"))
            obs.expert_lookups = int(m.group("lookups"))
            obs.pcie_experts = int(m.group("pcie") or 0)
        elif m := KV_HITS.search(line):
            obs.kv_hit = float(m.group("pct"))
        elif m := CACHE_PARK.search(line):
            obs.park_ms = float(m.group("ms"))
            obs.evictions = int(m.group("evictions"))
    obs.stratum = stratum_of(obs.reuse_frac, obs.prompt_tokens, obs.generated)
    return obs


def paired_median(a, b, tol: float = 0.25) -> tuple[float, int]:
    """Median of b/a - 1 over pairs, matched by PROMPT SIZE and then by proximity in time.

    Two things have to be cancelled or the number is meaningless: drift (a pair is served minutes apart at most)
    and request shape (a 55-token chat turn costs 0.5 s where a 1,900-token prompt costs 3 s - comparing them
    produced a "+200% gain" the first time this ran).  Entries are (metric, prompt_tokens); a baseline is paired
    with the nearest-sized candidate within `tol` (25%), one to one, keeping sequence order.
    """
    def as_pairs(xs):
        out = []
        for x in xs:
            out.append((float(x[0]), float(x[1])) if isinstance(x, (tuple, list)) else (float(x), 0.0))
        return out
    base, cand = as_pairs(a), as_pairs(b)
    if not base or not cand:
        return 0.0, 0
    used = [False] * len(base)
    ratios = []
    for bm, bn in cand:
        best, best_d = -1, None
        for i, (am, an) in enumerate(base):
            if used[i] or am <= 0:
                continue
            if an > 0 and bn > 0:
                d = abs(an - bn) / max(an, bn)
                if d > tol:
                    continue
            else:
                d = 0.0
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best >= 0:
            used[best] = True
            ratios.append((bm / base[best][0]) - 1.0)
    return (statistics.median(ratios) if ratios else 0.0), len(ratios)


def mad(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


class AutoTuner:
    """Classify, measure, decide.  One instance per served model; started by serve/server.py."""

    def __init__(self, cfg: dict, engine, service, model: str, mode: str = "shadow",
                 pinned: set[str] | None = None, explore_budget: float = 0.05,
                 latency_guard_tokens: int = 12000, cold_requests: int = 8,
                 state_dir: Path | None = None, log_path: Path | None = None, say=print):
        self.cfg = cfg or {}
        self.engine = engine
        self.service = service
        self.model = model
        self.mode = mode if mode in ("off", "shadow", "on") else "shadow"
        self.pinned = set(pinned or ())
        self.explore_budget = max(0.0, min(1.0, explore_budget))
        self.latency_guard_tokens = latency_guard_tokens
        self.cold_requests = cold_requests
        self.say = say
        self.tail = LogTail(log_path or (self.cfg.get("log") and Path(self.cfg["log"])))
        self.hw_key = self._hardware_key()
        self.state_dir = Path(state_dir or Path.cwd())
        self.state_path = self.state_dir / f"tuning-{re.sub(r'[^A-Za-z0-9_.-]', '-', model)}-{self.hw_key}.json"
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.observations: list[Observation] = []
        self.seen = 0
        self.seen_session = 0                        # requests served THIS session; reset to 0 on every start
        self.session = 0
        self.adopted: dict[str, dict] = {}          # setting -> {value, stratum, gain, pairs, session}
        self.arms: dict[str, list] = {}             # "stratum|setting|value" -> [metric, ...]
        self.baseline_arms: dict[str, list] = {}    # "stratum|setting" -> [metric, ...] (the value in force)
        self.exploring: tuple[str, object] | None = None   # (setting, value) the next request carries
        self.restore_pending: str | None = None            # a setting still carrying an arm after its request
        self._seen_at_arm = 0                              # self.seen when the current arm was applied
        self.explore_pairs: dict[str, int] = {}            # setting -> arms served so far this session
        self.explore_cap = 240                             # hard cap on explored requests per session
        self.decisions: list[dict] = []
        self.proposals: dict[str, dict] = {}
        self.errors: list[str] = []
        self.load_state()

    # --------------------------------------------------------------------------------------------- plumbing
    def _hardware_key(self) -> str:
        bits = [platform.system(), platform.machine(), str(self.cfg.get("backend") or ""),
                str(self.cfg.get("exe") or "")]
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    bits.append(line.split(":", 1)[1].strip())
                    break
        except OSError:
            pass
        gpu = ""
        try:
            st = self.service.v1_status() if hasattr(self.service, "v1_status") else {}
            gpu = str((st or {}).get("gpu") or (st or {}).get("device") or "")
        except Exception:                                    # noqa: BLE001
            pass
        bits.append(gpu)
        return hashlib.sha256("|".join(bits).encode()).hexdigest()[:10]

    def start(self) -> None:
        if self.mode == "off":
            return
        self.session = int(self.load_state().get("sessions", 0)) + 1
        self.thread = threading.Thread(target=self._loop, name="autotune", daemon=True)
        self.thread.start()
        self.say(f"[strata] auto-tune: {self.mode} (hardware {self.hw_key}, state {self.state_path.name})")

    def stop_tuner(self) -> None:
        self.stop.set()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                for block in self.tail.new_blocks():
                    self._consume(block)
                self._maybe_decide()
                self._restore()
                self._explore()
            except Exception as e:                           # noqa: BLE001 - never into serving
                self.errors.append(f"{type(e).__name__}: {e}")
                self.errors = self.errors[-10:]
            self.stop.wait(2.0)

    # ---------------------------------------------------------------------------------------------- ingest
    def _settings_in_force(self) -> dict:
        out = {name: rec["value"] for name, rec in self.adopted.items()}
        if self.exploring:
            out[self.exploring[0]] = self.exploring[1]
        return out

    def _consume(self, block: list[str]) -> None:
        self.seen += 1
        self.seen_session += 1
        # an arm tags the FIRST request served after it was applied (TUNE lands between requests, so that request
        # is the one that carried it); a block that was already streaming when the arm landed stays a baseline
        arm = None
        if self.exploring and self.seen > self._seen_at_arm:
            arm = f"{self.exploring[0]}={self.exploring[1]}"
        obs = parse_block(block, self._settings_in_force(), arm)
        if arm:                                             # that request carried the arm: back to the baseline
            name = self.exploring[0]
            self.explore_pairs[name] = self.explore_pairs.get(name, 0) + 1
            self.exploring = None
            # the engine is usually still finishing the response here, so the restore is retried by the loop -
            # and no new arm is started until it lands, or an untagged request would run the arm value
            self.restore_pending = name
            self._restore()
        if obs is None or obs.cancelled:
            return
        with self.lock:
            self.observations.append(obs)
            self.observations = self.observations[-400:]
            self._score(obs)
            self._propose(obs)
            self.save_state()

    def _metric(self, obs: Observation) -> float:
        """Requests/second over the whole request: prompt read plus decode, which is what a user waits for.
        A prompt-heavy request and a chat turn are then comparable inside one stratum."""
        return (1.0 / obs.cost()) if obs.cost() > 0 else 0.0

    def _score(self, obs: Observation) -> None:
        m = self._metric(obs)
        name = obs.arm.split("=", 1)[0] if obs.arm else None
        if obs.arm and obs.prompt_tokens > self.latency_guard_tokens:
            # a cold 30k-token prompt at an experimental value is not worth the user's wait, and it is not
            # comparable to the small-prompt requests that make up its stratum's baseline
            self.decisions.append(dict(action="dropped", arm=obs.arm, prompt_tokens=obs.prompt_tokens,
                                       why=f"over the {self.latency_guard_tokens}-token latency guard",
                                       at=time.strftime("%H:%M:%S")))
            return
        entry = (m, obs.prompt_tokens)
        if name in SETTINGS:                                # an arm request: it feeds the ARM series only
            series = self.arms.setdefault(f"{obs.stratum}|{name}|{obs.arm.split('=', 1)[1]}", [])
            series.append(entry)
            del series[:-120]
        else:                                               # a baseline request: it feeds every baseline series
            for setting in SETTINGS:
                if SETTINGS[setting]["kind"] == "propose" or setting in self.pinned:
                    continue
                cur = self._value_in_force(setting)
                series = self.baseline_arms.setdefault(f"{obs.stratum}|{setting}|base={cur}", [])
                series.append(entry)
                del series[:-120]                           # bounded: the last 120 of each series is plenty

    # --------------------------------------------------------------------------------------------- decide
    def _value_in_force(self, name: str):
        if name in self.pinned:
            return None
        if name in self.adopted:
            return self.adopted[name]["value"]
        return SETTINGS[name]["default"]

    def _gains(self, name: str, cur) -> dict[tuple[str, object], tuple[float, int]]:
        """(stratum, candidate value) -> (paired gain, pairs) for one setting, from the strata seen recently."""
        out: dict[tuple[str, object], tuple[float, int]] = {}
        for stratum in {o.stratum for o in self.observations[-200:]}:
            base = self.baseline_arms.get(f"{stratum}|{name}|base={cur}", [])
            for cand in self._candidates(name, cur):
                out[(stratum, cand)] = paired_median(base, self.arms.get(f"{stratum}|{name}|{cand}", []))
        return out

    def _maybe_decide(self) -> None:
        if self.mode == "off" or not self.observations:
            return
        if self.seen_session < self.cold_requests:          # a fresh engine's first requests are the slow ones
            return
        for name, spec in SETTINGS.items():
            if spec["kind"] == "propose" or name in self.pinned:
                continue
            cur = self._value_in_force(name)
            if cur is None:
                continue
            gains = self._gains(name, cur)
            # the best evidence for one value, and a refusal when another stratum that has enough pairs says the
            # same value is clearly WORSE: a setting has one value, so adopting on one stratum's word while another
            # contradicts it is how a tuner makes a box slower in the workload it was not watching
            best = max(((cand, stratum, gain, pairs) for (stratum, cand), (gain, pairs) in gains.items()
                        if pairs >= spec["min_pairs"] and gain >= spec["gain_bar"]),
                       key=lambda t: t[2], default=None)
            if best is None:
                continue
            cand, stratum, gain, pairs = best
            against = [(s, g) for (s, c), (g, p) in gains.items()
                       if c == cand and s != stratum and p >= spec["min_pairs"] and g <= -spec["gain_bar"]]
            if against:
                self.decisions.append(dict(action="declined", setting=name, value=cand, why="strata disagree",
                                           for_stratum=stratum, against=[s for s, _ in against],
                                           at=time.strftime("%H:%M:%S")))
                continue
            prev = self.adopted.get(name)
            if prev and prev["from_stratum"] == stratum and prev["value"] == cand:
                continue
            self._adopt(name, cand, stratum, gain, pairs)

    def _restore(self) -> None:
        if not self.restore_pending:
            return
        name = self.restore_pending
        cur = self._value_in_force(name)
        if cur is None or self._apply({name: cur}, why=f"restore after an arm for {name}"):
            self.restore_pending = None

    def _explore(self) -> None:
        """One arm at a time, alternating with the value in force: request N is the baseline, N+1 carries the arm,
        N+2 is the baseline again.  That is what makes the comparison paired (drift hits both arms) - and it costs
        one request in two, only while a setting still needs evidence."""
        if self.mode != "on" or self.exploring is not None or self.restore_pending or not self._idle():
            return
        if self.seen <= self._seen_at_arm:      # nothing has been served since the last arm: wait for traffic
            return
        if sum(self.explore_pairs.values()) >= self.explore_cap:
            return
        if self.seen_session < self.cold_requests:
            return
        # the setting with the least evidence so far, among those still short of min_pairs
        pool_names = [n for n in SETTINGS
                      if SETTINGS[n]["kind"] == "tune" and n not in self.pinned
                      and self._candidates(n, self._value_in_force(n))]
        # least evidence first, and every tunable setting gets a turn: only settings that have never had an arm
        # would otherwise be skipped forever (the first version explored pcie_frac and nothing else)
        pool_names.sort(key=lambda n: (self.explore_pairs.get(n, 0), n))
        name = next((n for n in pool_names
                     if self.explore_pairs.get(n, 0) < SETTINGS[n]["min_pairs"]), None)
        if name is None:
            return
        cur = self._value_in_force(name)
        arms = self._candidates(name, cur)
        if not arms:
            return
        value = arms[self.explore_pairs.get(name, 0) % len(arms)]
        if self._apply({name: value}, why=f"exploration arm for {name}"):
            self.exploring = (name, value)
            self._seen_at_arm = self.seen
            self.decisions.append(dict(action="exploring", setting=name, value=value,
                                       at=time.strftime("%H:%M:%S")))

    def _candidates(self, name: str, cur):
        arms = ARMS.get(name) or (cur,)
        return [v for v in arms if str(v) != str(cur)]

    def _adopt(self, name: str, value, stratum: str, gain: float, pairs: int) -> None:
        """Adopt one value for a setting.  The setting itself is global (the engine has one option), so the record
        says which stratum the evidence came from: `from_stratum`, not `stratum`, because the value applies to
        every stratum once it is in force."""
        rec = dict(value=value, from_stratum=stratum, gain=round(100 * gain, 2), pairs=pairs, session=self.session,
                   at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.adopted[name] = rec
        self.decisions.append(dict(setting=name, value=value, from_stratum=stratum, gain_pct=rec["gain"],
                                   pairs=pairs, applied=False, mode=self.mode))
        self.say(f"[strata] auto-tune: {name}={value} for {stratum} ({rec['gain']:+.1f}% over {pairs} pairs)")
        if self.mode == "on":
            self._apply({name: value}, why=f"adopted for {stratum}")
        self.save_state()

    # --------------------------------------------------------------------------------------------- apply
    def _idle(self) -> bool:
        st = getattr(self.service, "status", None) or {}
        return not st.get("busy") and not st.get("queued")

    def _apply(self, changes: dict, why: str) -> bool:
        """Send TUNE with the adopted values - only when nothing is in flight, because `tune()` reads the engine's
        shared reply stream and a request mid-stream would lose a line to it."""
        if self.mode != "on" or not changes:
            return False
        bad = {k: v for k, v in changes.items() if not setting_bounds_ok(k, v)}
        if bad:                                        # a value outside the setting's range never reaches the engine
            self.errors.append(f"refused locally (out of range): {bad}")
            return False
        if not self._idle():
            self.decisions.append(dict(action="deferred", changes=changes, why="a request is in flight",
                                       at=time.strftime("%H:%M:%S")))
            return False
        try:
            reply = self.engine.tune(changes)               # noqa: SLF001 - the wrapper we shipped with TUNE
        except Exception as e:                              # noqa: BLE001
            self.errors.append(f"tune({changes}) failed: {type(e).__name__}: {e}")
            return False
        refused = (reply or {}).get("refused") or []
        ok = not refused
        for item in refused:                                # the engine cannot take it here: stop offering it
            name = str(item).split("=", 1)[0].strip()
            if name in SETTINGS:
                self.pinned.add(name)
                self.errors.append(f"the engine refused {name}: {item} - not offered again on this build")
        self.decisions.append(dict(action="applied" if ok else "refused", changes=changes, why=why,
                                   refused=refused, at=time.strftime("%H:%M:%S")))
        for d in self.decisions:
            if d.get("setting") in changes and d.get("value") == changes[d["setting"]]:
                d["applied"] = ok
        self.save_state()
        return ok

    # --------------------------------------------------------------------------------------------- proposals
    def _propose(self, obs: Observation) -> None:
        """Settings that are sized at load, judged from what the request reported.  Evidence only: nothing is
        changed under a running engine, because these need a reload."""
        def note(name: str, value, evidence: str) -> None:
            cur = self.proposals.get(name)
            if cur and cur["value"] == value:
                cur["n"] += 1
                cur["evidence"] = evidence
            else:
                self.proposals[name] = dict(value=value, evidence=evidence, n=1,
                                            at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            self._write_proposals()

        if obs.kv_hit is not None and obs.kv_hit < 90.0 and obs.prompt_tokens >= 8000:
            note("kv_resident", "raise", f"{obs.kv_hit:.1f}% of KV block reads hit VRAM on a "
                                         f"{obs.prompt_tokens}-token prompt")
            note("kv_type", "int8", f"{obs.kv_hit:.1f}% KV hit rate at fp16 - int8 halves the footprint")
        if obs.expert_hit is not None and obs.expert_hit < 95.0:
            note("expert_cache", "raise", f"{obs.expert_hit:.1f}% expert hit rate "
                                          f"({obs.pcie_experts} experts sent over PCIe)")
        rate = obs.accept_rate
        if rate is not None and obs.offered >= 3 and rate < 0.4:
            note("spec_depth", "lower", f"{obs.accepted} of {obs.offered} drafts accepted - the window is too wide "
                                        f"for this text")
        if rate is not None and obs.offered >= 3 and rate >= 0.8:
            # the other half of the same signal, and the half that was missing: near-total acceptance means the
            # window runs out before the model stops guessing.  Measured on this box: --spec 4 accepted 59 of 75 on
            # code, --spec 6 read +5.2% there, and NOTHING proposed the raise because the rule only looked downward.
            note("spec_depth", "raise", f"{obs.accepted} of {obs.offered} drafts accepted - the window may be too "
                                        f"narrow for this text")
        if obs.park_ms is not None and obs.park_ms > 250.0 and obs.reuse_frac >= 0.9:
            note("conv_cache_mib", "raise", f"parking took {obs.park_ms:.0f} ms and the conversation reused "
                                            f"{100 * obs.reuse_frac:.0f}% of its prompt")
        if obs.evictions:
            note("conv_cache_mib", "raise", f"{obs.evictions} conversation(s) evicted - the cache is too small")

    def _write_proposals(self) -> None:
        """A proposal is for the NEXT start, so it has to exist outside this process.  Measured: nothing called
        apply_proposals(), so the file that a person or a start-up step is supposed to read was never written - the
        proposals lived in memory and in the state file only.  A read-only state dir must not fail a request."""
        try:
            self.apply_proposals()
        except OSError as e:
            self.errors.append(f"proposal write failed: {e}")

    def apply_proposals(self, path: Path | None = None) -> dict:
        """Write the proposals into a config for the NEXT start.  Flags are appended only if absent."""
        target = Path(path or (self.state_dir / "tuning-proposals.json"))
        out = {FLAG_OF[k]: v["value"] for k, v in self.proposals.items() if k in FLAG_OF}
        target.write_text(json.dumps({"for": self.model, "hardware": self.hw_key, "flags": out,
                                      "evidence": self.proposals}, indent=1))
        return out

    # --------------------------------------------------------------------------------------------- state
    STATE_VERSION = 2

    def load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}
        if data.get("version") != self.STATE_VERSION:
            # an older file's series are another format (bare numbers, no request size): keep the decisions and
            # the adopted values, drop the measurements rather than pair apples with oranges
            data["arms"], data["baselines"] = {}, {}
        self.adopted = data.get("adopted", {}) or {}
        self.proposals = data.get("proposals", {}) or {}
        self.decisions = data.get("decisions", [])[-200:] or []
        self.arms = {k: v[-40:] for k, v in (data.get("arms") or {}).items()}
        self.baseline_arms = {k: v[-40:] for k, v in (data.get("baselines") or {}).items()}
        self.seen = int(data.get("seen", 0))
        self.session = int(data.get("sessions", 0))
        return data

    def save_state(self) -> None:
        data = dict(version=self.STATE_VERSION, model=self.model, hardware=self.hw_key, mode=self.mode,
                    sessions=self.session,
                    seen=self.seen, adopted=self.adopted, proposals=self.proposals,
                    decisions=self.decisions[-200:], arms={k: v[-40:] for k, v in self.arms.items()},
                    baselines={k: v[-40:] for k, v in self.baseline_arms.items()},
                    errors=self.errors[-10:], updated=time.strftime("%Y-%m-%dT%H:%M:%S"))
        try:
            self.state_path.write_text(json.dumps(data, indent=1))
        except OSError as e:
            self.errors.append(f"state write failed: {e}")

    # --------------------------------------------------------------------------------------------- report
    def report(self) -> dict:
        # /v1/tuning payload.  `seen` and `seen_session` count THIS session (reset on every start), while
        # `seen_total` is the lifetime count carried in the state file; `requests_kept` is per-session too.
        # `seen` is kept for readers that predate the split, now with the per-session value.
        with self.lock:
            strata: dict[str, dict] = {}
            for o in self.observations[-200:]:
                s = strata.setdefault(o.stratum, dict(n=0, prompt_tokens=[], reuse=[], prompt_tps=[],
                                                      decode_tps=[], accept=[], kv_hit=[], cost=[]))
                s["n"] += 1
                s["prompt_tokens"].append(o.prompt_tokens)
                s["reuse"].append(100 * o.reuse_frac)
                s["prompt_tps"].append(o.prompt_tps)
                s["decode_tps"].append(o.decode_tps)
                s["cost"].append(o.cost())
                if o.accept_rate is not None:
                    s["accept"].append(100 * o.accept_rate)
                if o.kv_hit is not None:
                    s["kv_hit"].append(o.kv_hit)
            for s in strata.values():
                for k in ("prompt_tokens", "reuse", "prompt_tps", "decode_tps", "accept", "kv_hit", "cost"):
                    xs = s[k]
                    s[k] = dict(median=round(statistics.median(xs), 2) if xs else None,
                                p10=round(sorted(xs)[max(0, int(0.1 * len(xs)) - 1)], 2) if xs else None,
                                spread=round(mad(xs), 2) if xs else None, n=len(xs))
            pending = {}
            for name, spec in SETTINGS.items():
                cur = self._value_in_force(name)
                gains = self._gains(name, cur)
                (stratum, cand), (best_gain, best_pairs) = max(
                    gains.items(), key=lambda kv: kv[1][0] if kv[1][1] >= 2 else -1.0, default=((None, None), (0.0, 0)))
                best = f"{stratum}:{cand}" if stratum and best_pairs >= 2 else None
                if best is None:
                    best_gain, best_pairs = 0.0, 0
                if spec["kind"] != "propose":
                    pending[name] = dict(in_force=cur, explored=best, gain_pct=round(100 * best_gain, 2),
                                         pairs=best_pairs, min_pairs=spec["min_pairs"],
                                         enough_data=best_pairs >= spec["min_pairs"] and best_gain >= spec["gain_bar"])
            return dict(mode=self.mode, model=self.model, hardware=self.hw_key, session=self.session,
                        seen=self.seen_session, seen_session=self.seen_session, seen_total=self.seen,
                        requests_kept=len(self.observations),
                        state_file=str(self.state_path), strata=strata, adopted=self.adopted,
                        arms=pending, proposals=self.proposals, decisions=self.decisions[-12:],
                        errors=self.errors[-5:],
                        note=("shadow: deciding and recording, changing nothing" if self.mode == "shadow"
                              else ("on: adopted values are applied between requests" if self.mode == "on"
                                    else "off")))
