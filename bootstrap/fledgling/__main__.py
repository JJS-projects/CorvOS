"""Command line: python3 -m fledgling FILE... [--trusted DIR]... -o output.s"""

import argparse
import sys

from . import compile_program, CompileError
from .dump import dump_tokens, dump_tree


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fledgling",
                                 description="Bootstrap compiler for Rook (emits AArch64 assembly).")
    ap.add_argument("inputs", nargs="+", help="Rook source files, compiled as one program")
    ap.add_argument("-o", "--output", help="assembly output file (default: stdout)")
    ap.add_argument("--trusted", action="append", default=[], metavar="DIR",
                    help="a directory whose files may use `unsafe` and `extern fn`")
    ap.add_argument("--suggest-imports", action="store_true",
                    help="print the imports each file is missing, and generate nothing")
    ap.add_argument("--dump-tokens", action="store_true",
                    help="print the tokens of the one input file in the text format the "
                         "self-hosted lexer must match (docs/self-hosting.md), and stop")
    ap.add_argument("--dump-tree", action="store_true",
                    help="print the syntax tree of the one input file in the text format the "
                         "self-hosted parser must match (docs/self-hosting.md), and stop")
    args = ap.parse_args(argv)

    if args.dump_tree:
        if len(args.inputs) != 1:
            ap.error("--dump-tree takes exactly one input file")
        with open(args.inputs[0]) as f:
            sys.stdout.write(dump_tree(f.read()))
        return 0

    if args.dump_tokens:
        if len(args.inputs) != 1:
            ap.error("--dump-tokens takes exactly one input file")
        with open(args.inputs[0]) as f:
            sys.stdout.write(dump_tokens(f.read()))
        return 0

    sources = {}
    for path in args.inputs:
        with open(path) as f:
            sources[path] = f.read()
    suggestions = [] if args.suggest_imports else None
    try:
        asm = compile_program(list(sources.items()), args.trusted, suggestions)
    except CompileError as err:
        print(err.format(args.inputs[-1], sources.get(err.pos.file)), file=sys.stderr)
        return 1

    if suggestions is not None:
        wanted = {}
        for path, module, name in suggestions:
            wanted.setdefault((path, module), set()).add(name)
        for (path, module), names in sorted(wanted.items()):
            print(f"{path}: import {module}: {', '.join(sorted(names))}")
        return 0

    if args.output:
        with open(args.output, "w") as f:
            f.write(asm)
    else:
        sys.stdout.write(asm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
