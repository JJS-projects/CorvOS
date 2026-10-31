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
                 // input: hello
                 // expect-echo: hello
             Type "hello" and a newline into the program's serial input once it
             has printed `Raven: running Rook test`, and pass when the program has
             written a line "hello" back, even if test_main never returns (a
             polling driver doesn't). At most 15 bytes, so the UART's FIFO holds
             them. Not run against a real board: this harness doesn't type into one.

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

CORVOS_SERIAL chooses where boot output is read from: unset (QEMU's own output),
`pty` (QEMU's serial port on a pseudo-terminal, read the way a real port is) or a
device such as /dev/ttyUSB0 for a real board. With a device only boot/hello runs:
the kernel on the board's SD card is whatever is there.

Usage: python3 tests/run_tests.py [name-or-prefix ...]
       e.g. `rook/` runs only the Rook program tests.
"""

import dataclasses
import glob
import os
import re
import selectors
import subprocess
import sys
import termios
import time
import tty
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
# Where boot output is read from: "stdio" (QEMU's own output), "pty" (QEMU's
# serial port on a pseudo-terminal), or a serial device such as /dev/ttyUSB0
# connected to a real board. See docs/hardware.md.
SERIAL = os.environ.get("CORVOS_SERIAL", "stdio")
# A board is slower to start than QEMU: its timeouts are this many times longer.
BOARD_PATIENCE = 6


def board_mode():
    return SERIAL not in ("stdio", "pty")


if board_mode():
    MACHINES = ["board"]

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

def open_serial(path):
    """Open a serial port (or a pseudo-terminal): raw, so that no
    byte is changed on its way in, at 115200 baud, 8N1."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[2] |= termios.CLOCAL | termios.CREAD      # no modem lines; receive
    attrs[4] = attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


def pty_path(proc, timeout=10):
    """The pseudo-terminal QEMU announces for `-serial pty`."""
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    seen = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen += read_available(sel, 0.1)
        match = re.search(r"char device redirected to (/dev/pts/\d+)", seen)
        if match:
            return match.group(1)
        if proc.poll() is not None:
            break
    raise RuntimeError(f"QEMU did not announce a serial pty: {seen!r}")


def boot(image, finished, timeout, machine, log_path, feed=None):
    """Boot `image` on `machine` until `finished(lines)` is true or `timeout` passes.

    Returns (output, timed_out, qemu_log). `lines` passed to `finished` are
    complete lines with carriage returns removed. `qemu_log` is the list of
    lines QEMU reported about unimplemented or misused devices.

    Where the output comes from depends on CORVOS_SERIAL: QEMU's own output
    (the default), QEMU's serial port on a pseudo-terminal read like a real
    port ("pty"), or a real serial port on a board (a device path).

    `feed`, if given, is typed into the program's serial input once the program
    has started (FEED_AFTER has been printed).
    """
    if SERIAL not in ("stdio", "pty"):
        return read_board(finished, timeout)
    if os.path.exists(log_path):
        os.remove(log_path)
    cmd = [QEMU, "-M", machine, "-kernel", image,
           "-display", "none", "-serial", SERIAL,
           "-d", "unimp,guest_errors", "-D", log_path]
    if SERIAL == "pty":
        # Output written before the port is opened would be lost, so QEMU
        # starts paused and is told to go once the port is open.
        cmd += ["-S", "-monitor", "stdio"]
    else:
        cmd += ["-monitor", "none"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if SERIAL == "pty" or feed
                            else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sel = selectors.DefaultSelector()
    port = None
    try:
        if SERIAL == "pty":
            port = open_serial(pty_path(proc))
            sel.register(port, selectors.EVENT_READ)
            proc.stdin.write(b"cont\n")
            proc.stdin.flush()
        else:
            sel.register(proc.stdout, selectors.EVENT_READ)
        typer = None
        if feed:
            if SERIAL == "pty":
                type_in = lambda data: os.write(port, data)
            else:
                type_in = lambda data: (proc.stdin.write(data), proc.stdin.flush())
            typer = (feed, type_in)
        output, timed_out = read_until(sel, finished, timeout, proc, typer)
        return output, timed_out, read_qemu_log(proc, log_path)
    finally:
        proc.kill()
        proc.wait()
        if port is not None:
            os.close(port)


def read_board(finished, timeout):
    """Read a real board's serial port. The board must be started (powered on or
    reset) by something else while this waits; nothing here can do that yet."""
    port = open_serial(SERIAL)
    sel = selectors.DefaultSelector()
    sel.register(port, selectors.EVENT_READ)
    try:
        output, timed_out = read_until(sel, finished, timeout * BOARD_PATIENCE, None)
        return output, timed_out, []
    finally:
        os.close(port)


def read_until(sel, finished, timeout, proc, typer=None):
    """Collect output from `sel` until `finished(lines)`, the timeout, or the
    process (if any) ends. Returns (output, timed_out). `typer` is (bytes,
    function): the bytes are passed to the function once, when the program starts."""
    output = ""
    deadline = time.monotonic() + timeout
    while True:
        if typer and FEED_AFTER in output:
            typer[1](typer[0])
            typer = None
        if finished(complete_lines(output)):
            # Catch anything printed straight afterwards.
            return output + read_available(sel, 0.2), False
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (proc is not None and proc.poll() is not None):
            return output + read_available(sel, 0), remaining <= 0
        output += read_available(sel, min(remaining, 0.2))


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
        if not board_mode() and not os.path.exists(image):
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
FEED_AFTER = "Raven: running Rook test"
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
    input: str = None               # typed (with a newline) into the serial input
    expect_echo: str = None         # a line the program must write back
    timeout: float = 10.0

    @classmethod
    def from_file(cls, path):
        rel = os.path.relpath(path, os.path.join(ROOT, "tests", "rook"))
        base = os.path.splitext(rel)[0].replace(os.sep, "/")
        description, expect, panic, qemu_log = "", [], None, []
        fault, machines, typed, echo = None, None, None, None
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
            elif text.startswith("input:"):
                typed = text[len("input:"):].strip()
            elif text.startswith("expect-echo:"):
                echo = text[len("expect-echo:"):].strip()
            elif not description:
                description = text
        return cls(f"rook/{base}", description, f"build/rook-tests/{base}.img", expect, panic,
                   qemu_log, fault, machines, input=typed, expect_echo=echo)

    def run(self):
        return on_each_machine(self, self.run_on, self.machines)

    def run_on(self, machine):
        image = os.path.join(ROOT, self.image)
        if not board_mode() and not os.path.exists(image):
            return False, missing_image(image)

        if board_mode() and self.input is not None:
            return True, "skipped: input is not typed into a board", ""

        def finished(lines):
            return any(l.startswith((DONE, PANIC)) or "unhandled exception" in l
                       or (self.expect_echo is not None and self.expect_echo in lines)
                       for l in lines)

        typed = None if self.input is None else (self.input + "\n").encode()
        output, timed_out, qemu_log = boot(image, finished, self.timeout, machine,
                                           log_base(self.name, machine) + ".qemu", typed)
        save_log(self.name, machine, output, qemu_log)
        lines = complete_lines(output)
        problem = check_qemu_log(qemu_log, self.expect_qemu_log)
        if problem:
            return False, problem, output
        outs = [l[len("out: "):] for l in lines if l.startswith("out: ")]
        panics = [l[len(PANIC):] for l in lines if l.startswith(PANIC)]

        if len(panics) > 1:
            return False, f"more than one panic: {panics[1]!r}", output
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
        elif self.expect_echo is not None:
            if panics:
                return False, f"unexpected panic: {panics[0]!r}", output
            if self.expect_echo not in lines:
                return False, f"expected the program to write back {self.expect_echo!r}", output
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


def with_serial(mode, run):
    """Run `run()` with boot output read the way `mode` says (see SERIAL)."""
    global SERIAL, MACHINES
    saved = SERIAL, MACHINES
    SERIAL = mode
    MACHINES = ["board"] if board_mode() else saved[1]
    try:
        return run()
    finally:
        SERIAL, MACHINES = saved


def check_serial_pty():
    """A Rook test passes with its output read from QEMU's serial port on a
    pseudo-terminal, as it will be from a real board's."""
    test = RookTest.from_file(os.path.join(ROOT, "tests", "rook", "text.rook"))
    result = with_serial("pty", lambda: test.run_on("raspi3b"))
    return None if result[0] else f"through a pty: {result[1]}"


def check_serial_raw():
    """A serial port is opened raw: no byte changes on its way in. (A terminal
    left in its default mode turns CR into LF and acts on Ctrl-U and DEL.)"""
    master, slave = os.openpty()
    try:
        port = open_serial(os.ttyname(slave))
        sent = b"a\x15b\x7fc\x03d\r\ne\x04f\n"
        os.write(master, sent)
        time.sleep(0.1)
        got = os.read(port, 100)
        os.close(port)
    finally:
        os.close(master)
        os.close(slave)
    return None if got == sent else f"sent {sent!r}, read {got!r}"


def check_serial_board():
    """The path a real board will use: a serial device with no QEMU behind it.
    A pseudo-terminal plays the board and sends boot/hello's lines."""
    import threading
    hello = [t for t in BOOT_TESTS if t.name == "boot/hello"][0]
    # With a board there is no local image to find: this one doesn't exist.
    hello = dataclasses.replace(hello, image="build/no-such-kernel.img")
    master, slave = os.openpty()
    name = os.ttyname(slave)

    def board():
        time.sleep(0.3)
        for line in hello.expect:
            os.write(master, (line + "\r\n").encode())

    thread = threading.Thread(target=board)
    thread.start()
    try:
        result = with_serial(name, lambda: hello.run_on("board"))
    finally:
        thread.join()
        os.close(master)
        os.close(slave)
    if not result[0]:
        return f"from a board: {result[1]}"
    chosen = [t.name for t in runnable_on_board(all_tests())]
    return None if chosen == ["boot/hello"] else f"with a board, these would run: {chosen}"


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


def check_echo_expectations():
    """An echo test passes only if the program really writes the typed line back."""
    if board_mode():
        return None
    path = os.path.join(ROOT, "tests", "rook", "echo_demo.rook")
    wrong = {
        "an echo that is not what was typed": dict(expect_echo="goodbye"),
        "no input typed": dict(input=None),
    }
    for what, change in wrong.items():
        test = RookTest.from_file(path)
        test.machines, test.timeout = [MACHINES[0]], 3.0
        for key, value in change.items():
            setattr(test, key, value)
        if test.run()[0]:
            return f"passed with {what}"
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
    HarnessTest("harness/echo_expectations", "A Rook test that expects an echo fails unless the "
                "typed line comes back", check_echo_expectations),
    HarnessTest("harness/serial_pty", "Output is read from QEMU's serial port on a pty, as a "
                "board's will be", check_serial_pty),
    HarnessTest("harness/serial_raw", "A serial port is read raw: no byte is changed",
                check_serial_raw),
    HarnessTest("harness/serial_board", "A board on a serial device is read without QEMU",
                check_serial_board),
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
    if board_mode():
        selected = runnable_on_board(selected)
        if not selected:
            print("with a board, only boot/hello can run: it is the kernel on the SD card")
            return 2
        print(f"reading the board on {SERIAL}: start (or reset) it now")

    return run_all(selected)


def runnable_on_board(tests):
    """Of `tests`, those that make sense with a board: it runs whatever kernel is
    on its SD card, so only the normal kernel's boot test."""
    return [t for t in tests if t.name == "boot/hello"]


def run_all(selected):
    """Run `selected` tests, print one line each, and return the exit status."""
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
