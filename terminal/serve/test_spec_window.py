"""The draft-window rule: downshift for ordinary requests, keep the start-up depth for retrieval.

Measured on the B70 (Arc Pro B70, Qwen3.8-flash-next-coder IQ1_M, engine 0.1.58, 2026-11-07, two interleaved
pairs with bench_prefill.py + strata-longprobe.py): spec 4 against the deployed 6 is +5.0 % fresh 4.2 k,
+3.6 % fresh 8.8 k, +7.5 % follow-up 4.5 k decode, and -9.9 % on a 7.8 k retrieval needle.  So the rule cannot
be one constant: it keys on whether the prompt is retrieval-shaped.
"""
import unittest

from serve.server import Service, StrataEngine, TUNE_PROMPT_TAIL


class Stub:
    def __init__(self, text=""):
        self.spec_window_down = 4
        self.spec_window_needle_min = 6000
        self.spec_window_stats = {"downshifted": 0, "kept": 0, "last": None}
        self.tok = type("Tok", (), {"decode": staticmethod(lambda ids, errors=None: text)})()


apply = Service.spec_window_sampling    # unbound: the stub supplies self


class SpecWindow(unittest.TestCase):
    def setUp(self):
        self.svc = Stub("Summarise this note in three bullets.")

    def test_ordinary_prompt_is_downshifted(self):
        out = apply(self.svc, list(range(4000)), {"temperature": 0})
        self.assertEqual(out["strata_tune"]["spec"], 4)
        self.assertEqual(self.svc.spec_window_stats["downshifted"], 1)
        self.assertIn("spec=4", StrataEngine.sampling_keys(out))

    def test_long_retrieval_prompt_keeps_the_startup_depth(self):
        self.svc.tok = type("Tok", (), {"decode": staticmethod(
            lambda ids, errors=None: "Question: under which reference is the certificate filed?")})()
        out = apply(self.svc, list(range(7000)), {"temperature": 0})
        self.assertNotIn("strata_tune", out)
        self.assertEqual(self.svc.spec_window_stats["kept"], 1)

    def test_a_long_prompt_without_a_question_is_not_retrieval(self):
        out = apply(self.svc, list(range(20000)), {"temperature": 0})
        self.assertEqual(out["strata_tune"]["spec"], 4)

    def test_a_short_question_is_not_retrieval(self):
        self.svc.tok = type("Tok", (), {"decode": staticmethod(lambda ids, errors=None: "What is 17*23?")})()
        out = apply(self.svc, list(range(300)), {"temperature": 0})
        self.assertEqual(out["strata_tune"]["spec"], 4)

    def test_client_spec_wins(self):
        out = apply(self.svc, list(range(4000)), {"strata_tune": {"spec": 6}})
        self.assertEqual(out["strata_tune"]["spec"], 6)
        self.assertEqual(self.svc.spec_window_stats["downshifted"], 0)

    def test_client_tune_is_kept_and_extended(self):
        out = apply(self.svc, list(range(4000)), {"strata_tune": {"spec_min_p": 0.4}})
        self.assertEqual(out["strata_tune"], {"spec_min_p": 0.4, "spec": 4})

    def test_off_means_untouched(self):
        self.svc.spec_window_down = 0
        self.assertNotIn("strata_tune", apply(self.svc, list(range(4000)), {"temperature": 0}))

    def test_no_ids_untouched(self):
        self.assertNotIn("strata_tune", apply(self.svc, [], {"temperature": 0}))

    def test_decode_failure_does_not_fail_the_request(self):
        def boom(ids, errors=None):
            raise RuntimeError("no tokenizer")
        self.svc.tok = type("Tok", (), {"decode": staticmethod(boom)})()
        out = apply(self.svc, list(range(4000)), {"temperature": 0})
        self.assertNotIn("strata_tune", out)

    def test_setter_clamps_and_reports(self):
        svc = Stub()
        Service.set_spec_window(svc, 99, 1000)
        self.assertEqual(svc.spec_window_down, 8)
        self.assertEqual(svc.spec_window_needle_min, 1000)
        Service.set_spec_window(svc, "not a number")
        self.assertEqual(svc.spec_window_down, 0)
        rep = Service.spec_window_report(svc)
        self.assertEqual(rep["down"], 0)
        self.assertIn("downshifted", rep)

    def test_only_the_prompt_tail_is_read(self):
        seen = {}

        def grab(ids, errors=None):
            seen["n"] = len(ids)
            return "x"
        self.svc.tok = type("Tok", (), {"decode": staticmethod(grab)})()
        apply(self.svc, list(range(50000)), {"temperature": 0})
        self.assertLessEqual(seen["n"], TUNE_PROMPT_TAIL)


if __name__ == "__main__":
    unittest.main()
