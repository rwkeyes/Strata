#!/usr/bin/env python3
"""The serving-measurement primitives the scripts in this directory share.

Both `warmup-swing.py` and `spec-min-p-pair.py` measured decode rate by streaming a completion and timing the delta
between the first and last token.  That loop, the prompt loader and the JSON GET were identical copies; keeping one
copy here means a fix to the measurement lands in both, and the two can never drift apart.

Decode rate excludes prefill by construction: the clock starts at the first streamed token.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "strata"          # the serving model name is not used for routing here; any non-empty value works
DEFAULT_CHARS = 9000
REPO_ROOT = Path(__file__).resolve().parent.parent


def default_prompt_file() -> Path:
    """A real source file of this repository, used as a long code-continuation prompt.

    Defaults to this tree rather than an absolute path to a developer's checkout, so the script works from any clone.
    """
    return REPO_ROOT / "tools" / "calibrate.py"


def load_prompt(path: Path, chars: int = DEFAULT_CHARS) -> str:
    text = Path(path).read_text(encoding="utf-8", errors="replace")[:chars]
    return ("Continue this file from where it stops, reproducing the same style and identifiers:\n\n"
            + text + "\n\ndef _next_part() -> None:\n")


def get(url: str, path: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url + path, timeout=timeout) as r:
        return json.load(r)


def stream_rate(base_url: str, text: str, max_tokens: int = 128, model: str = DEFAULT_MODEL,
                tune: dict | None = None, timeout: int = 600) -> float:
    """Decode tokens/second for one request, measured from the first streamed token to the last.

    Returns 0.0 when fewer than four tokens arrive, so a truncated or failed request cannot look like a fast one.
    """
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
            "messages": [{"role": "user", "content": text}]}
    if tune:
        body["strata_tune"] = tune
    req = urllib.request.Request(base_url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    first = last = None
    n = 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            if not raw.startswith(b"data: {"):
                continue
            try:
                d = json.loads(raw[6:])
            except ValueError:
                continue
            delta = (d.get("choices") or [{}])[0].get("delta") or {}
            if delta.get("content") or delta.get("reasoning_content"):
                now = time.time()
                first = first or now
                last = now
                n += 1
    return (n - 1) / (last - first) if first and last and n > 3 and last > first else 0.0
