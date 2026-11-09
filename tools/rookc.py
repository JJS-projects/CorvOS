#!/usr/bin/env python3
"""Compile Rook files with the self-hosted compiler (milestone 5f, decision 170).

    tools/rookc.py FILE... [--trusted DIR]... -o OUT.s [--image IMG]

The same command line as `python3 -m fledgling FILE... -o OUT.s`, and the same assembly. It boots the stage-2
compiler image (default build/stage2/asmdump.img, made by `make bootstrap`: the Rook compiler built from its own
assembly) in QEMU, types the files on the UART and writes what it prints. A compile takes about half a minute,
so the result is kept in build/cache/ under a hash of the files and the compiler image: the same input with
the same compiler is never compiled twice. A compile error is printed (`error FILE:LINE:COL: MESSAGE`) and exits 1.
"""
import argparse
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from fledgling import is_trusted  # noqa: E402
from fledgling.dump import make_stream  # noqa: E402
import run_tests  # noqa: E402

CACHE = os.path.join(ROOT, "build", "cache")


def compile_files(paths, trusted_dirs, image, timeout=900.0):
    """The assembly (or the one `error ...` line) for `paths`; (text, was_cached)."""
    stream = make_stream([(p, is_trusted(p, trusted_dirs), open(p).read()) for p in paths])
    with open(image, "rb") as f:
        key = hashlib.sha256(hashlib.sha256(f.read()).digest() + stream.encode()).hexdigest()
    cached = os.path.join(CACHE, key + ".s")
    if os.path.exists(cached):
        return open(cached).read(), True
    machine = run_tests.MACHINES[0]
    os.makedirs(run_tests.LOG_DIR, exist_ok=True)
    output, timed_out, qemu_log = run_tests.boot(
        image, lambda lines: run_tests.HOSTED_END in lines, timeout, machine,
        os.path.join(run_tests.LOG_DIR, f"rookc_{key[:12]}.qemu"), [(stream.encode() + run_tests.END_OF_INPUT, "")])
    lines = run_tests.complete_lines(output)
    if run_tests.HOSTED_BEGIN not in lines or run_tests.HOSTED_END not in lines:
        raise RuntimeError("the compiler printed no Hosted: begin/end" + (" before the timeout" if timed_out else ""))
    text = "".join(line + "\n" for line in lines[lines.index(run_tests.HOSTED_BEGIN) + 1:lines.index(run_tests.HOSTED_END)])
    if not text.startswith("error "):          # an error is not kept: fix the program and it is another input anyway
        os.makedirs(CACHE, exist_ok=True)
        tmp = cached + f".{os.getpid()}"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, cached)                # (two compiles at once never see half a file)
    return text, False


def main(argv):
    ap = argparse.ArgumentParser(prog="rookc", description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--trusted", action="append", default=[])
    ap.add_argument("--image", default=os.path.join(ROOT, "build", "stage2", "asmdump.img"))
    args = ap.parse_args(argv)
    if not os.path.exists(args.image):
        sys.exit(f"rookc: {args.image} does not exist: run `make bootstrap` first")
    text, _ = compile_files(args.inputs, args.trusted, args.image)
    if text.startswith("error "):
        sys.stderr.write(text)
        return 1
    with open(args.output, "w") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
