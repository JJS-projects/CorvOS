#!/usr/bin/env python3
"""CorvOS QEMU test suite.

Each test boots a kernel image in QEMU, watches the UART output, and checks
that the expected lines appear in order (and forbidden ones never do) within
a time limit. Full output of every run is saved to build/test-logs/ so it can
be inspected after a failure, locally or as a CI artifact.

Usage: python3 tests/run_tests.py [test-name ...]
"""

import os
import selectors
import subprocess
import sys
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "build", "test-logs")
QEMU = os.environ.get("QEMU", "qemu-system-aarch64")


@dataclass
class BootTest:
    name: str
    description: str
    image: str
    expect: list                      # lines that must appear, in this order
    forbid: list = field(default_factory=list)  # lines that must never appear
    timeout: float = 10.0


TESTS = [
    BootTest(
        name="boot-hello",
        description="kernel boots to EL1, prints over the UART and idles",
        image="build/kernel8.img",
        expect=[
            "Raven: hello from CorvOS",
            "Raven: firmware entered at EL",
            "Raven: running at EL1",
            "Raven: boot complete, idling",
        ],
        forbid=["unhandled exception"],
    ),
    BootTest(
        name="fault-report",
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


def run(test):
    """Boot one image; return (passed, reason, output)."""
    image = os.path.join(ROOT, test.image)
    if not os.path.exists(image):
        return False, f"missing {test.image} (run `make` first)", ""

    cmd = [QEMU, "-M", "raspi3b", "-kernel", image,
           "-display", "none", "-serial", "stdio", "-monitor", "none"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)

    output = ""
    deadline = time.monotonic() + test.timeout
    next_expected = 0
    reason = None
    try:
        while True:
            lines = output.replace("\r", "").split("\n")
            # Only judge complete lines (the last element may be partial).
            complete = lines[:-1]
            for bad in test.forbid:
                if any(bad in line for line in complete):
                    reason = f"forbidden output appeared: {bad!r}"
            if reason:
                break
            # Advance through expected lines in order.
            next_expected = 0
            for line in complete:
                if next_expected < len(test.expect) and test.expect[next_expected] in line:
                    next_expected += 1
            if next_expected == len(test.expect):
                # Give the kernel a moment to print anything forbidden after.
                time.sleep(0.3)
                more = _drain(sel, proc)
                output += more
                lines = output.replace("\r", "").split("\n")
                for bad in test.forbid:
                    if any(bad in line for line in lines):
                        reason = f"forbidden output appeared: {bad!r}"
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = (f"timed out after {test.timeout}s waiting for "
                          f"{test.expect[next_expected]!r}")
                break
            if proc.poll() is not None:
                output += _drain(sel, proc)
                reason = f"QEMU exited with code {proc.returncode}"
                break
            for key, _ in sel.select(timeout=min(remaining, 0.2)):
                chunk = os.read(key.fd, 4096)
                output += chunk.decode("utf-8", errors="replace")
    finally:
        proc.kill()
        proc.wait()

    return reason is None, reason or "ok", output


def _drain(sel, proc):
    out = ""
    for key, _ in sel.select(timeout=0):
        out += os.read(key.fd, 4096).decode("utf-8", errors="replace")
    return out


def main(argv):
    selected = [t for t in TESTS if not argv or t.name in argv]
    unknown = set(argv) - {t.name for t in TESTS}
    if unknown:
        print(f"unknown test(s): {', '.join(sorted(unknown))}")
        return 2

    os.makedirs(LOG_DIR, exist_ok=True)
    failures = 0
    for test in selected:
        start = time.monotonic()
        passed, reason, output = run(test)
        elapsed = time.monotonic() - start
        with open(os.path.join(LOG_DIR, f"{test.name}.log"), "w") as f:
            f.write(output)
        status = "PASS" if passed else "FAIL"
        print(f"{status}  {test.name:<14} {elapsed:4.1f}s  {test.description}")
        if not passed:
            failures += 1
            print(f"      reason: {reason}")
            print("      output:")
            for line in output.replace("\r", "").splitlines() or ["(none)"]:
                print(f"        | {line}")

    print(f"\n{len(selected) - failures}/{len(selected)} passed"
          f" (logs in build/test-logs/)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
