#!/usr/bin/env python3
"""Check a self-hosted compiler stage against Fledgling on every Rook file of the repository.

    tools/selfhost-check.py STAGE [FILE...]       STAGE is a hosted program with a reference: lexdump, parsedump, expanddump
    make selfhost-check                           the same for every stage, on every .rook file

Runs `compiler/STAGE.rook` in QEMU on each file (the files of the repository's raven/, tests/rook/ and
compiler/ by default), as the hosted tests do, and compares its output with Fledgling's. Prints one line
per file that differs, then a summary; exits 1 if any differs. Slow (a file takes seconds), so it is not
part of `make test`; the stage's own hosted tests (a few files and edge cases) are.
"""
import concurrent.futures
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import run_tests  # noqa: E402


def corpus():
    files = []
    for pattern in ("raven/*.rook", "raven/trusted/*.rook", "tests/rook/*.rook", "tests/rook/*/*.rook",
                    "tests/rook/errors/*.rook", "tests/rook/errors/trusted/*.rook", "compiler/*.rook",
                    "compiler/lib/*.rook"):
        files += sorted(glob.glob(os.path.join(ROOT, pattern)))
    return files


def program_corpus():
    """The programs a whole-program stage is checked on: every test program and compiler program."""
    files = []
    for pattern in ("tests/rook/*.rook", "tests/rook/trusted/*.rook", "tests/rook/errors/*.rook",
                    "tests/rook/errors/trusted/*.rook", "compiler/*.rook"):
        files += sorted(glob.glob(os.path.join(ROOT, pattern)))
    return files


def program_files(stage, path):
    """For a stage that works on a whole program: the files of the program `path` is, as the tests
    compile it (Raven's Rook code, the test support files, the compiler's modules, and the file
    itself); else ()."""
    if stage not in run_tests.STREAM_STAGES:
        return ()
    rel = os.path.relpath(path, ROOT)
    lib = run_tests.ROOK_LIB
    if rel.startswith("tests/rook/errors/"):
        alone = any(line.strip() == "// alone" for line in open(path) if line.strip().startswith("//"))
        lib = [] if alone else run_tests.ERROR_LIB
    elif rel.startswith("compiler/"):
        lib = lib + sorted(glob.glob(os.path.join(ROOT, "compiler", "lib", "*.rook")))
    return tuple(os.path.relpath(f, ROOT) for f in lib + [path])


def main(argv):
    if not argv or argv[0] not in run_tests.HOSTED_REFERENCE:
        sys.exit(__doc__)
    stage = argv[0]
    files = argv[1:] or (program_corpus() if stage in run_tests.STREAM_STAGES else corpus())
    tests = [run_tests.HostedTest(f"{stage}:{os.path.relpath(f, ROOT)}", "", f"build/compiler/{stage}.img",
                                  f, None, 600.0 if stage in run_tests.STREAM_STAGES else 120.0, True,
                                  program_files(stage, f)) for f in files]
    jobs = int(os.environ.get("JOBS", run_tests.DEFAULT_JOBS))
    bad = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for test, result in zip(tests, pool.map(run_tests.run_one, tests)):
            if not result[0]:
                bad += 1
                print(f"DIFFERS  {test.name}: {result[1]}", flush=True)
    print(f"\n{len(tests) - bad}/{len(tests)} files give the same output as Fledgling")
    return 1 if bad else 0


sys.exit(main(sys.argv[1:]))
