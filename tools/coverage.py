#!/usr/bin/env python3
"""Which lines of Fledgling does the test suite reach?

    python3 tools/coverage.py [--fail-under PERCENT]

Compiles every test program (the Rook programs and the error programs, the
same way tests/run_tests.py does) under the standard library's `trace` module,
then lists the lines of bootstrap/fledgling/*.py that never ran. It only runs
the compiler: whether the generated code behaves is what the QEMU tests check.
No dependencies beyond Python (see decisions.md, 102).
"""
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
    from fledgling import CompileError, compile_program
    lib = run_tests.ROOK_LIB
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "*.rook"))
                       + glob.glob(os.path.join(ROOT, "tests", "rook", "trusted", "*.rook"))):
        compile_program([(p, open(p).read()) for p in lib + [path]], run_tests.TRUSTED_DIRS)
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "errors", "*.rook"))
                       + glob.glob(os.path.join(ROOT, "tests", "rook", "errors", "trusted", "*.rook"))):
        try:
            compile_program([(p, open(p).read()) for p in run_tests.ERROR_LIB + [path]],
                            run_tests.TRUSTED_DIRS)
        except CompileError as err:
            err.format(os.path.basename(path))      # the message layout runs too
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


def main(argv):
    threshold = float(argv[1]) if len(argv) == 2 and argv[0] == "--fail-under" else None
    tracer = trace.Trace(count=1, trace=0)
    tracer.runfunc(compile_everything)
    ran = {}
    for (path, line), _ in tracer.results().counts.items():
        ran.setdefault(os.path.realpath(path), set()).add(line)

    total = hit = 0
    # __main__.py is the command-line entry point; the Makefile runs it, not this.
    for path in sorted(glob.glob(os.path.join(PACKAGE, "*.py"))):
        if path.endswith("__main__.py"):
            continue
        wanted = executable_lines(path)
        missed = wanted - ran.get(os.path.realpath(path), set())
        total += len(wanted)
        hit += len(wanted) - len(missed)
        pct = 100 * (len(wanted) - len(missed)) / len(wanted) if wanted else 100
        print(f"{os.path.relpath(path, ROOT):<36} {len(wanted) - len(missed):>5}/{len(wanted):<5} {pct:5.1f}%")
        if missed:
            print(f"    never run: {ranges(missed)}")
    pct = 100 * hit / total
    print(f"\ntotal {hit}/{total} lines, {pct:.1f}%")
    return 1 if threshold is not None and pct < threshold else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
