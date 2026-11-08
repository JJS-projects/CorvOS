#!/usr/bin/env python3
"""Which lines of Fledgling does the test suite reach?

    python3 tools/coverage.py

Compiles every test program (the Rook programs and the error programs, the
same way tests/run_tests.py does) under the standard library's `trace` module,
then lists the lines of bootstrap/fledgling/*.py that never ran. It only runs
the compiler: whether the generated code behaves is what the QEMU tests check.
No dependencies beyond Python (see decisions.md, 102).

Exit status 1 if a line never ran and is not in tools/coverage-gaps.txt, or if
that file lists a statement that does run now (so it can't go stale). Entries
there name a statement by its text and the line before it, not by line number,
so edits elsewhere don't break them. Each has a reason.
"""
import ast
import dis
import glob
import os
import sys
import trace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.dont_write_bytecode = True

import run_tests  # noqa: E402  (its test lists say how each program is compiled)

PACKAGE = os.path.join(ROOT, "bootstrap", "fledgling")
GAPS = os.path.join(ROOT, "tools", "coverage-gaps.txt")


def executable_lines(path):
    """Line numbers that hold code, from the compiled bytecode."""
    lines = set()

    def walk(code):
        lines.update(line for _, line in dis.findlinestarts(code) if line)
        for const in code.co_consts:
            if hasattr(const, "co_code"):
                walk(const)

    walk(compile(open(path).read(), path, "exec"))
    return lines


def compile_everything():
    """Compile each test program once, as the tests do."""
    # Import the compiler here, under the tracer, so the lines that run at import
    # (every `def` and `class`) count too.
    for name in [m for m in sys.modules if m == "fledgling" or m.startswith("fledgling.")]:
        del sys.modules[name]
    from fledgling import compile_program
    from fledgling.dump import dump_tokens, dump_tree, dump_expanded, dump_declarations, dump_bodies, dump_asm, make_stream, tree
    lib = run_tests.ROOK_LIB
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "*.rook"))
                       + glob.glob(os.path.join(ROOT, "tests", "rook", "trusted", "*.rook"))):
        compile_program([(p, open(p).read()) for p in lib + [path]], run_tests.TRUSTED_DIRS)
    # Error programs go through the test itself, so `// alone` and the other
    # headers are honoured exactly as `make test` does.
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "errors", "*.rook"))
                       + glob.glob(os.path.join(ROOT, "tests", "rook", "errors", "trusted", "*.rook"))):
        run_tests.ErrorTest.from_file(path).run()
    # The two import cases a one-file test can't reach.
    run_tests.check_import_ambiguous()
    run_tests.check_suggest_imports()
    # The token dump (the check in run_tests holds the module imported before the tracer started).
    dump_tokens("x: u8 = 1\ns = \"a\"\n")
    dump_tokens("x\ty\n")
    # The tree dump: every Rook file of the tests and of Raven, so every kind of node is printed once,
    # and the two kinds of error.
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "*.rook"))
                       + glob.glob(os.path.join(ROOT, "tests", "rook", "trusted", "*.rook"))
                       + glob.glob(os.path.join(ROOT, "raven", "*.rook"))
                       + glob.glob(os.path.join(ROOT, "raven", "trusted", "*.rook"))):
        dump_tree(open(path).read())
    dump_tree("x\ty\n")
    dump_tree("fn f() {\n")
    # The expanded program: the tests' own generic programs, and an error.
    generic_library = os.path.join(ROOT, "tests", "rook", "support", "generic_library.rook")
    for name in ("generics_files", "generics_option", "generics_struct"):
        path = os.path.join(ROOT, "tests", "rook", name + ".rook")
        dump_expanded(make_stream([(f, False, open(f).read()) for f in (generic_library, path)]))
    dump_expanded("@@@ a.rook\nstruct B<T> {\n}\nfn f(x: B) {\n}\n")
    # The declarations of whole programs (Raven's files and a test with actors, enums and generics).
    for name in ("generics_files", "supervisor_demo", "restart_basic", "interfaces_basic", "actors_val_field"):
        path = os.path.join(ROOT, "tests", "rook", name + ".rook")
        if os.path.exists(path):
            dump_declarations(make_stream([(f, run_tests.is_trusted(f, run_tests.TRUSTED_DIRS), open(f).read())
                                           for f in run_tests.ROOK_LIB + [path]]))
    dump_declarations("@@@ a.rook\nstruct Bad {\n}\n")
    dump_declarations("@@@ a.rook\nenum Mode {\n    Fast\n    fn label() -> u8 {\n        return 1\n    }\n}\n")
    # The bodies of whole programs, with every switch on and off: the tests with actors, sends, generics,
    # text constants and matches, and an error.
    for name in ("generics_files", "supervisor_demo", "restart_basic", "interfaces_basic", "actors_val_field",
                 "generics_option", "text_literals", "match_lending", "stop_children", "text_builder"):
        path = os.path.join(ROOT, "tests", "rook", name + ".rook")
        if os.path.exists(path):
            stream = make_stream([(f, run_tests.is_trusted(f, run_tests.TRUSTED_DIRS), open(f).read())
                                  for f in run_tests.ROOK_LIB + [path]])
            dump_bodies(stream)
            dump_bodies(stream, ownership=True, actors=True)
            dump_bodies(stream, bodies=False)
            dump_bodies(stream, ownership=True, actors=True, bodies="all")
    dump_bodies("@@@ a.rook\nA: u8 = 256\n")
    dump_asm("@@@ a.rook\nfn f() {\n}\n")
    dump_asm("@@@ a.rook\nA: u8 = 256\n")
    dump_bodies("@@@ a.rook\nstruct Box<T> {\n    value: T\n    fn get() -> T {\n        return copy(.value)\n    }\n}\n"
                "fn f() -> u64 {\n    b: Box<u64> = Box(value: 1)\n    return b.get()\n}\n")
    try:
        tree(object())          # a node the dump has no form for is a bug in the dump, not silence
    except AssertionError:
        pass
    # Raven itself.
    raven = sorted(glob.glob(os.path.join(ROOT, "raven", "trusted", "*.rook"))
                   + glob.glob(os.path.join(ROOT, "raven", "*.rook")))
    compile_program([(p, open(p).read()) for p in raven],
                    [os.path.join(ROOT, "raven", "trusted")])


def ranges(numbers):
    """[3, 4, 5, 9] -> '3-5, 9'"""
    out, start, prev = [], None, None
    for n in sorted(numbers):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev > start else str(start))
    return ", ".join(out)


def statements(path, source_lines):
    """For each line that holds code: (first line of its statement, text of that
    line, text of the line before). The text is what coverage-gaps.txt matches."""
    owner = {}
    for node in ast.walk(ast.parse("\n".join(source_lines))):
        if isinstance(node, ast.stmt) and not hasattr(node, "body"):
            for line in range(node.lineno, node.end_lineno + 1):
                owner[line] = node.lineno
    return owner


def load_gaps():
    gaps = {}
    for raw in open(GAPS):
        if raw.strip() and not raw.startswith("#"):
            file, previous, line, reason = [part.strip() for part in raw.split("||", 3)]
            gaps[(file, previous, line)] = reason
    return gaps


def main(argv):
    if argv:
        sys.exit(__doc__)
    tracer = trace.Trace(count=1, trace=0)
    tracer.runfunc(compile_everything)
    ran = {}
    for (path, line), _ in tracer.results().counts.items():
        ran.setdefault(os.path.realpath(path), set()).add(line)

    gaps = load_gaps()
    seen_gaps = set()
    unexpected = []
    total = hit = 0
    # __main__.py is the command-line entry point; the Makefile runs it, not this.
    for path in sorted(glob.glob(os.path.join(PACKAGE, "*.py"))):
        if path.endswith("__main__.py"):
            continue
        rel = os.path.relpath(path, ROOT)
        source = open(path).read().splitlines()
        owner = statements(path, source)
        wanted = executable_lines(path)
        missed = wanted - ran.get(os.path.realpath(path), set())
        total += len(wanted)
        hit += len(wanted) - len(missed)
        pct = 100 * (len(wanted) - len(missed)) / len(wanted) if wanted else 100
        print(f"{rel:<36} {len(wanted) - len(missed):>5}/{len(wanted):<5} {pct:5.1f}%")
        known, new = set(), set()
        for line in missed:
            start = owner.get(line, line)
            key = (rel, source[start - 2].strip() if start > 1 else "", source[start - 1].strip())
            if key in gaps:
                seen_gaps.add(key)
                known.add(line)
            else:
                new.add(line)
                unexpected.append((rel, line, source[line - 1].strip()))
        if known:
            print(f"    known gaps (coverage-gaps.txt): {ranges(known)}")
        if new:
            print(f"    NEVER RUN: {ranges(new)}")
    print(f"\ntotal {hit}/{total} lines, {100 * hit / total:.1f}%")

    stale = [key for key in gaps if key not in seen_gaps]
    for rel, line, text in unexpected:
        print(f"not covered, and not in coverage-gaps.txt: {rel}:{line}: {text}")
    for file, previous, line in stale:
        print(f"coverage-gaps.txt lists a statement that now runs (or is gone): {file}: {line}")
    return 1 if unexpected or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
