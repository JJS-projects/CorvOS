#!/usr/bin/env python3
"""Make an error test in two steps, so the message is read before it is trusted.

    tools/new-error-test.py NAME "One-line description." [--alone] [--trusted] < program.rook
    tools/new-error-test.py --accept NAME [--trusted]

Step 1 writes tests/rook/errors/NAME.rook (errors/trusted/ with --trusted) with a header
and a placeholder `// error:` line (the test fails until step 2), compiles it the way the
test does, and prints the real error with its source line and caret. Read it: is this the
error the program was written to provoke, at the right place, in words a user can follow?
If not, edit the program and run step 1 again.
Step 2 (--accept) fills the `// error:` line from that same compile. The header is the
same length before and after, so the line numbers do not move.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
import run_tests  # noqa: E402
from fledgling import CompileError, compile_program  # noqa: E402

PLACEHOLDER = "// error: 1:1: PLACEHOLDER (run tools/new-error-test.py --accept NAME)"


def compile_error(path, alone):
    """The CompileError for `path`, with the text to show; exits if there is none."""
    lib = [] if alone else run_tests.ERROR_LIB
    files = [(p, open(p).read()) for p in lib + [path]]
    try:
        compile_program(files, run_tests.TRUSTED_DIRS)
    except CompileError as err:
        if err.pos.file != path:
            sys.exit(f"the error is in {err.pos.file}, not in the test:\n"
                     + err.format(os.path.basename(err.pos.file), dict(files).get(err.pos.file)))
        return err, err.format(os.path.basename(path), dict(files).get(err.pos.file))
    sys.exit("the program compiled without an error: it does not test anything")


def main(args):
    where = None
    if "--dir" in args:               # for the harness test: somewhere other than tests/rook/errors
        at = args.index("--dir")
        where = args[at + 1]
        args = args[:at] + args[at + 2:]
    trusted = "--trusted" in args
    alone = "--alone" in args
    accept = "--accept" in args
    args = [a for a in args if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    folder = where or os.path.join(ROOT, "tests", "rook", "errors", "trusted" if trusted else "")
    path = os.path.join(folder, args[0] + ".rook")
    if accept:
        text = open(path).read()
        alone = re.search(r"^// alone$", text, re.M) is not None
        err, shown = compile_error(path, alone)
        new = re.sub(r"^// error:.*$", f"// error: {err.pos.line}:{err.pos.col}: {err.message}",
                     text, count=1, flags=re.M)
        open(path, "w").write(new)
        print(shown)
        print(f"wrote the header of {os.path.relpath(path, ROOT)}")
        return
    if len(args) != 2:
        sys.exit(__doc__)
    if os.path.exists(path):
        sys.exit(f"{path} exists")
    program = sys.stdin.read().rstrip("\n")
    header = [f"// {args[1]}"] + (["// alone"] if alone else []) + [PLACEHOLDER]
    os.makedirs(folder, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(header) + "\n\n" + program + "\n")
    # Line numbers in the message count the header, which accept does not change.
    err, shown = compile_error(path, alone)
    print(shown)
    print(f"\nnot yet a test: read the error above, then run\n"
          f"  tools/new-error-test.py --accept {args[0]}" + (" --trusted" if trusted else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
