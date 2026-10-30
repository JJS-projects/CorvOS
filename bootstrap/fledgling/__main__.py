"""Command line: python3 -m fledgling FILE... [--trusted DIR]... -o output.s"""

import argparse
import sys

from . import compile_program, CompileError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fledgling",
                                 description="Bootstrap compiler for Rook (emits AArch64 assembly).")
    ap.add_argument("inputs", nargs="+", help="Rook source files, compiled as one program")
    ap.add_argument("-o", "--output", help="assembly output file (default: stdout)")
    ap.add_argument("--trusted", action="append", default=[], metavar="DIR",
                    help="a directory whose files may use `unsafe` and `extern fn`")
    args = ap.parse_args(argv)

    sources = {}
    for path in args.inputs:
        with open(path) as f:
            sources[path] = f.read()
    try:
        asm = compile_program(list(sources.items()), args.trusted)
    except CompileError as err:
        print(err.format(args.inputs[-1], sources.get(err.pos.file)), file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w") as f:
            f.write(asm)
    else:
        sys.stdout.write(asm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
