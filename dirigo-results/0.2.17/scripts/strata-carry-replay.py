#!/usr/bin/env python3
"""strata-carry-replay.py - replay the Dirigo carry onto a new upstream tag whose tree was REORGANISED.

    strata-carry-replay.py <new-tree> <new-tag> <old-fork-tree> <old-tag>

v0.2.17 split the repository into app/ engine/ terminal/ docs/ tools/: src/ include/ sycl/ metal/ cmake/ and the
root CMakeLists live under engine/, while serve/ strata_cli/ strata_control/ tools/ setup.py strata.py conftest.py
and the requirements files live under terminal/.  This script maps each path the fork changed to its new home, then
merges (ours = <new-tag>:<mapped path>, base = <old-tag>:<original path>, theirs = the old fork tree's file) and
writes the result into <new-tree> at the mapped path.  Fork-only files land at their mapped home too (a fork test
next to the server it tests), and files whose upstream counterpart is gone are reported instead of guessed at.

UNDO: rm ~/bin/strata-carry-replay.py
"""
import subprocess
import sys
from pathlib import Path

PREFIX = [
    ("src/", "engine/src/"), ("include/", "engine/include/"), ("sycl/", "engine/sycl/"),
    ("metal/", "engine/metal/"), ("cmake/", "engine/cmake/"), ("third_party/", "engine/third_party/"),
    ("CMakeLists.txt", "engine/CMakeLists.txt"), ("CMakePresets.json", "engine/CMakePresets.json"),
    ("serve/", "terminal/serve/"), ("strata_cli/", "terminal/strata_cli/"),
    ("strata_control/", "terminal/strata_control/"), ("tools/", "terminal/tools/"),
    ("bench/", "terminal/bench/"), ("tests/", "terminal/tests/"),
    ("setup.py", "terminal/setup.py"), ("strata.py", "terminal/strata.py"),
    ("conftest.py", "terminal/conftest.py"), ("requirements", "terminal/requirements"),
]


def map_path(p: str) -> str:
    for a, b in PREFIX:
        if p.startswith(a):
            return b + p[len(a):]
    return p


def git(tree, *args):
    r = subprocess.run(["git", "-C", tree, *args], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def show(tree, rev_path, binary=False):
    return git(tree, "show", rev_path)


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    new_tree, new_tag, old_tree, old_tag = sys.argv[1:5]
    paths = (git(old_tree, "diff", "--name-only", old_tag, "HEAD") or b"").decode().split()
    rows = []
    for oldp in paths:
        newp = map_path(oldp)
        src = Path(old_tree) / oldp
        if not src.exists() and not src.is_symlink():
            rows.append((oldp, newp, "fork-deleted", ""))
            continue
        theirs = show(old_tree, f"HEAD:{oldp}", binary=True)
        dest = Path(new_tree) / newp
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            dest.unlink(missing_ok=True)
            dest.symlink_to(theirs.decode().strip())
            rows.append((oldp, newp, "symlink", theirs.decode().strip()))
            continue
        if theirs is None:
            rows.append((oldp, newp, "unreadable", ""))
            continue
        base = show(old_tree, f"{old_tag}:{oldp}", binary=True)
        ours = show(new_tree, f"{new_tag}:{newp}", binary=True)
        if ours is None and base is None:                   # the fork's own file
            dest.write_bytes(theirs)
            rows.append((oldp, newp, "added", ""))
            continue
        if ours is None:                                    # upstream dropped the original
            rows.append((oldp, newp, "CONFLICT-upstream-deleted", "not written"))
            continue
        if ours == base:
            dest.write_bytes(theirs)
            rows.append((oldp, newp, "copy-fork", ""))
            continue
        if theirs == base:
            dest.write_bytes(ours)
            rows.append((oldp, newp, "keep-upstream", ""))
            continue
        tmp = Path(new_tree) / ".replay.tmp"
        (tmp.parent / ".replay.o").write_bytes(ours)
        (tmp.parent / ".replay.b").write_bytes(base)
        (tmp.parent / ".replay.t").write_bytes(theirs)
        r = subprocess.run(["git", "merge-file", "-p", ".replay.o", ".replay.b", ".replay.t"],
                           cwd=new_tree, capture_output=True)
        dest.write_bytes(r.stdout)
        for s in (".replay.o", ".replay.b", ".replay.t"):
            (tmp.parent / s).unlink(missing_ok=True)
        rows.append((oldp, newp, "merged" if r.returncode == 0 else f"CONFLICT x{r.returncode}", ""))
    w = max(len(r[1]) for r in rows)
    bad = 0
    for oldp, newp, status, note in rows:
        if status.startswith("CONFLICT"):
            bad += 1
        print(f"  {newp:<{w}}  {status:22} {note}")
    print(f"\n{len(rows)} paths, {bad} needing a human")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
