#!/usr/bin/env python3
"""Run a list of deliberate-bug checks and write one summary.

    tools/bug-check-batch.py MUTATIONS [-o SUMMARY]
    (run it in the background: each mutation is a full `make test`, so a list takes a while)

MUTATIONS is a text file of blocks:

    @@ Title shown in the table: path/to/file
    - the text to replace (exactly once in the file; one line per `- `)
    + what to put there

Blank lines and `#` lines between blocks are ignored. Each block is run with
tools/bug-check.py (MACHINES=raspi3b unless set, to halve the time), which restores the file.
One progress line is printed per mutation, then the table, which is also written to SUMMARY
(default build/bug-check-summary.md) and pastes straight into a PR. `none` in the second column means
no test noticed the bug: add a test, or explain why not.

Refuses to start if a target file has uncommitted changes (commit real work first), and puts the
files back with `git checkout` however it ends. Never commit while it runs.
"""
import os
import re
import signal
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def parse(text):
    blocks, current = [], None
    for raw in text.splitlines():
        if raw.startswith("@@"):
            title, _, path = raw[2:].rpartition(":")
            current = {"title": title.strip(), "path": path.strip(), "old": [], "new": []}
            blocks.append(current)
        elif current is not None and raw.startswith("- "):
            current["old"].append(raw[2:])
        elif current is not None and raw.startswith("+ "):
            current["new"].append(raw[2:])
        elif current is not None and raw in ("-", "+"):
            current["old" if raw == "-" else "new"].append("")
    for b in blocks:
        b["old"], b["new"] = "\n".join(b["old"]), "\n".join(b["new"])
    return blocks


def main(args):
    if not args or args[0].startswith("-"):
        sys.exit(__doc__)
    summary = "build/bug-check-summary.md"
    if "-o" in args:
        summary = args[args.index("-o") + 1]
    blocks = parse(open(args[0]).read())
    if not blocks:
        sys.exit("no mutations found in " + args[0])
    files = sorted({b["path"] for b in blocks})
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *files],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        sys.exit("commit (or stash) these first; the batch edits and restores them:\n" + dirty)

    def restore(*_):
        subprocess.run(["git", "checkout", "--", *files], capture_output=True)
        if _:
            sys.exit(1)
    signal.signal(signal.SIGTERM, restore)
    env = dict(os.environ)
    env.setdefault("MACHINES", "raspi3b")
    rows = []
    try:
        for i, b in enumerate(blocks, 1):
            run = subprocess.run([sys.executable, "tools/bug-check.py", b["path"], b["old"], b["new"]],
                                 capture_output=True, text=True, env=env)
            result = run.stdout.strip() or ("(error: " + run.stderr.strip().splitlines()[-1] + ")"
                                            if run.stderr.strip() else "(no output)")
            if "MACHINES" not in os.environ:
                # harness/machines needs both machines; with one it always fails.
                result = ", ".join(r for r in result.split(", ") if r != "harness/machines") or "none"
            rows.append((b["title"], result))
            print(f"{i}/{len(blocks)} {b['title']}: {result}", flush=True)
    finally:
        restore()
    table = "| Bug | Tests that fail |\n|---|---|\n" + "\n".join(
        f"| {t} | {r.replace('|', '/')} |" for t, r in rows) + "\n"
    os.makedirs(os.path.dirname(summary) or ".", exist_ok=True)
    open(summary, "w").write(table)
    print("\n" + table + f"written to {summary}")
    if any(r == "none" for _, r in rows):
        print("NOTE: a bug nothing catches (none).")


if __name__ == "__main__":
    main(sys.argv[1:])
