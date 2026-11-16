#!/usr/bin/env python3
"""strata-carry-merge.py - replay the Dirigo carry onto a new upstream tag, file by file, 3-way.

    strata-carry-merge.py <new-tree> <new-tag> <old-fork-tree> <old-tag>

For every path the fork changed between <old-tag> and <old-fork-tree>'s HEAD:
    ours   = <new-tag>:<path>        (upstream's file at the new tag)
    base   = <old-tag>:<path>        (upstream's file at the old tag)
    theirs = <old-fork-tree>/<path>  (the fork's file)
and writes the merge into <new-tree>. Clean cases are copied; overlapping ones are merged with
`git merge-file`, which leaves conflict markers and prints the count. Nothing is guessed: a path that
needs a human is listed as CONFLICT and left with markers in place.

UNDO: rm ~/bin/strata-carry-merge.py
"""
import subprocess
import sys
from pathlib import Path


def git(tree, *args, binary=False):
    p = subprocess.run(["git", "-C", tree, *args], capture_output=True)
    if p.returncode != 0:
        return None
    return p.stdout if binary else p.stdout.decode()


def show(tree, rev_path, binary=False):
    return git(tree, "show", rev_path, binary=binary)


def mode_of(tree, rev_path):
    out = git(tree, "ls-tree", rev_path)
    return out.split()[0] if out and out.strip() else None


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    new_tree, new_tag, old_tree, old_tag = sys.argv[1:5]
    names = git(old_tree, "diff", "--name-only", old_tag, "HEAD").split()
    rows = []
    for path in names:
        theirs_bytes = show(old_tree, f"HEAD:{path}", binary=True)
        if theirs_bytes is None:                     # deleted in the fork: nothing to carry
            rows.append((path, "fork-deleted", ""))
            continue
        tree_mode = mode_of(old_tree, f"HEAD:{path}")
        dest = Path(new_tree) / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(old_tree, path).is_symlink() or tree_mode == "120000":
            dest.unlink(missing_ok=True)
            dest.symlink_to(theirs_bytes.decode().strip())
            rows.append((path, "symlink", theirs_bytes.decode().strip()))
            continue
        base = show(old_tree, f"{old_tag}:{path}", binary=True)
        ours = show(new_tree, f"{new_tag}:{path}", binary=True)

        if base is None and ours is None:             # the fork's own new file
            dest.write_bytes(theirs_bytes)
            rows.append((path, "added", ""))
            continue
        if ours is None:                              # upstream deleted a file the fork changes
            rows.append((path, "CONFLICT-upstream-deleted", "left as upstream (absent)"))
            continue
        if ours == base:                              # upstream did not touch it
            dest.write_bytes(theirs_bytes)
            rows.append((path, "copy-fork", ""))
            continue
        if theirs_bytes == base:                      # the fork did not touch it
            dest.write_bytes(ours)
            rows.append((path, "keep-upstream", ""))
            continue

        # genuine 3-way: write ours, merge theirs in
        tmp_ours = dest.with_suffix(dest.suffix + ".ours")
        tmp_theirs = dest.with_suffix(dest.suffix + ".theirs")
        tmp_base = dest.with_suffix(dest.suffix + ".base")
        tmp_ours.write_bytes(ours)
        tmp_theirs.write_bytes(theirs_bytes)
        tmp_base.write_bytes(base)
        rc = subprocess.run(["git", "merge-file", "-p", str(tmp_ours), str(tmp_base), str(tmp_theirs)],
                            capture_output=True)
        dest.write_bytes(rc.stdout)
        for t in (tmp_ours, tmp_theirs, tmp_base):
            t.unlink(missing_ok=True)
        rows.append((path, "merged" if rc.returncode == 0 else f"CONFLICT x{rc.returncode}", ""))

    width = max(len(r[0]) for r in rows)
    bad = 0
    for path, status, note in rows:
        if status.startswith("CONFLICT"):
            bad += 1
        print(f"  {path:<{width}}  {status}  {note}")
    print(f"\n{len(rows)} paths, {bad} needing a human")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
