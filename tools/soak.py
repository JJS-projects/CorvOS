#!/usr/bin/env python3
"""Run tests many times to find the ones that fail now and then (a race, a timing assumption).

    tools/soak.py [-n RUNS] [-j JOBS] [--icount SHIFTS] [--list] NAME_OR_PREFIX...

Each argument is a test name or prefix, as for tests/run_tests.py; it is expanded to the tests it names (all of them
if there is none). Every test is run RUNS times (default 25) one run after another, and JOBS different tests (default
4) run side by side. Never the same test twice at once: they share log files and would trip over each other. It prints
the tests that failed at least once, with how often and the first reason, then a summary; exit status 1 if any failed.
With `--icount 0,1,2` each test is run once under QEMU's instruction counter at each of those shifts instead (RUNS is
ignored): every shift is one schedule of the cores that repeats exactly, so a failure it finds can be replayed at will
with `ICOUNT=N python3 tests/run_tests.py TEST` (decision 195).
Needs the images of `make test`. A single run proves little about a race (decisions 178 and 181 to 191): the kernel races
of milestone 6 and 7 showed up in about one run in thirty.
"""
import concurrent.futures
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))


def expand(names, tests):
    """The tests the arguments name, in the harness's order, each once."""
    if not names:
        return [t.name for t in tests]
    return [t.name for t in tests if any(t.name == a or t.name.startswith(a) for a in names)]


def soak_one(name, runs, shifts=None):
    """(name, failures, first reason) for `runs` runs of one test, or one run at each instruction-counter shift in `shifts`
    (then the reason names the shifts that failed)."""
    failures, first, failed_shifts = 0, "", []
    for run in (shifts if shifts else range(runs)):
        env = dict(os.environ, ICOUNT=str(run)) if shifts else None
        result = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "run_tests.py"), name],
                                capture_output=True, text=True, env=env)
        if result.returncode != 0:
            failures += 1
            if shifts:
                failed_shifts.append(str(run))
            if not first:
                reasons = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("reason:")]
                first = reasons[0] if reasons else result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if failed_shifts:
        first = f"at shifts {','.join(failed_shifts)}; {first}"
    return name, failures, first


def main(argv):
    runs, jobs, listing, names, shifts = 25, 4, False, [], None
    at = 0
    while at < len(argv):
        if argv[at] == "-n":
            runs, at = int(argv[at + 1]), at + 2
        elif argv[at] == "-j":
            jobs, at = int(argv[at + 1]), at + 2
        elif argv[at] == "--icount":
            shifts, at = argv[at + 1].split(","), at + 2
        elif argv[at] == "--list":
            listing, at = True, at + 1
        elif argv[at].startswith("-"):
            sys.exit(__doc__)
        else:
            names.append(argv[at])
            at += 1
    import run_tests
    selected = expand(names, run_tests.all_tests())
    if not selected:
        print(f"no tests match: {' '.join(names)}")
        return 2
    if listing:
        print("\n".join(selected))
        return 0
    bad = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for name, failures, first in pool.map(lambda n: soak_one(n, runs, shifts), selected):
            if failures:
                bad += 1
                print(f"{name}: {failures} of {len(shifts) if shifts else runs} runs failed; {first}")
    each = f"one run at each of {len(shifts)} shifts" if shifts else f"{runs} runs each"
    print(f"{len(selected)} tests, {each}, {bad} with failures")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
