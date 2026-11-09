#!/usr/bin/env python3
"""Tests for scripts/serving_measure.py, the module both measurement scripts now share.

    .venv/bin/python -m pytest scripts/test_serving_measure.py -q

The prompt loader is pure and always tested.  The streaming measurement needs a serving engine, so it SKIPS when none is
reachable rather than failing — a check that cannot read its input must skip, never report red.
"""
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serving_measure as sm  # noqa: E402

BASE = "http://127.0.0.1:8095"


def serving() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/v1/status", timeout=3) as r:
            return bool(json.load(r).get("loaded"))
    except Exception:
        return False


def test_default_prompt_file_exists_and_is_this_tree():
    p = sm.default_prompt_file()
    assert p.exists(), f"default prompt file {p} does not exist"
    # it must be inside the repo, not an absolute path to somebody's checkout
    assert str(p).startswith(str(sm.REPO_ROOT)), p


def test_load_prompt_is_non_empty_and_framed():
    text = sm.load_prompt(sm.default_prompt_file(), 500)
    assert text.startswith("Continue this file")
    assert text.endswith("def _next_part() -> None:\n")
    body = text.split("\n\n", 1)[1]
    assert len(body) > 200, "the prompt body is suspiciously short — the file was not read"


def test_load_prompt_honours_the_char_budget():
    short = sm.load_prompt(sm.default_prompt_file(), 100)
    long = sm.load_prompt(sm.default_prompt_file(), 5000)
    assert len(short) < len(long)


def test_a_missing_prompt_file_raises():
    with pytest.raises(OSError):
        sm.load_prompt(Path("/nonexistent/prompt.py"))


@pytest.mark.skipif(not serving(), reason="no serving engine on :8095")
def test_stream_rate_measures_a_real_request():
    text = sm.load_prompt(sm.default_prompt_file(), 400)
    rate = sm.stream_rate(BASE, text, max_tokens=32, tune={"spec": 6})
    assert rate > 0, "the measurement returned 0 tok/s — the stream loop or the ruler is broken"


@pytest.mark.skipif(not serving(), reason="no serving engine on :8095")
def test_the_two_scripts_share_the_module():
    """The DRY claim: neither script keeps its own copy of the stream loop."""
    for name in ("warmup-swing.py", "spec-min-p-pair.py"):
        src = (Path(__file__).resolve().parent / name).read_text()
        assert "serving_measure" in src, f"{name} does not import the shared module"
        assert "data: {" not in src, f"{name} still parses the event stream itself"


class _Stream(BaseHTTPRequestHandler):
    """A stub engine: five content deltas 100 ms apart, plus chunks that carry no token.

    If the ruler counts chunks instead of tokens it reads twice the true rate, so the assertion below can tell the
    two apart without needing a GPU.
    """

    def do_POST(self):                      # noqa: N802 - http.server's spelling
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [{"delta": {"role": "assistant"}},                      # no token
                  {"delta": {"content": "a"}},
                  {"delta": {}},                                          # no token
                  {"delta": {"content": "b"}},
                  {"delta": {"reasoning_content": "c"}},
                  {"delta": {"content": ""}},                             # empty: not a token
                  {"delta": {"content": "d"}},
                  {"delta": {"content": "e"}},
                  {"finish_reason": "stop"}]                              # no token
        for ev in events:
            body = {"choices": [ev]} if "delta" in ev else {"choices": [{"delta": {}, **ev}]}
            self.wfile.write(b"data: " + json.dumps(body).encode() + b"\n\n")
            self.wfile.flush()
            delta = ev.get("delta") or {}
            # only a real token advances the clock: 5 tokens over four 0.2 s gaps is ~5 tok/s. Counting the
            # structural chunks reads ~10, and counting the empty-content chunk reads 7.5 — both outside the band.
            if delta.get("content") or delta.get("reasoning_content"):
                time.sleep(0.2)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *a):
        pass


def test_the_ruler_counts_tokens_not_chunks():
    srv = HTTPServer(("127.0.0.1", 0), _Stream)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        rate = sm.stream_rate(base, "x", max_tokens=8)
    finally:
        srv.server_close()
    # 5 tokens over four 0.2 s intervals is 5.0 tok/s. One extra counted token reads 6.25 and counting every chunk
    # reads 10.0, so a +/-16 % band pins the count without being sensitive to scheduler jitter.
    assert abs(rate - 5.0) < 0.8, (
        f"{rate:.2f} tok/s, expected 5.0 +/- 0.8 — the ruler is counting something other than 5 tokens")
