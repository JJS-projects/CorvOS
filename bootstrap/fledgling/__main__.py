"""Command line: python3 -m fledgling input.rook -o output.s"""

import argparse
import sys

from . import compile_source, CompileError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fledgling",
                                 description="Bootstrap compiler for Rook (emits AArch64 assembly).")
    ap.add_argument("input", help="Rook source file")
    ap.add_argument("-o", "--output", help="assembly output file (default: stdout)")
    args = ap.parse_args(argv)

    with open(args.input) as f:
        source = f.read()
    try:
        asm = compile_source(source, args.input)
    except CompileError as err:
        print(err.format(args.input, source), file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w") as f:
            f.write(asm)
    else:
        sys.stdout.write(asm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
