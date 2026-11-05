"""Tests for serve/autotune.py - the hot tuner's parsing, strata, pairing and adoption rules.

Run: .venv/bin/python -m pytest serve/test_autotune.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from serve.autotune import (AutoTuner, Observation, paired_median, parse_block, setting_bounds_ok,  # noqa: E402
                            stratum_of)

BLOCK = [
    "strata serve: prompt 4210 tokens = 0 reused + 4210 read in 3041 ms (1385.0 tok/s), 66 generated in "
    "1030 ms (64.1 tok/s), drafts accepted 2 of 3, 1 checkpoints",
    "strata serve: decode expert cache hit rate: 99.5% (12519 hits / 12583 lookups; 64 sent over PCIe)",
    "strata serve: KV streaming: 96.55% of 4872 block reads hit VRAM, 0.7 MiB read from RAM",
    "strata serve: conversation cache: parked 3004 tokens in 126.1 ms; parked=1 bytes=1 evictions=0",
]


def test_parse_block_reads_the_engine_accounting():
    obs = parse_block(BLOCK, {"spec_min_p": 0.5}, None)
    assert obs.prompt_tokens == 4210 and obs.reused == 0 and obs.generated == 66
    assert abs(obs.prompt_tps - 1384.4) < 1.0 and abs(obs.decode_tps - 64.1) < 0.2
    assert (obs.accepted, obs.offered) == (2, 3)
    assert obs.expert_hit == 99.5 and obs.pcie_experts == 64
    assert obs.kv_hit == 96.55 and obs.park_ms == 126.1
    assert obs.stratum == "cold/mid/terse"


def test_strata_never_pool_cold_and_warm():
    assert stratum_of(0.0, 4000, 60) == "cold/mid/terse"
    assert stratum_of(0.99, 4000, 60) == "warm/mid/terse"
    assert stratum_of(0.5, 900, 900) == "mixed/short/long"
    assert stratum_of(0.1, 30000, 10) == "cold/long/terse"


def test_paired_median_ignores_a_global_drift():
    # the same 20% drift on both arms must not look like a gain
    base = [10.0, 10.2, 9.8, 10.1]
    arm = [12.0, 12.24, 11.76, 12.12]
    gain, pairs = paired_median(base, arm)
    assert pairs == 4 and abs(gain - 0.2) < 1e-9


def test_pairing_refuses_a_size_mismatch():
    # a 55-token chat turn must never be paired against a 1,900-token prompt: that produced a +200% "gain"
    base = [(2.0, 55.0), (0.33, 1922.0)]
    arm = [(0.33, 1922.0)]
    gain, pairs = paired_median(base, arm)
    assert pairs == 1 and abs(gain) < 1e-9          # matched the same-sized request, not the cheap one
    gain2, pairs2 = paired_median([(2.0, 55.0)], [(0.5, 1900.0)])
    assert pairs2 == 0                               # nothing to compare: must not report a gain


def test_cancelled_reads_are_counted_from_what_was_read():
    obs = parse_block(["strata serve: prompt 98179 tokens = 0 reused + 12288 of 98179 read in 9000 ms "
                       "(1365.3 tok/s), 12 generated in 200 ms (60.0 tok/s), drafts accepted 0 of 0, "
                       "0 checkpoints"], {}, None)
    assert obs.read_tokens == 12288


def test_bounds_and_choices():
    assert setting_bounds_ok("spec_min_p", 0.7) and not setting_bounds_ok("spec_min_p", 1.5)
    assert setting_bounds_ok("kv_type", "int8") and not setting_bounds_ok("kv_type", "fp8")
    assert not setting_bounds_ok("not_a_setting", 1)


def _tuner(tmp_path, mode="on", **kw):
    class Svc:
        status = {"busy": False, "queued": 0}

        def v1_status(self):
            return {"device": "test"}

    class Eng:
        def __init__(self):
            self.calls = []

        def tune(self, changes):
            self.calls.append(changes)
            return {"applied": changes, "refused": []}

    class SvcWithStatus(Svc):
        pass

    svc, eng = SvcWithStatus(), Eng()
    t = AutoTuner(cfg={"args": [], "log": str(tmp_path / "e.log")}, engine=eng, service=svc, model="m",
                  mode=mode, state_dir=tmp_path, log_path=tmp_path / "e.log", say=lambda *a: None, **kw)
    return t, svc, eng


def _obs(t, prompt=4000, gen=60, prompt_ms=3000.0, decode_ms=1000.0, arm=None, reused=0):
    o = Observation(ts=0, stratum="", prompt_tokens=prompt, reused=reused, read_tokens=prompt - reused,
                    prompt_ms=prompt_ms, generated=gen, decode_ms=decode_ms, accepted=1, offered=2, arm=arm)
    o.stratum = stratum_of(o.reuse_frac, o.prompt_tokens, o.generated)
    return o


def test_adoption_needs_pairs_bar_and_gain(tmp_path):
    t, svc, eng = _tuner(tmp_path)
    t.adopted.pop("spec_min_p", None)
    # 12 pairs where the arm is 5% faster overall
    for _ in range(12):
        b = _obs(t)                                           # baseline request
        a = _obs(t, prompt_ms=3000.0 * 0.95, decode_ms=1000.0 * 0.95, arm="spec_min_p=0.7")
        t.observations.extend([b, a])
        t._score(b)
        t._score(a)
    t.explore_pairs["spec_min_p"] = 12
    t.seen = 20
    t._maybe_decide()
    assert "spec_min_p" in t.adopted and t.adopted["spec_min_p"]["gain"] > 3.0

    # a 1% arm is not adopted
    (tmp_path / "b").mkdir()
    t2, _, _ = _tuner(tmp_path / "b")
    for _ in range(12):
        b = _obs(t2)
        a = _obs(t2, prompt_ms=3000.0 * 0.99, decode_ms=1000.0 * 0.99, arm="spec_min_p=0.3")
        t2.observations.extend([b, a])
        t2._score(b)
        t2._score(a)
    t2.explore_pairs["spec_min_p"] = 12
    t2.seen = 20
    t2._maybe_decide()
    assert "spec_min_p" not in t2.adopted


def test_a_pinned_setting_is_never_explored(tmp_path):
    t, svc, eng = _tuner(tmp_path, pinned={"spec_min_p"})
    t.explore_pairs = {}
    t.seen = 50
    t._explore()
    assert all("spec_min_p" not in c for c in eng.calls)


def test_no_write_while_a_request_is_in_flight(tmp_path):
    t, svc, eng = _tuner(tmp_path)
    svc.status = {"busy": True, "queued": 0}
    assert t._apply({"adapt_every": 1}, why="test") is False and eng.calls == []
    svc.status = {"busy": False, "queued": 1}
    assert t._apply({"adapt_every": 1}, why="test") is False
    svc.status = {"busy": False, "queued": 0}
    assert t._apply({"adapt_every": 1}, why="test") is True and eng.calls[-1] == {"adapt_every": 1}


def test_an_out_of_range_value_never_reaches_the_engine(tmp_path):
    t, svc, eng = _tuner(tmp_path)
    assert t._apply({"spec_min_p": 2.5}, why="test") is False and eng.calls == []
    assert "out of range" in t.errors[-1]
    assert t._apply({"adapt_swaps": 9999}, why="test") is False and eng.calls == []


def test_adoption_is_declined_when_two_strata_disagree(tmp_path):
    """A setting has ONE value: adopting on the colder stratum's word while the warm one says it is worse is how a
    tuner slows down the workload it was not watching."""
    t, svc, eng = _tuner(tmp_path)
    # cold/mid/terse: the arm is 5% faster; warm/mid/terse: the same arm is 6% slower, both with enough pairs
    for _ in range(12):
        for stratum, factor, reused in (("cold/mid/terse", 0.95, 0), ("warm/mid/terse", 1.06, 4000)):
            b = _obs(t, reused=reused)
            a = _obs(t, reused=reused, prompt_ms=3000.0 * factor, decode_ms=1000.0 * factor, arm="spec_min_p=0.7")
            t.observations.extend([b, a])
            t._score(b)
            t._score(a)
    t.explore_pairs["spec_min_p"] = 24
    t.seen = 40
    t._maybe_decide()
    assert "spec_min_p" not in t.adopted
    assert any(d.get("action") == "declined" and d.get("why") == "strata disagree" for d in t.decisions)


def test_shadow_never_writes(tmp_path):
    t, svc, eng = _tuner(tmp_path, mode="shadow")
    t.exploring = None
    assert t._apply({"adapt_every": 1}, why="test") is False and eng.calls == []


def test_latency_guard_drops_a_big_prompt_arm(tmp_path):
    t, svc, eng = _tuner(tmp_path, latency_guard_tokens=8000)
    t.explore_pairs["spec_min_p"] = 1
    t._score(_obs(t, prompt=30000, arm="spec_min_p=0.7"))
    assert t.arms == {}
    assert t.decisions[-1]["action"] == "dropped"


def test_proposals_come_from_the_evidence(tmp_path):
    t, svc, eng = _tuner(tmp_path)
    o = _obs(t, prompt=12000)
    o.kv_hit, o.expert_hit, o.accepted, o.offered, o.pcie_experts = 71.0, 88.0, 1, 4, 900
    t._propose(o)
    assert t.proposals["kv_resident"]["value"] == "raise"
    assert t.proposals["kv_type"]["value"] == "int8"
    assert t.proposals["expert_cache"]["value"] == "raise"
    assert t.proposals["spec_depth"]["value"] == "lower"
    flags = t.apply_proposals()
    assert flags.get("--kv") == "int8"


def test_state_round_trips(tmp_path):
    t, svc, eng = _tuner(tmp_path)
    t.adopted["spec_min_p"] = dict(value=0.7, stratum="cold/mid/terse", gain=4.4, pairs=12, session=1)
    t.seen = 7
    t.save_state()
    t2, _, _ = _tuner(tmp_path)
    assert t2.adopted["spec_min_p"]["value"] == 0.7 and t2.seen == 7


def test_report_shape(tmp_path):
    t, svc, eng = _tuner(tmp_path)
    for _ in range(3):
        t.observations.append(_obs(t))
    r = t.report()
    assert r["mode"] == "on" and "cold/mid/terse" in r["strata"]
    assert r["strata"]["cold/mid/terse"]["n"] == 3
    assert json.dumps(r)                      # serialisable


def test_near_total_acceptance_proposes_a_wider_window(tmp_path):
    """The rule only looked downward: 1 of 4 accepted proposed a lower depth, while 4 of 4 proposed nothing - and the
    raise is what this box wanted (--spec 4 accepted 59 of 75, --spec 6 read +5.2% on code)."""
    t, svc, eng = _tuner(tmp_path)
    o = _obs(t, prompt=2000)
    o.accepted, o.offered = 4, 4
    t._propose(o)
    assert t.proposals["spec_depth"]["value"] == "raise", t.proposals
    assert "narrow" in t.proposals["spec_depth"]["evidence"]


def test_a_proposal_is_written_for_the_next_start(tmp_path):
    """A proposal is only useful if it survives the process.  apply_proposals() existed and nothing called it, so the
    file a person (or a start-up step) is meant to read was never written - the proposal lived in memory and in the
    state file, and the "for the next start" half of the feature produced nothing."""
    t, svc, eng = _tuner(tmp_path)
    o = _obs(t, prompt=2000)
    o.accepted, o.offered = 4, 4
    t._propose(o)
    f = tmp_path / "tuning-proposals.json"
    assert f.exists(), "no proposals file was written"
    body = json.loads(f.read_text())
    assert body["flags"]["--spec"] == "raise", body
    assert "evidence" in body and body["evidence"]["spec_depth"]["n"] == 1
