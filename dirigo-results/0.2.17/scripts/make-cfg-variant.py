#!/usr/bin/env python3
"""make-cfg-variant.py <src-config> <dst-config> KEY=VAL [KEY=VAL ...]

Copies a tuned run config and sets (or replaces) keys in its "env" block - the engine's own environment,
which is the only way an engine variable reaches the engine (serve/server.py builds the child env itself).
"""
import json, sys
src, dst, *pairs = sys.argv[1:]
cfg = json.load(open(src))
env = dict(cfg.get("env") or {})
for p in pairs:
    k, v = p.split("=", 1)
    env[k] = v
cfg["env"] = env
json.dump(cfg, open(dst, "w"), indent=2)
print(dst, "->", json.dumps(env))
