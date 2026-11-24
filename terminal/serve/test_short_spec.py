"""The short-answer depth rule: spec=4 when the ANSWER BUDGET is short, never when the client said otherwise.

Measured on the B70 (Arc Pro B70, Qwen3.8-flash-next-coder IQ1_M, 20 extraction prompts x 2 rounds): at
max_tokens 8, spec=4 against the start-up 6 is +11.3 % decode (34/40 pairs, both rounds positive) and -1.9 %
wall. At max_tokens 128 the same change is a loss, which is why the rule keys on max_tokens and not on the
prompt kind.
"""
import unittest

from serve.server import Service, StrataEngine


class Stub:
    def __init__(self):
        self.short_spec = 4
        self.short_spec_max_tokens = 16
        self.short_spec_stats = {"applied": 0, "last": None}


apply = Service.apply_short_spec   # unbound: the stub supplies self


class ShortAnswerDepth(unittest.TestCase):
    def setUp(self):
        self.svc = Stub()

    def test_short_budget_gets_the_deeper_chain(self):
        out = apply(self.svc, {"temperature": 0}, 8)
        self.assertEqual(out["strata_tune"]["spec"], 4)
        self.assertEqual(self.svc.short_spec_stats["applied"], 1)
        self.assertIn("spec=4", StrataEngine.sampling_keys(out))

    def test_boundary_is_inclusive(self):
        self.assertIn("strata_tune", apply(self.svc, {}, 16))
        self.assertNotIn("strata_tune", apply(self.svc, {}, 17))

    def test_long_budget_untouched(self):
        self.assertNotIn("strata_tune", apply(self.svc, {"temperature": 0}, 128))

    def test_client_wins(self):
        out = apply(self.svc, {"strata_tune": {"spec": 6}}, 8)
        self.assertEqual(out["strata_tune"]["spec"], 6)
        self.assertEqual(self.svc.short_spec_stats["applied"], 0)

    def test_client_tune_is_kept_and_extended(self):
        out = apply(self.svc, {"strata_tune": {"pcie_frac": 0.3}}, 8)
        self.assertEqual(out["strata_tune"], {"pcie_frac": 0.3, "spec": 4})

    def test_input_not_mutated(self):
        given = {"strata_tune": {"pcie_frac": 0.3}}
        apply(self.svc, given, 8)
        self.assertNotIn("spec", given["strata_tune"])

    def test_off_by_default(self):
        self.svc.short_spec = 0
        self.assertNotIn("strata_tune", apply(self.svc, {}, 8))

    def test_no_budget(self):
        for max_new in (0, None):
            self.assertNotIn("strata_tune", apply(self.svc, {}, max_new))

    def test_engine_never_gets_a_depth_it_cannot_have(self):
        self.assertNotIn("spec=", StrataEngine.sampling_keys({"strata_tune": {"spec": 99}}))
        self.assertNotIn("spec=", StrataEngine.sampling_keys({"strata_tune": {"spec": 1}}))
        self.assertNotIn("spec=", StrataEngine.sampling_keys({"strata_tune": {"spec": True}}))

    def test_set_short_spec_sanitises(self):
        svc = Stub()
        Service.set_short_spec(svc, "4", 0)          # a JSON string, a zero threshold
        self.assertEqual((svc.short_spec, svc.short_spec_max_tokens), (0, 16))
        Service.set_short_spec(svc, 4, 8)
        self.assertEqual((svc.short_spec, svc.short_spec_max_tokens), (4, 8))


if __name__ == "__main__":
    unittest.main()
