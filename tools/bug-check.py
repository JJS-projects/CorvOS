#!/usr/bin/env python3
"""Deliberate-bug check: break one thing, run the suite, say which tests failed.

    tools/bug-check.py FILE OLD NEW

Replaces the text OLD (which must occur exactly once) with NEW in FILE, runs
`make test`, then restores FILE whatever happens. Prints the failing tests, or
"none" if nothing noticed the bug. The results go in the PR's table (see
docs/workflow.md, Practices).
"""
import subprocess
import sys

if len(sys.argv) != 4:
    sys.exit(__doc__)
path, old, new = sys.argv[1:]
original = open(path).read()
if original.count(old) != 1:
    sys.exit(f"{path}: expected exactly one occurrence of {old!r}, found {original.count(old)}")
try:
    with open(path, "w") as f:
        f.write(original.replace(old, new))
    run = subprocess.run(["make", "test"], capture_output=True, text=True)
finally:
    with open(path, "w") as f:
        f.write(original)
failed = [l.split()[1] for l in run.stdout.splitlines() if l.startswith("FAIL")]
if not failed and run.returncode != 0:
    failed = ["(make failed: " + (run.stderr.strip().splitlines() or ["?"])[-1] + ")"]
print(", ".join(failed) if failed else "none")
