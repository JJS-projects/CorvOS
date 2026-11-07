"""Command line: python3 -m fledgling FILE... [--trusted DIR]... -o output.s"""

import argparse
import sys

from . import compile_program, CompileError
from .dump import dump_tokens, dump_tree, dump_expanded, dump_declarations, dump_bodies, make_stream
from . import is_trusted


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
    ap.add_argument("--dump-expanded", action="store_true",
                    help="print the program of the input files after generics are expanded, in the text "
                         "format the self-hosted generics pass must match, and stop")
    ap.add_argument("--dump-declarations", action="store_true",
                    help="print what the checker knows once every declaration is checked, in the text "
                         "format the self-hosted checker must match, and stop")
    ap.add_argument("--dump-bodies", action="store_true",
                    help="print the constants and the checked tree of the last input file, with what the "
                         "checker recorded (types, slots, values); see --ownership and --actors")
    ap.add_argument("--ownership", action="store_true",
                    help="with --dump-bodies: also track moves, locks and loop states")
    ap.add_argument("--no-bodies", action="store_true",
                    help="with --dump-bodies: check the constants only, no function body")
    ap.add_argument("--actors", action="store_true",
                    help="with --dump-bodies: also check actors' `new` and handlers")
    args = ap.parse_args(argv)

    if args.dump_bodies:
        files = [(path, is_trusted(path, args.trusted), open(path).read()) for path in args.inputs]
        sys.stdout.write(dump_bodies(make_stream(files), ownership=args.ownership, actors=args.actors,
                                          bodies=not args.no_bodies))
        return 0

    if args.dump_declarations:
        files = [(path, is_trusted(path, args.trusted), open(path).read()) for path in args.inputs]
        sys.stdout.write(dump_declarations(make_stream(files)))
        return 0

    if args.dump_expanded:
        files = [(path, is_trusted(path, args.trusted), open(path).read()) for path in args.inputs]
        sys.stdout.write(dump_expanded(make_stream(files)))
        return 0

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
