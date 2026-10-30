#!/usr/bin/env python3
"""CorvOS test suite.

Four kinds of test:

  boot/...   Kernel boot tests, defined in BOOT_TESTS below. Boot an image in
             QEMU and check that expected UART lines appear in order, and
             forbidden ones never do.

  rook/...   Rook programs in tests/rook/*.rook. Each is compiled by Fledgling
             together with raven/trusted/bytes.rook and the test output
             functions, linked into a test kernel that calls its `test_main()`,
             and booted in QEMU. Programs that need `unsafe` are in
             tests/rook/trusted/. Its header comments say what it must print:
                 // expect: 42                  one per `out:` line, exact, in order
                 // expect-panic: integer overflow at x.rook:7
             Without expect-panic, test_main must return normally.
                 // expect-fault: FAR_EL1:  0x0000000030000000
             The program must end in a CPU exception, whose report (see
             kernel/arch/aarch64/vectors.s) has a line containing this text.
                 // machines: raspi3b
             Run only on the listed machines (for tests of the machine itself).

  error/...  Programs in tests/rook/errors/*.rook (or errors/trusted/, compiled
             as trusted) that Fledgling must reject:
                 // error: 7:5: can't assign to `x`
             The error must be in that file, at that line:column, and contain
             that message. A header line `// alone` compiles the file by itself,
             without Raven's code (for errors about what a program must include).

  harness/...  Checks of the test harness and build itself, defined in
             HARNESS_TESTS below: the things a Rook program can't test.

Every boot test and Rook program runs on each machine in MACHINES: raspi3b
(1 GB) and raspi3ap (512 MB, like the Zero 2 W). QEMU also logs accesses to
unimplemented or misused devices (-d unimp,guest_errors); any such report fails
the test, unless the program declares it with a header line:
                 // expect-qemu-log: pl011_write: Bad offset 0x100

Full QEMU output of every boot is saved to build/test-logs/.

Usage: python3 tests/run_tests.py [name-or-prefix ...]
       e.g. `rook/` runs only the Rook program tests.
"""

import glob
import os
import selectors
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
sys.dont_write_bytecode = True      # never run a stale cached copy of the compiler
LOG_DIR = os.path.join(ROOT, "build", "test-logs")
QEMU = os.environ.get("QEMU", "qemu-system-aarch64")
# The QEMU machines every boot and Rook test runs on: a Pi 3B (1 GB) and a
# Pi 3A+ (512 MB, the Zero 2 W's amount). Override with MACHINES="raspi3b".
MACHINES = os.environ.get("MACHINES", "raspi3b raspi3ap").split()

from fledgling import compile_program, CompileError  # noqa: E402

# Error tests are compiled with Raven's Rook code and the test output functions
# (as test programs are), and the directories trusted with `unsafe`.
ROOK_LIB = (sorted(glob.glob(os.path.join(ROOT, "raven", "trusted", "*.rook")))
            + sorted(glob.glob(os.path.join(ROOT, "raven", "*.rook")))
            + [os.path.join(ROOT, "tests", "rook", "support", "trusted", "print.rook")])
# Error tests are also compiled with a file of private items, to test visibility.
ERROR_LIB = ROOK_LIB + [os.path.join(ROOT, "tests", "rook", "support", "visibility.rook")]
TRUSTED_DIRS = [os.path.join(ROOT, *d.split("/")) for d in
                ("raven/trusted", "tests/rook/support/trusted", "tests/rook/trusted",
                 "tests/rook/errors/trusted")]


# ---------------------------------------------------------------------------
# Booting an image in QEMU

def boot(image, finished, timeout, machine, log_path):
    """Boot `image` on `machine` until `finished(lines)` is true or `timeout` passes.

    Returns (output, timed_out, qemu_log). `lines` passed to `finished` are
    complete lines with carriage returns removed. `qemu_log` is the list of
    lines QEMU reported about unimplemented or misused devices.
    """
    if os.path.exists(log_path):
        os.remove(log_path)
    cmd = [QEMU, "-M", machine, "-kernel", image,
           "-display", "none", "-serial", "stdio", "-monitor", "none",
           "-d", "unimp,guest_errors", "-D", log_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    output = ""
    deadline = time.monotonic() + timeout
    try:
        while True:
            if finished(complete_lines(output)):
                # Catch anything printed straight afterwards.
                output += read_available(sel, 0.2)
                return output, False, read_qemu_log(proc, log_path)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or proc.poll() is not None:
                output += read_available(sel, 0)
                return output, remaining <= 0, read_qemu_log(proc, log_path)
            output += read_available(sel, min(remaining, 0.2))
    finally:
        proc.kill()
        proc.wait()


def read_qemu_log(proc, log_path):
    """QEMU's report of device misuse so far, as a list of lines."""
    proc.kill()
    proc.wait()
    try:
        with open(log_path) as f:
            return [l.rstrip("\n") for l in f if l.strip()]
    except FileNotFoundError:
        return []


def read_available(sel, wait):
    out = ""
    end = time.monotonic() + wait
    while True:
        events = sel.select(timeout=max(0, end - time.monotonic()))
        if not events:
            return out
        for key, _ in events:
            chunk = os.read(key.fd, 4096)
            if not chunk:
                return out
            out += chunk.decode("utf-8", errors="replace")


def complete_lines(output):
    return output.replace("\r", "").split("\n")[:-1]


def log_base(name, machine):
    return os.path.join(LOG_DIR, name.replace("/", "__") + "." + machine)


def save_log(name, machine, output, qemu_log):
    with open(log_base(name, machine) + ".log", "w") as f:
        f.write(output)
        if qemu_log:
            f.write("\n--- QEMU device log ---\n" + "\n".join(qemu_log) + "\n")


def check_qemu_log(qemu_log, expected):
    """None if QEMU reported exactly what the test expects, else why not."""
    for want in expected:
        if not any(want in line for line in qemu_log):
            return f"QEMU did not report {want!r}"
    for line in qemu_log:
        if not any(want in line for want in expected):
            return f"QEMU reported a device problem: {line}"
    return None


def on_each_machine(test, run_once, machines=None):
    """Run `run_once(machine)` on every machine (or just `machines`); the first
    failure is the result."""
    for machine in machines or MACHINES:
        result = run_once(machine)
        if not result[0]:
            return (False, f"[{machine}] {result[1]}") + tuple(result[2:])
    return result


def missing_image(image):
    return f"missing {os.path.relpath(image, ROOT)} (run `make test`, not this script directly)"


# ---------------------------------------------------------------------------
# boot/...: kernel boot tests

@dataclass
class BootTest:
    name: str
    description: str
    image: str
    expect: list                                  # lines that must appear, in order
    forbid: list = field(default_factory=list)    # text that must never appear
    timeout: float = 10.0

    def run(self):
        return on_each_machine(self, self.run_on)

    def run_on(self, machine):
        image = os.path.join(ROOT, self.image)
        if not os.path.exists(image):
            return False, missing_image(image)

        def progress(lines):
            n = 0
            for line in lines:
                if n < len(self.expect) and self.expect[n] in line:
                    n += 1
            return n

        output, timed_out, qemu_log = boot(
            image, lambda lines: progress(lines) == len(self.expect), self.timeout,
            machine, log_base(self.name, machine) + ".qemu")
        save_log(self.name, machine, output, qemu_log)
        lines = complete_lines(output)
        problem = check_qemu_log(qemu_log, [])
        if problem:
            return False, problem, output
        for bad in self.forbid:
            if any(bad in line for line in lines):
                return False, f"forbidden output appeared: {bad!r}", output
        n = progress(lines)
        if n < len(self.expect):
            return False, f"never saw {self.expect[n]!r}", output
        return True, "ok", output


BOOT_TESTS = [
    BootTest(
        name="boot/hello",
        description="kernel boots to EL1, prints over the UART and idles",
        image="build/kernel8.img",
        expect=[
            "Raven: hello from CorvOS",
            "Raven: firmware entered at EL",
            "Raven: running at EL1",
            "Raven: boot complete, idling",
        ],
        forbid=["unhandled exception", "panic"],
    ),
    BootTest(
        name="boot/fault-report",
        description="a breakpoint is caught by the vectors and reported",
        image="build/fault/kernel8.img",
        expect=[
            "Raven: hello from CorvOS",
            "Raven: FAULT_TEST build, triggering a breakpoint",
            "*** Raven: unhandled exception ***",
            "kind:     synchronous, current EL, SPx",
            "EC:       0x000000000000003c",   # 0x3C = BRK instruction
            "ESR_EL1:  0x00000000f2000042",   # EC 0x3C, IL=1, immediate 0x42
            "core halted",
        ],
        forbid=["boot complete"],
    ),
]


# ---------------------------------------------------------------------------
# rook/...: compiled Rook programs

DONE = "Raven: test_main returned"
PANIC = "Raven: panic: "


@dataclass
class RookTest:
    name: str
    description: str
    image: str
    expect: list
    expect_panic: str = None
    expect_qemu_log: list = field(default_factory=list)
    expect_fault: str = None
    machines: list = None           # None: every machine in MACHINES
    timeout: float = 10.0

    @classmethod
    def from_file(cls, path):
        rel = os.path.relpath(path, os.path.join(ROOT, "tests", "rook"))
        base = os.path.splitext(rel)[0].replace(os.sep, "/")
        description, expect, panic, qemu_log = "", [], None, []
        fault, machines = None, None
        for line in open(path):
            line = line.strip()
            if not line.startswith("//"):
                break
            text = line[2:].strip()
            if text.startswith("expect:"):
                expect.append(text[len("expect:"):].strip())
            elif text.startswith("expect-panic:"):
                panic = text[len("expect-panic:"):].strip()
            elif text.startswith("expect-fault:"):
                fault = text[len("expect-fault:"):].strip()
            elif text.startswith("machines:"):
                machines = text[len("machines:"):].split()
            elif text.startswith("expect-qemu-log:"):
                qemu_log.append(text[len("expect-qemu-log:"):].strip())
            elif not description:
                description = text
        return cls(f"rook/{base}", description, f"build/rook-tests/{base}.img", expect, panic,
                   qemu_log, fault, machines)

    def run(self):
        return on_each_machine(self, self.run_on, self.machines)

    def run_on(self, machine):
        image = os.path.join(ROOT, self.image)
        if not os.path.exists(image):
            return False, missing_image(image)

        def finished(lines):
            return any(l.startswith((DONE, PANIC)) or "unhandled exception" in l
                       for l in lines)

        output, timed_out, qemu_log = boot(image, finished, self.timeout, machine,
                                           log_base(self.name, machine) + ".qemu")
        save_log(self.name, machine, output, qemu_log)
        lines = complete_lines(output)
        problem = check_qemu_log(qemu_log, self.expect_qemu_log)
        if problem:
            return False, problem, output
        outs = [l[len("out: "):] for l in lines if l.startswith("out: ")]
        panics = [l[len(PANIC):] for l in lines if l.startswith(PANIC)]

        if self.expect_fault is not None:
            if not any(self.expect_fault in l for l in lines):
                return False, f"expected a CPU exception report with {self.expect_fault!r}", output
            return True, "ok", output
        if any("unhandled exception" in l for l in lines):
            return False, "CPU exception (see output)", output
        for i, (want, got) in enumerate(zip(self.expect, outs)):
            if want != got:
                return False, f"output line {i + 1}: expected {want!r}, got {got!r}", output
        if len(outs) != len(self.expect):
            return False, f"expected {len(self.expect)} output line(s), got {len(outs)}", output
        if self.expect_panic is not None:
            if not panics:
                return False, f"expected a panic ({self.expect_panic!r}), but none happened", output
            if panics[0] != self.expect_panic:
                return False, f"expected panic {self.expect_panic!r}, got {panics[0]!r}", output
        else:
            if panics:
                return False, f"unexpected panic: {panics[0]!r}", output
            if not any(l.startswith(DONE) for l in lines):
                reason = "timed out" if timed_out else "QEMU stopped"
                return False, f"{reason} before test_main returned", output
        return True, "ok", output


# ---------------------------------------------------------------------------
# error/...: programs the compiler must reject

@dataclass
class ErrorTest:
    name: str
    description: str
    path: str
    line: int
    col: int
    message: str
    alone: bool = False

    @classmethod
    def from_file(cls, path):
        rel = os.path.relpath(path, os.path.join(ROOT, "tests", "rook", "errors"))
        base = os.path.splitext(rel)[0].replace(os.sep, "/")
        description, spec, alone = "", None, False
        for line in open(path):
            line = line.strip()
            if not line.startswith("//"):
                break
            text = line[2:].strip()
            if text.startswith("error:"):
                spec = text[len("error:"):].strip()
            elif text == "alone":
                alone = True
            elif not description:
                description = text
        if spec is None:
            raise SystemExit(f"{path}: missing `// error: LINE:COL: message` header")
        where, message = spec.split(" ", 1)
        line_no, col = where.rstrip(":").split(":")
        return cls(f"error/{base}", description, path, int(line_no), int(col), message.strip(),
                   alone)

    def run(self):
        lib = [] if self.alone else ERROR_LIB
        files = [(p, open(p).read()) for p in lib + [self.path]]
        try:
            compile_program(files, TRUSTED_DIRS)
        except CompileError as err:
            got = err.format(os.path.basename(self.path), dict(files).get(err.pos.file))
            if err.pos.file != self.path:
                return False, f"error in {err.pos.file}, not in the test", got
            if (err.pos.line, err.pos.col) != (self.line, self.col):
                return False, f"error at {err.pos.line}:{err.pos.col}, expected {self.line}:{self.col}", got
            if self.message not in err.message:
                return False, f"expected message containing {self.message!r}", got
            return True, "ok", got
        return False, "compiled without error", ""


# ---------------------------------------------------------------------------
# harness/...: checks of the harness and the build

@dataclass
class HarnessTest:
    name: str
    description: str
    check: object           # () -> None if fine, else a reason string

    def run(self):
        problem = self.check()
        return (problem is None, problem or "ok")


def check_log_cases():
    """check_qemu_log must reject a missing report and an unexpected one."""
    cases = [
        ("nothing reported, nothing expected", [], [], True),
        ("expected report seen", ["pl011_write: Bad offset 0x100"], ["Bad offset 0x100"], True),
        ("expected report missing", [], ["Bad offset 0x100"], False),
        ("unexpected report", ["pl011_write: Bad offset 0x100"], [], False),
        ("one expected, one extra",
         ["pl011_write: Bad offset 0x100", "bcm2835: unimplemented"], ["Bad offset 0x100"], False),
    ]
    for what, log, expected, ok in cases:
        if (check_qemu_log(log, expected) is None) != ok:
            return f"check_qemu_log wrong for: {what}"
    return None


def check_machines():
    """The suite must cover both the 1 GB and the 512 MB (Zero 2 W sized) machine."""
    for need in ("raspi3b", "raspi3ap"):
        if need not in MACHINES:
            return f"{need} is not in MACHINES ({' '.join(MACHINES)})"
    return None


def check_error_display():
    """The compiler command shows the source line with a caret under the error."""
    test = ErrorTest.from_file(os.path.join(ROOT, "tests", "rook", "errors", "assign_to_immutable.rook"))
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "bootstrap"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "fledgling", *ERROR_LIB, test.path,
         *[arg for d in TRUSTED_DIRS for arg in ("--trusted", d)]],
        cwd=ROOT, env=env, capture_output=True, text=True)
    lines = result.stderr.splitlines()
    if result.returncode != 1 or len(lines) < 3:
        return f"expected exit status 1 and an error, got {result.returncode}: {result.stderr!r}"
    source = open(test.path).read().splitlines()[test.line - 1]
    if lines[-2] != "    " + source:
        return f"source line not shown: {lines[-2]!r}"
    if lines[-1] != "    " + " " * (test.col - 1) + "^":
        return f"caret not under column {test.col}: {lines[-1]!r}"
    return None


def check_make_deps():
    """Changing the Makefile must rebuild everything it builds.

    Compares what make would build if the Makefile were newer (`-W Makefile`)
    with what it builds from nothing (`-B`).
    """
    def outputs(*flags):
        result = subprocess.run(["make", "-n", *flags, "test"], cwd=ROOT,
                                capture_output=True, text=True)
        found = set()
        for line in result.stdout.splitlines():
            words = line.split()
            if "-o" in words:
                found.add(words[words.index("-o") + 1])
            elif words and words[0].endswith("objcopy"):
                found.add(words[-1])
        return found

    everything = outputs("-B")
    if not everything:
        return "make would build nothing"
    stale = sorted(everything - outputs("-W", "Makefile"))
    if stale:
        return f"{len(stale)} file(s) would not be rebuilt, e.g. {stale[0]}"
    return None


def check_wrong_expectations_fail():
    """A Rook test whose expectation is wrong must fail, not pass."""
    path = os.path.join(ROOT, "tests", "rook", "trusted", "ram_512mb.rook")
    wrong = {
        "expect-fault: the wrong address": dict(expect_fault="FAR_EL1:  0x0000000040000000"),
        "expect-fault missing": dict(expect_fault=None),
        "expect-qemu-log: a report that never comes": dict(expect_qemu_log=["no such report"]),
    }
    for what, change in wrong.items():
        test = RookTest.from_file(path)
        for key, value in change.items():
            setattr(test, key, value)
        if test.run()[0]:
            return f"passed with {what}"
    return None


HARNESS_TESTS = [
    HarnessTest("harness/qemu_log_check", "QEMU's device log must match what a test expects, "
                "exactly", check_log_cases),
    HarnessTest("harness/machines", "Tests run on a 1 GB and a 512 MB Raspberry Pi machine",
                check_machines),
    HarnessTest("harness/wrong_expectations", "A Rook test with a wrong fault or QEMU-log "
                "expectation fails", check_wrong_expectations_fail),
    HarnessTest("harness/error_display", "The compiler shows the source line with a caret under "
                "the error", check_error_display),
    HarnessTest("harness/make_deps", "Changing the Makefile rebuilds everything built from it",
                check_make_deps),
]


# ---------------------------------------------------------------------------

def all_tests():
    tests = list(BOOT_TESTS) + HARNESS_TESTS
    for pattern in ("*.rook", "trusted/*.rook"):
        for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", pattern))):
            tests.append(RookTest.from_file(path))
    for pattern in ("*.rook", "trusted/*.rook"):
        for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "errors", pattern))):
            tests.append(ErrorTest.from_file(path))
    return tests


def main(argv):
    tests = all_tests()
    if argv:
        selected = [t for t in tests if any(t.name == a or t.name.startswith(a) for a in argv)]
        if not selected:
            print(f"no tests match: {' '.join(argv)}")
            return 2
    else:
        selected = tests

    os.makedirs(LOG_DIR, exist_ok=True)
    failures = 0
    width = max(len(t.name) for t in selected)
    for test in selected:
        start = time.monotonic()
        try:
            result = test.run()
        except Exception:
            # A crash inside the compiler fails this test only; the rest of
            # the suite still runs.
            result = (False, "compiler crashed", traceback.format_exc())
        passed, reason = result[0], result[1]
        output = result[2] if len(result) > 2 else ""
        elapsed = time.monotonic() - start
        print(f"{'PASS' if passed else 'FAIL'}  {test.name:<{width}}  {elapsed:4.1f}s  {test.description}")
        if not passed:
            failures += 1
            print(f"      reason: {reason}")
            if output:
                print("      output:")
                for line in output.replace("\r", "").splitlines():
                    print(f"        | {line}")

    print(f"\n{len(selected) - failures}/{len(selected)} passed (logs in build/test-logs/)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
