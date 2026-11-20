"""Tests for the per-request code/prose retune (Service.code_tune_sampling).

Run: .venv/bin/python -m pytest serve/test_code_tune.py -q

The contract that matters: off changes nothing, shadow changes nothing but counts, on sets only the engine's
per-request keys, a caller's own tuning always wins, and nothing in here can fail a request.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from serve import server as S  # noqa: E402

CODE_TEXT = "Write a Python function that reverses a linked list, with a test."
PROSE_TEXT = "Explain why the sky is blue, in one paragraph."


class Tok:
    def __init__(self, text="", fail=False):
        self.text, self.fail = text, fail

    def decode(self, ids, errors="replace"):
        if self.fail:
            raise RuntimeError("tokenizer is gone")
        return self.text


def service(text="", mode="off", profiles=None, fail=False):
    svc = S.Service.__new__(S.Service)          # no __init__: only the fields this path touches
    svc.tok = Tok(text, fail)
    svc.code_tune_mode = mode
    svc.code_tune_profiles = profiles or {}
    svc.code_tune_stats = {"labelled": {"code": 0, "prose": 0}, "applied": {"code": 0, "prose": 0},
                           "shadowed": {"code": 0, "prose": 0}, "last": None}
    return svc


PROFILES = {"code": {"spec_min_p": 0.5}, "prose": {"spec_min_p": 0.9}}


def test_off_is_a_no_op():
    svc = service(CODE_TEXT, "off", PROFILES)
    sampling = {"temperature": 0}
    assert S.Service.code_tune_sampling(svc, [1, 2, 3], sampling) is sampling
    assert svc.code_tune_stats["labelled"] == {"code": 0, "prose": 0}


def test_shadow_counts_and_does_not_apply():
    svc = service(CODE_TEXT, "shadow", PROFILES)
    sampling = {"temperature": 0}
    out = S.Service.code_tune_sampling(svc, [1, 2, 3], sampling)
    assert out is sampling and "strata_tune" not in out
    assert svc.code_tune_stats["labelled"]["code"] == 1
    assert svc.code_tune_stats["shadowed"]["code"] == 1
    assert svc.code_tune_stats["applied"]["code"] == 0


def test_on_applies_the_label_profile():
    svc = service(CODE_TEXT, "on", PROFILES)
    out = S.Service.code_tune_sampling(svc, [1, 2, 3], {"temperature": 0})
    assert out["strata_tune"] == {"spec_min_p": 0.5}
    assert out["temperature"] == 0                       # the rest of the request is untouched
    svc = service(PROSE_TEXT, "on", PROFILES)
    assert S.Service.code_tune_sampling(svc, [1], {})["strata_tune"] == {"spec_min_p": 0.9}


def test_a_callers_own_setting_wins():
    svc = service(CODE_TEXT, "on", PROFILES)
    out = S.Service.code_tune_sampling(svc, [1], {"strata_tune": {"spec_min_p": 0.7, "pcie_frac": 0.2}})
    assert out["strata_tune"] == {"spec_min_p": 0.7, "pcie_frac": 0.2}
    assert svc.code_tune_stats["applied"]["code"] == 0


def test_no_profile_for_the_label_changes_nothing():
    svc = service(CODE_TEXT, "on", {"prose": {"spec_min_p": 0.9}})
    sampling = {"temperature": 0}
    assert S.Service.code_tune_sampling(svc, [1], sampling) is sampling
    assert svc.code_tune_stats["labelled"]["code"] == 1


def test_a_tokenizer_failure_never_fails_the_request():
    svc = service("", "on", PROFILES, fail=True)
    sampling = {"temperature": 0}
    assert S.Service.code_tune_sampling(svc, [1], sampling) is sampling


def test_set_code_tune_filters_to_the_engine_per_request_keys():
    svc = service("", "on")
    S.Service.set_code_tune(svc, "on", {"code": {"spec_min_p": "0.5", "dial": 3}, "prose": {"pcie_frac": 0.1},
                                        "nonsense": {"spec_min_p": 9}})
    assert svc.code_tune_profiles == {"code": {"spec_min_p": 0.5}, "prose": {"pcie_frac": 0.1}}
    S.Service.set_code_tune(svc, "sometimes", None)
    assert svc.code_tune_mode == "off" and svc.code_tune_profiles == {}


def test_draft_depth_is_forwarded_per_request():
    keys = S.StrataEngine.sampling_keys({"strata_tune": {"spec": 2}})
    assert " spec=2" in keys, keys
    for bad in (1, 0, -3, 33, True, "4", 4.5):
        keys = S.StrataEngine.sampling_keys({"strata_tune": {"spec": bad}})
        assert "spec=" not in keys, (bad, keys)


def test_a_depth_profile_is_applied_and_floored_at_two():
    svc = service(CODE_TEXT, "on", {"code": {"spec": 2}})
    out = S.Service.code_tune_sampling(svc, [1], {})
    assert out["strata_tune"] == {"spec": 2.0}
    assert " spec=2" in S.StrataEngine.sampling_keys(out)


def test_report_shape():
    svc = service(CODE_TEXT, "shadow", PROFILES)
    S.Service.code_tune_sampling(svc, [1], {})
    rep = S.Service.code_tune_report(svc)
    assert rep["mode"] == "shadow" and rep["profiles"]["code"]["spec_min_p"] == 0.5
    assert rep["labelled"]["code"] == 1 and "last" not in rep
