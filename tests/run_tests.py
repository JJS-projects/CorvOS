#!/usr/bin/env python3
"""CorvOS test suite.

Three kinds of test:

  boot/...   Kernel boot tests, defined in BOOT_TESTS below. Boot an image in
             QEMU and check that expected UART lines appear in order, and
             forbidden ones never do.

  rook/...   Rook programs in tests/rook/*.rook. Each is compiled by Fledgling,
             linked into a test kernel that calls its `test_main()`, and booted
             in QEMU. Its header comments say what it must print:
                 // expect: 42                  one per `out:` line, exact, in order
                 // expect-panic: integer overflow at x.rook:7
             Without expect-panic, test_main must return normally.

  error/...  Programs in tests/rook/errors/*.rook that Fledgling must reject:
                 // error: 7:5: can't assign to `x`
             The error must be at that line:column and contain that message.

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
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
LOG_DIR = os.path.join(ROOT, "build", "test-logs")
QEMU = os.environ.get("QEMU", "qemu-system-aarch64")

from fledgling import compile_source, CompileError  # noqa: E402


# ---------------------------------------------------------------------------
# Booting an image in QEMU

def boot(image, finished, timeout):
    """Boot `image` until `finished(lines)` is true or `timeout` passes.

    Returns (output, timed_out). `lines` passed to `finished` are complete
    lines with carriage returns removed.
    """
    cmd = [QEMU, "-M", "raspi3b", "-kernel", image,
           "-display", "none", "-serial", "stdio", "-monitor", "none"]
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
                return output, False
            remaining = deadline - time.monotonic()
            if remaining <= 0 or proc.poll() is not None:
                output += read_available(sel, 0)
                return output, remaining <= 0
            output += read_available(sel, min(remaining, 0.2))
    finally:
        proc.kill()
        proc.wait()


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


def save_log(name, output):
    path = os.path.join(LOG_DIR, name.replace("/", "__") + ".log")
    with open(path, "w") as f:
        f.write(output)


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
        image = os.path.join(ROOT, self.image)
        if not os.path.exists(image):
            return False, missing_image(image)

        def progress(lines):
            n = 0
            for line in lines:
                if n < len(self.expect) and self.expect[n] in line:
                    n += 1
            return n

        output, timed_out = boot(image, lambda lines: progress(lines) == len(self.expect),
                                 self.timeout)
        save_log(self.name, output)
        lines = complete_lines(output)
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
    timeout: float = 10.0

    @classmethod
    def from_file(cls, path):
        base = os.path.splitext(os.path.basename(path))[0]
        description, expect, panic = "", [], None
        for line in open(path):
            line = line.strip()
            if not line.startswith("//"):
                break
            text = line[2:].strip()
            if text.startswith("expect:"):
                expect.append(text[len("expect:"):].strip())
            elif text.startswith("expect-panic:"):
                panic = text[len("expect-panic:"):].strip()
            elif not description:
                description = text
        return cls(f"rook/{base}", description, f"build/rook-tests/{base}.img", expect, panic)

    def run(self):
        image = os.path.join(ROOT, self.image)
        if not os.path.exists(image):
            return False, missing_image(image)

        def finished(lines):
            return any(l.startswith((DONE, PANIC)) or "unhandled exception" in l
                       for l in lines)

        output, timed_out = boot(image, finished, self.timeout)
        save_log(self.name, output)
        lines = complete_lines(output)
        outs = [l[len("out: "):] for l in lines if l.startswith("out: ")]
        panics = [l[len(PANIC):] for l in lines if l.startswith(PANIC)]

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

    @classmethod
    def from_file(cls, path):
        base = os.path.splitext(os.path.basename(path))[0]
        description, spec = "", None
        for line in open(path):
            line = line.strip()
            if not line.startswith("//"):
                break
            text = line[2:].strip()
            if text.startswith("error:"):
                spec = text[len("error:"):].strip()
            elif not description:
                description = text
        if spec is None:
            raise SystemExit(f"{path}: missing `// error: LINE:COL: message` header")
        where, message = spec.split(" ", 1)
        line_no, col = where.rstrip(":").split(":")
        return cls(f"error/{base}", description, path, int(line_no), int(col), message.strip())

    def run(self):
        source = open(self.path).read()
        try:
            compile_source(source, os.path.basename(self.path))
        except CompileError as err:
            got = err.format(os.path.basename(self.path))
            if (err.pos.line, err.pos.col) != (self.line, self.col):
                return False, f"error at {err.pos.line}:{err.pos.col}, expected {self.line}:{self.col}", got
            if self.message not in err.message:
                return False, f"expected message containing {self.message!r}", got
            return True, "ok", got
        return False, "compiled without error", ""


# ---------------------------------------------------------------------------

def all_tests():
    tests = list(BOOT_TESTS)
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "*.rook"))):
        tests.append(RookTest.from_file(path))
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "rook", "errors", "*.rook"))):
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
        result = test.run()
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
