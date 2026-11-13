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
             Without expect-panic, test_main must return normally. A panic inside one of
             Raven's files may end `at mailbox.rook:*` (any line); the test's own file
             keeps its exact line.
                 // expect-fault: FAR_EL1:  0x0000000030000000
             The program must end in a CPU exception, whose report (see
             kernel/arch/aarch64/vectors.s) has a line containing this text.
                 // machines: raspi3b
             Run only on the listed machines (for tests of the machine itself).
                 // timeout: 60
             Seconds the test may take (10 by default): for a test that does a lot of work, such as
             filling the whole region.
                 // expect-failure: handler gave up at x.rook:15
             (add ` x40` for 40 identical lines) An actor's handler panicked (or ran over its budget) and Raven restarted the
             actor: the program carries on and returns. One line per failure, in order; the
             panic lines must be exactly these (decision 148). `expect-panic` is for a panic
             that ends the program.
                 // expect-line-count: hello 1
             The program (which must return) wrote the line "hello" exactly once: a way to
             test that something did *not* get written.
                 // input: hello
                 // expect-echo: hello
             Type "hello" and a newline into the program's serial input once it
             has printed `Raven: running Rook test`, and pass when the program has
             written a line "hello" back, even if test_main never returns (a
             polling driver doesn't). At most 15 bytes, so the UART's FIFO holds
             them. Several pairs are typed one after another: each `input:` after the
             first is typed once the line before it has come back. With `expect-failure:`
             lines too, the program's contained failures must be exactly those, before it
             has echoed everything (a driver that is killed and restarted). `input:` alone
             types and expects nothing back (test_main must return). Not run against
             a real board: this harness doesn't type into one.

  error/...  Programs in tests/rook/errors/*.rook (or errors/trusted/, compiled
             as trusted) that Fledgling must reject:
                 // error: 7:5: can't assign to `x`
             The error must be in that file, at that line:column, and contain
             that message. A header line `// alone` compiles the file by itself,
             without Raven's code (for errors about what a program must include).

  A header line `// retries: 3` lets a test of a timing race (one a loaded machine can
             upset, such as a handler waiting for ring room against the 100 ms budget) pass if
             any of up to 3 runs does; a real regression, such as a deadlock, fails every run.

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

Usage: python3 tests/run_tests.py [-v] [name-or-prefix ...]   (-v or VERBOSE=1: a line per test)
       e.g. `rook/` runs only the Rook program tests.
"""

import concurrent.futures
import dataclasses
import codecs
import glob
import os
import re
import selectors
import threading
import subprocess
import sys
import tempfile
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

from fledgling import compile_program, CompileError, is_trusted  # noqa: E402
from fledgling.dump import dump_tokens, dump_tree, dump_expanded, dump_declarations, dump_bodies, dump_asm, make_stream, tree  # noqa: E402

# Error tests are compiled with Raven's Rook code and the test output functions
# (as test programs are), and the directories trusted with `unsafe`.
ROOK_LIB = (sorted(glob.glob(os.path.join(ROOT, "raven", "trusted", "*.rook")))
            + sorted(glob.glob(os.path.join(ROOT, "raven", "*.rook")))
            + [os.path.join(ROOT, "tests", "rook", "support", "trusted", "print.rook"),
               os.path.join(ROOT, "tests", "rook", "support", "shell.rook"),
               os.path.join(ROOT, "tests", "rook", "support", "generic_library.rook")])
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

    `feed`, if given, is a list of (bytes to type, line to wait for before typing
    the next): see Typist.
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
                # (written from a thread: a program that stops reading, such as a miscompiled compiler given
                # half a megabyte, would otherwise block this write for good and the timeout would never be seen)
                def type_in(data):
                    def write():
                        try:
                            proc.stdin.write(data)
                            proc.stdin.flush()
                        except (OSError, ValueError):
                            pass    # QEMU was killed
                    threading.Thread(target=write, daemon=True).start()
            typer = Typist(feed, type_in)
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


class Typist:
    """Types a test program's input: the first part once the program has started
    (FEED_AFTER has been printed), each later part once the line that followed the
    one before has come back."""

    def __init__(self, parts, send):
        self.parts, self.send, self.next = parts, send, 0

    def poll(self, output):
        if self.next >= len(self.parts):
            return
        if self.next == 0:
            ready = FEED_AFTER in output
        else:
            ready = self.parts[self.next - 1][1] in complete_lines(output)
        if ready:
            self.send(self.parts[self.next][0])
            self.next += 1


def read_until(sel, finished, timeout, proc, typer=None):
    """Collect output from `sel` until `finished(lines)`, the timeout, or the
    process (if any) ends. Returns (output, timed_out). `typer` types the program's
    input as it goes (see Typist)."""
    output = ""
    deadline = time.monotonic() + timeout
    while True:
        if typer:
            typer.poll(output)
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
    """Whatever the registered files give within `wait` seconds, as text. A character of several
    bytes can arrive in two reads (a busy machine splits them anywhere), so each file has an
    incremental decoder that waits for the rest instead of turning each half into an error."""
    decoders = sel.__dict__.setdefault("decoders", {})
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
            decoder = decoders.setdefault(key.fd, codecs.getincrementaldecoder("utf-8")(errors="replace"))
            out += decoder.decode(chunk)


def complete_lines(output):
    return output.replace("\r", "").split("\n")[:-1]


def log_base(name, machine):
    # CI uploads the logs as an artifact, which refuses a name with ":" (live: cases)
    return os.path.join(LOG_DIR, name.replace("/", "__").replace(":", "_") + "." + machine)


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
            "Raven: MMU and caches on",
            "Raven: 4 cores online",
            "Raven: boot complete, idling",
        ],
        forbid=["unhandled exception", "panic", "OFF"],
    ),
    BootTest(
        name="boot/hello-rook-built",
        description="the kernel built by the Rook compiler (make rook-kernel, decision 170) boots as the other does",
        image="build/rook/kernel8.img",
        expect=[
            "Raven: hello from CorvOS",
            "Raven: firmware entered at EL",
            "Raven: running at EL1",
            "Raven: MMU and caches on",
            "Raven: 4 cores online",
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


def panic_matches(want, got, own_file):
    """Does the panic line `got` fit the expected `want`?  Exact, except that a site in one of
    Raven's own files may end in `:*` (`out of memory at mailbox.rook:*`): those lines move
    whenever raven/trusted changes. The test's own file (`own_file`) keeps its exact line."""
    if not want.endswith(":*"):
        return want == got
    head, _, file = want[:-2].rpartition(" at ")
    if file == own_file:
        return False
    return re.fullmatch(re.escape(head + " at " + file) + r":\d+", got) is not None


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
    input: list = field(default_factory=list)        # typed (with a newline) into the input
    expect_echo: list = field(default_factory=list)  # lines the program must write back
    line_counts: list = field(default_factory=list)  # (line, times) it must write
    expect_failures: list = field(default_factory=list)   # contained panics, in order
    timeout: float = 10.0
    failures_open: bool = False     # the last expected failure may repeat (`xN+`)
    retries: int = 1                # runs allowed: it passes if any one does (a test of a timing race, decision 188)

    @classmethod
    def from_file(cls, path):
        rel = os.path.relpath(path, os.path.join(ROOT, "tests", "rook"))
        base = os.path.splitext(rel)[0].replace(os.sep, "/")
        description, expect, panic, qemu_log = "", [], None, []
        fault, machines, typed, echo, counts = None, None, [], [], []
        failures, timeout, retries, failures_open = [], 10.0, 1, False
        for line in open(path):
            line = line.strip()
            if not line.startswith("//"):
                break
            text = line[2:].strip()
            if text.startswith("expect:"):
                expect.append(text[len("expect:"):].strip())
            elif text.startswith("expect-panic:"):
                panic = text[len("expect-panic:"):].strip()
            elif text.startswith("expect-failure:"):
                what, _, times = text[len("expect-failure:"):].strip().rpartition(" x")
                if what and times.isdigit():
                    failures += [what] * int(times)      # "msg at f.rook:7 x40": 40 times
                elif what and times.endswith("+") and times[:-1].isdigit():
                    failures += [what] * int(times[:-1])     # "msg x3+": 3 times or more (a storm ends when a supervisor
                    failures_open = True                     # gets to it: how many restarts are in flight is not fixed)
                else:
                    failures.append(text[len("expect-failure:"):].strip())
            elif text.startswith("expect-fault:"):
                fault = text[len("expect-fault:"):].strip()
            elif text.startswith("machines:"):
                machines = text[len("machines:"):].split()
            elif text.startswith("timeout:"):
                timeout = float(text[len("timeout:"):])
            elif text.startswith("retries:"):
                retries = int(text[len("retries:"):])
            elif text.startswith("expect-qemu-log:"):
                qemu_log.append(text[len("expect-qemu-log:"):].strip())
            elif text.startswith("input:"):
                typed.append(text[len("input:"):].strip())
            elif text.startswith("expect-echo:"):
                echo.append(text[len("expect-echo:"):].strip())
            elif text.startswith("expect-line-count:"):
                line_text, times = text[len("expect-line-count:"):].strip().rsplit(" ", 1)
                counts.append((line_text, int(times)))
            elif not description:
                description = text
        return cls(f"rook/{base}", description, f"build/rook-tests/{base}.img", expect, panic,
                   qemu_log, fault, machines, input=typed, expect_echo=echo,
                   line_counts=counts, expect_failures=failures, timeout=timeout, retries=retries,
                   failures_open=failures_open)

    def own_file(self):
        """The test's own source file name, as a panic line names it."""
        return self.name.rsplit("/", 1)[-1] + ".rook"

    def run(self):
        return on_each_machine(self, self.run_on, self.machines)

    def run_on(self, machine):
        """One run, or up to `retries` for a test of a timing race that a loaded machine can upset: it passes if any run
        does, and a real regression (a deadlock hangs every run) fails all of them."""
        for attempt in range(max(1, self.retries)):
            ok, message, output = self.run_once(machine)
            if ok:
                return ok, message if attempt == 0 else f"ok (run {attempt + 1} of {self.retries})", output
        return ok, f"{message} (after {self.retries} runs)" if self.retries > 1 else message, output

    def run_once(self, machine):
        image = os.path.join(ROOT, self.image)
        if not board_mode() and not os.path.exists(image):
            return False, missing_image(image)

        if board_mode() and self.input:
            return True, "skipped: input is not typed into a board", ""

        def finished(lines):
            ends = DONE if self.expect_failures else (DONE, PANIC)
            return any(l.startswith(ends) or "unhandled exception" in l
                       or (self.expect_echo and all(e in lines for e in self.expect_echo))
                       for l in lines)

        # Each part is typed with a newline; the line to wait for before the next part
        # is the echo that goes with it (or none, if the test expects no echoes).
        typed = [((text + "\n").encode(), self.expect_echo[i] if i < len(self.expect_echo) else "")
                 for i, text in enumerate(self.input)]
        output, timed_out, qemu_log = boot(image, finished, self.timeout, machine,
                                           log_base(self.name, machine) + ".qemu", typed)
        save_log(self.name, machine, output, qemu_log)
        lines = complete_lines(output)
        problem = check_qemu_log(qemu_log, self.expect_qemu_log)
        if problem:
            return False, problem, output
        outs = [l[len("out: "):] for l in lines if l.startswith("out: ")]
        panics = [l[len(PANIC):] for l in lines if l.startswith(PANIC)]

        if self.expect_failures:
            wanted = list(self.expect_failures)
            if self.failures_open and len(panics) > len(wanted):
                wanted += [wanted[-1]] * (len(panics) - len(wanted))     # `xN+`: more of the last one
            if len(panics) != len(wanted) or not all(
                    panic_matches(w, g, self.own_file()) for w, g in zip(wanted, panics)):
                return False, f"expected the failures {self.expect_failures!r}, got {panics!r}", output
        elif len(panics) > 1:
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
            if not panic_matches(self.expect_panic, panics[0], self.own_file()):
                return False, f"expected panic {self.expect_panic!r}, got {panics[0]!r}", output
        elif self.expect_echo:
            if panics and not self.expect_failures:       # (contained failures were matched above, decision 184)
                return False, f"unexpected panic: {panics[0]!r}", output
            for echo in self.expect_echo:
                if echo not in lines:
                    return False, f"expected the program to write back {echo!r}", output
        else:
            if panics and not self.expect_failures:
                return False, f"unexpected panic: {panics[0]!r}", output
            if not any(l.startswith(DONE) for l in lines):
                reason = "timed out" if timed_out else "QEMU stopped"
                return False, f"{reason} before test_main returned", output
            for text, times in self.line_counts:
                seen = lines.count(text)
                if seen != times:
                    return False, f"expected the line {text!r} {times} time(s), saw it {seen}", output
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
# hosted/...: a compiler program run on its input (docs/self-hosting.md)

# What Fledgling prints for the same input, for each hosted program that has a reference.
HOSTED_REFERENCE = {"lexdump": dump_tokens, "parsedump": dump_tree, "expanddump": dump_expanded,
                    "declsdump": dump_declarations,
                    "bodiesdump": lambda stream: dump_bodies(stream, ownership=True, actors=True, bodies="all"),
                    "asmdump": dump_asm}
# Stages that work on a whole program are given a stream of files (`@@@ PATH` header lines, see
# fledgling.dump.split_stream); in live.txt a line names the files of one stream, in order.
STREAM_STAGES = {"expanddump", "declsdump", "bodiesdump", "asmdump"}

HOSTED_BEGIN = "Hosted: begin"
HOSTED_END = "Hosted: end"
END_OF_INPUT = b"\x04"


@dataclass
class HostedTest:
    """A program of the self-hosted compiler (compiler/PROGRAM.rook) booted in QEMU and given
    the contents of tests/hosted/PROGRAM/NAME.in on the UART, ended by the byte 4. It writes its
    result between the lines `Hosted: begin` and `Hosted: end`, and that must be exactly
    NAME.out. The input is text without the bytes 2, 3 or 4, and the output ends in a newline."""
    name: str
    description: str
    image: str
    input_path: str
    expected_path: str
    timeout: float = 60.0   # a safety net, not a speed test: a busy machine runs many QEMUs at once
                            # (a program from live.txt gets 120 s, a whole program 600 s: a loaded machine ran many of them at once)
    live: bool = False      # no .out file: Fledgling's own output for the input is the expectation
    sources: tuple = ()     # live, for a stream stage: the repository files (paths from the root) it is made of

    @classmethod
    def live_files(cls, program, listing):
        """One test per file named in tests/hosted/PROGRAM/live.txt (paths from the repository
        root; `#` lines are notes): the file is the input and Fledgling's dump of it the expectation."""
        tests = []
        for line in open(listing).read().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names = tuple(line.split()) if program in STREAM_STAGES else ()
                # (a program of many files is named by its last file, so the name and the log's name stay short)
                shown = f"{names[-1]} (+{len(names) - 1} files)" if len(names) > 1 else line
                tests.append(cls(f"hosted/{program}/live:{shown}", f"{program} on {line}, against Fledgling",
                                 f"build/compiler/{program}.img", os.path.join(ROOT, names[-1] if names else line),
                                 None, 600.0 if names else 120.0, True, names))
        return tests

    @classmethod
    def from_file(cls, in_path):
        program = os.path.basename(os.path.dirname(in_path))
        case = os.path.splitext(os.path.basename(in_path))[0]
        base = os.path.splitext(in_path)[0]
        return cls(f"hosted/{program}/{case}", f"{program} on {case}.in",
                   f"build/compiler/{program}.img", in_path, base + ".out")

    def stream(self):
        """The input of a stream stage: the files named in `sources`, one after the other."""
        files = []
        for rel in self.sources:
            path = os.path.join(ROOT, rel)
            files.append((rel, is_trusted(path, TRUSTED_DIRS), open(path).read()))
        return make_stream(files)

    def run(self):
        # The protocol is the UART and the same on both machines (the `numberer` shows it); the
        # compiler's programs, with many cases each, run on the first machine only.
        machines = None if self.image.endswith("/numberer.img") else [MACHINES[0]]
        return on_each_machine(self, self.run_on, machines)

    def run_on(self, machine):
        image = os.path.join(ROOT, self.image)
        if board_mode():
            return True, "skipped: hosted programs are not run on a board", ""
        if not os.path.exists(image):
            return False, missing_image(image)
        if self.sources:
            stream = self.stream()
            data = stream.encode() + END_OF_INPUT
        else:
            data = open(self.input_path, "rb").read() + END_OF_INPUT
        output, timed_out, qemu_log = boot(
            image, lambda lines: HOSTED_END in lines, self.timeout, machine,
            log_base(self.name, machine) + ".qemu", [(data, "")])
        save_log(self.name, machine, output, qemu_log)
        problem = check_qemu_log(qemu_log, [])
        if problem:
            return False, problem, output
        lines = complete_lines(output)
        if HOSTED_BEGIN not in lines or HOSTED_END not in lines:
            return False, f"no `{HOSTED_BEGIN}` ... `{HOSTED_END}` output" + (
                " before the timeout" if timed_out else ""), output
        got = lines[lines.index(HOSTED_BEGIN) + 1:lines.index(HOSTED_END)]
        if self.live:
            reference = HOSTED_REFERENCE[self.image.split("/")[-1][:-4]]
            want = reference(self.stream() if self.sources else open(self.input_path).read()).splitlines()
        else:
            want = open(self.expected_path).read().splitlines()
        if got != want:
            for i, (g, w) in enumerate(zip(got + [None] * len(want), want + [None] * len(got))):
                if g != w:
                    return False, f"output line {i + 1}: expected {w!r}, got {g!r}", output
        return True, "ok", output


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
    test = RookTest.from_file(os.path.join(ROOT, "tests", "rook", "text_literals.rook"))
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
        "an echo that is not what was typed": dict(expect_echo=["goodbye"]),
        "no input typed": dict(input=[]),
    }
    for what, change in wrong.items():
        test = RookTest.from_file(path)
        test.machines, test.timeout = [MACHINES[0]], 3.0
        for key, value in change.items():
            setattr(test, key, value)
        if test.run()[0]:
            return f"passed with {what}"
    return None


def thread_ticks(pid):
    """CPU time, user plus system, of each thread of a process, in clock ticks."""
    ticks = {}
    for task in os.listdir(f"/proc/{pid}/task"):
        try:
            with open(f"/proc/{pid}/task/{task}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        ticks[task] = int(fields[11]) + int(fields[12])
    return ticks


def check_idle_sleeps():
    """A core with nothing to run sleeps (`wfi`) instead of spinning. Boots the
    interrupt echo, which idles in `run_forever`, and counts QEMU's busy threads. Cores
    1 to 3 are parked with `wfe`, which QEMU spins on (one busy thread each), so a
    sleeping core 0 leaves exactly three; a spinning one makes four."""
    if board_mode():
        return None
    image = os.path.join(ROOT, "build", "rook-tests", "echo_interrupt.img")
    if not os.path.exists(image):
        return missing_image(image)
    cmd = [QEMU, "-M", MACHINES[0], "-kernel", image, "-display", "none",
           "-serial", "stdio", "-monitor", "none"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    try:
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        seen, deadline = "", time.monotonic() + 10
        while FEED_AFTER not in seen and time.monotonic() < deadline:
            seen += read_available(sel, 0.2)
        if FEED_AFTER not in seen:
            return "the test program did not start"
        time.sleep(0.5)                              # let it reach its idle loop
        before = thread_ticks(proc.pid)
        time.sleep(2.0)
        after = thread_ticks(proc.pid)
        busy = sum(1 for t, n in after.items()
                   if (n - before.get(t, 0)) / os.sysconf("SC_CLK_TCK") > 1.5)
        if busy > 3:
            return f"{busy} QEMU threads were busy for 2 s; core 0 is not sleeping"
        return None
    finally:
        proc.kill()
        proc.wait()


SURFACE_FILE = os.path.join(ROOT, "tests", "public-surface.txt")


def public_surface():
    """What application code can reach in Raven: for each trusted module that says
    `module public`, its `pub` names, and `Type.name` for the `pub` methods and fields of
    its types. Closed modules are not listed, because application code can't import them."""
    from fledgling import syntax as S
    from fledgling.lexer import lex
    from fledgling.parser import parse
    found = {}
    for directory in (os.path.join(ROOT, "raven", "trusted"),
                      os.path.join(ROOT, "tests", "rook", "support", "trusted")):
        for path in sorted(glob.glob(os.path.join(directory, "*.rook"))):
            items = parse(lex(open(path).read(), path)).items
            if not any(isinstance(i, S.ModuleDecl) for i in items):
                continue
            names = []
            for item in items:
                if not getattr(item, "public", False) or isinstance(item, (S.Import, S.ModuleDecl)):
                    continue
                names.append(item.name)
                for method in getattr(item, "methods", []):
                    if method.public:
                        names.append(f"{item.name}.{method.name}")
                for fld in getattr(item, "fields", []):
                    if getattr(fld, "public", False):
                        names.append(f"{item.name}.{fld.name}")
            found[os.path.splitext(os.path.basename(path))[0]] = sorted(names)
    return found


def check_public_surface():
    """The names application code can reach in Raven are exactly those in
    tests/public-surface.txt. A new `pub` name in a public module, or a module opened with
    `module public`, fails this until someone has looked at what it gives away (decision
    142): is it a number used as authority, or a function that reaches memory or code from
    one? If not, add it to the file."""
    wanted = {}
    for raw in open(SURFACE_FILE):
        if raw.strip() and not raw.startswith("#"):
            module, names = raw.split(":", 1)
            wanted[module.strip()] = sorted(n.strip() for n in names.split(",") if n.strip())
    found = public_surface()
    if found == wanted:
        return None
    lines = []
    for module in sorted(set(found) | set(wanted)):
        added = sorted(set(found.get(module, [])) - set(wanted.get(module, [])))
        gone = sorted(set(wanted.get(module, [])) - set(found.get(module, [])))
        if added:
            lines.append(f"{module}: new public {', '.join(added)}")
        if gone:
            lines.append(f"{module}: no longer public {', '.join(gone)}")
    return "the public surface of Raven changed (tests/public-surface.txt): " + "; ".join(lines)


def check_failure_expectations():
    """A test that expects contained failures fails if the program's failures differ: other
    text, one missing, or one extra."""
    path = os.path.join(ROOT, "tests", "rook", "restart_basic.rook")
    wrong = {
        "other failure text": ["something else at restart_basic.rook:41"],
        "a missing failure": [],
        "an extra failure": ["it blew up at restart_basic.rook:41"] * 2,
    }
    for what, failures in wrong.items():
        test = RookTest.from_file(path)
        test.machines = [MACHINES[0]]
        test.expect_failures = failures
        if test.run()[0]:
            return f"passed with {what}"
    return None


def check_line_counts():
    """A test that expects a line written exactly N times fails when it was written more or
    fewer times (the revocation demo checks the line is NOT written again)."""
    path = os.path.join(ROOT, "tests", "rook", "capability_revoke.rook")
    for times in (0, 2):
        test = RookTest.from_file(path)
        test.machines = [MACHINES[0]]
        test.line_counts = [("hello", times)]
        if test.run()[0]:
            return f"passed with the line expected {times} times"
    return None


def check_import_ambiguous():
    """A module is a file named without `.rook`; two files of one name can't be imported
    (an error test compiles one file beside Raven's, so it can't make this case)."""
    twin = [("one/shared.rook", "pub fn alpha() {\n}\n"),
            ("two/shared.rook", "pub fn beta() {\n}\n"),
            ("main.rook", "import shared: alpha\n\nfn f() {\n    alpha()\n}\n")]
    try:
        compile_program(twin)
    except CompileError as err:
        if "names more than one file" in err.message:
            return None
        return f"wrong error: {err.message}"
    return "two files named `shared` were accepted"


def check_suggest_imports():
    """The compiler can list the imports a file lacks instead of failing (the hint a script
    uses to add them), and generates nothing then."""
    files = [("shared.rook", "pub fn alpha() {\n}\n"),
             ("main.rook", "fn f() {\n    alpha()\n}\n")]
    found = []
    try:
        asm = compile_program(files, (), found)
    except CompileError as err:
        return f"failed instead of suggesting: {err.message}"
    if found != [("main.rook", "shared", "alpha")] or asm != "":
        return f"suggested {found!r} and generated {asm!r}"
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


def check_quiet_output():
    """A run prints failures in full and a summary; a line per passing test only with -v."""
    import contextlib
    import io

    class Fake:
        def __init__(self, name, ok):
            self.name, self.description, self.ok = name, "d", ok

        def run(self):
            return (self.ok, "" if self.ok else "the reason", "" if self.ok else "the output")

    tests = [Fake("fake/good", True), Fake("fake/bad", False)]
    global VERBOSE
    saved = VERBOSE
    try:
        text = {}
        for verbose in (False, True):
            VERBOSE = verbose
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = run_all(tests)
            text[verbose] = out.getvalue()
            if status != 1:
                return f"a failing test gave status {status}"
    finally:
        VERBOSE = saved
    quiet, loud = text[False], text[True]
    if "FAIL  fake/bad" not in quiet or "the reason" not in quiet or "the output" not in quiet:
        return f"a failure was not shown in full: {quiet!r}"
    if "PASS" in quiet:
        return f"quiet mode listed a passing test: {quiet!r}"
    if "1/2 passed" not in quiet or "slowest:" not in quiet:
        return f"no summary: {quiet!r}"
    if "PASS  fake/good" not in loud:
        return f"verbose mode left out a passing test: {loud!r}"
    return None


def check_decisions_index():
    """docs/decisions-index.md is regenerated from decisions.md after every new decision."""
    result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "decisions-index.py"),
                             "--check"], capture_output=True, text=True)
    return None if result.returncode == 0 else result.stderr.strip()


def check_bringup_stages():
    """tools/bringup-stages.py: every stage names a test that exists and has expected output; the table it writes
    lists each stage with the serial output taken from the test's own header (decision 186)."""
    tool = os.path.join(ROOT, "tools", "bringup-stages.py")
    result = subprocess.run([sys.executable, tool, "--check"], capture_output=True, text=True)
    if result.returncode != 0:
        return "the stage list is broken: " + result.stdout.strip()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("bringup_stages", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.table()
    for number, (name, test, *_rest) in enumerate(module.STAGES, 1):
        if f"{number:02d}_{name}.img" not in text:
            return f"the table has no row for stage {number} ({name})"
    # a stage's output comes from its test: the demo's header says the rally ends 6 and 5
    lines, typed = module.expectations("rook/trusted/two_core_demo")
    if "out: 6" not in lines or "Raven: panic: domain 1 faulted" not in lines:
        return f"two_core_demo expectations read wrongly: {lines}"
    lines, typed = module.expectations("rook/separate_uart_driver")
    if typed != ["hello", "!llo", "world"]:
        return f"the typed input of separate_uart_driver read wrongly: {typed}"
    names = [stage[0] for stage in module.STAGES]
    for wanted in ("dma", "storm"):
        if wanted not in names:
            return f"the stage list has no {wanted} stage"
    return None


def check_retries():
    """`// retries: N` runs a test up to N times and passes if one run does; without it a failure is final."""
    class Fake(RookTest):
        results = []

        def run_once(self, machine):
            ok = self.results.pop(0)
            return ok, "ok" if ok else "boom", ""

    def made(retries, results):
        test = Fake("rook/x", "d", "x.img", [], retries=retries)
        test.results = list(results)
        return test.run_on("raspi3b")[0]

    if not made(3, [False, False, True]):
        return "a test that passes on its third run of 3 should pass"
    if made(3, [False, False, False]):
        return "a test that fails 3 runs of 3 should fail"
    if made(1, [False, True]):
        return "without `retries:` a failure is final"
    return None


def check_failures_open():
    """`expect-failure: msg xN+` accepts N or more of that failure, in order; `xN` and a plain line stay exact."""
    import tempfile
    def parse(header):
        with tempfile.NamedTemporaryFile("w", suffix=".rook", dir=os.path.join(ROOT, "build"), delete=False) as f:
            f.write("// d\n" + header + "\nfn test_main() {}\n")
        try:
            return RookTest.from_file(f.name)
        finally:
            os.unlink(f.name)
    open_test = parse("// expect-failure: boom at t.rook:3 x3+")
    exact = parse("// expect-failure: boom at t.rook:3 x3")
    if not open_test.failures_open or open_test.expect_failures != ["boom at t.rook:3"] * 3:
        return f"`x3+` read wrongly: {open_test.expect_failures!r} open={open_test.failures_open}"
    if exact.failures_open or exact.expect_failures != ["boom at t.rook:3"] * 3:
        return f"`x3` read wrongly: {exact.expect_failures!r} open={exact.failures_open}"
    return None


def check_panic_wildcard():
    """`at FILE.rook:*` matches any line in one of Raven's files, but never the wrong message or
    file, and never the test's own file, which keeps its exact line."""
    cases = [
        ("boom at devices.rook:*", "boom at devices.rook:58", "t.rook", True),
        ("boom at devices.rook:*", "boom at devices.rook:7", "t.rook", True),
        ("boom at devices.rook:*", "bang at devices.rook:58", "t.rook", False),
        ("boom at devices.rook:*", "boom at actors.rook:58", "t.rook", False),
        ("boom at devices.rook:*", "boom at devices.rook:", "t.rook", False),
        ("boom at t.rook:*", "boom at t.rook:5", "t.rook", False),
        ("boom at t.rook:5", "boom at t.rook:5", "t.rook", True),
        ("boom at t.rook:5", "boom at t.rook:6", "t.rook", False),
    ]
    for want, got, own, expected in cases:
        if panic_matches(want, got, own) != expected:
            return f"panic_matches({want!r}, {got!r}, {own!r}) should be {expected}"
    path = os.path.join(ROOT, "tests", "rook", "mmio_outside.rook")
    for what, panic, expected in (
            ("the right site", "register outside Mmio at devices.rook:*", True),
            ("another file", "register outside Mmio at actors.rook:*", False),
            ("the test's own file", "register outside Mmio at mmio_outside.rook:*", False)):
        test = RookTest.from_file(path)
        test.machines = [MACHINES[0]]
        test.expect_panic = panic
        if test.run()[0] != expected:
            return f"a wildcard for {what} should {'pass' if expected else 'fail'}"
    return None


def load_tool(name):
    """A script in tools/ as a module (its file name has a hyphen), without running it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), os.path.join(ROOT, "tools", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_parallel_run():
    """Tests run side by side still report in order, each result with its own test, and really
    overlap in time; error tests compiled in other processes keep their results straight."""
    def fake(name, ok, seconds):
        test = RookTest(name, "d", "img", [])
        test.run = lambda: (time.sleep(seconds), (ok, "reason " + name))[1]
        return test
    tests = [fake("rook/a", True, 0.6), fake("rook/b", False, 0.2), fake("rook/c", True, 0.4),
             fake("rook/d", False, 0.1)]
    start = time.monotonic()
    got = run_parallel_group(tests, 4)
    elapsed = time.monotonic() - start
    if [g[0] for g in got] != [True, False, True, False] or got[1][1] != "reason rook/b":
        return f"results out of order or mixed up: {got!r}"
    if elapsed > 1.0:
        return f"four tests of 0.6, 0.2, 0.4 and 0.1 s took {elapsed:.1f} s: not side by side"
    quitter = RookTest("rook/q", "d", "img", [])
    quitter.run = lambda: sys.exit("a tool gave up")
    if run_one(quitter)[0]:
        return "a test that calls sys.exit passed (it must fail, not end the run)"
    errors_dir = os.path.join(ROOT, "tests", "rook", "errors")
    good = ErrorTest.from_file(os.path.join(errors_dir, "assign_to_immutable.rook"))
    bad = ErrorTest.from_file(os.path.join(errors_dir, "assign_to_immutable.rook"))
    bad.line += 1
    got = run_parallel_group([good, bad, good, bad], 4)
    if [g[0] for g in got] != [True, False, True, False]:
        return f"error test results mixed up: {[g[0] for g in got]!r}"
    return None


def check_make_quick():
    """`make quick` runs the tests named in T, and by default every Rook and error test."""
    def command(*flags):
        out = subprocess.run(["make", "-n", *flags, "quick"], cwd=ROOT, capture_output=True,
                             text=True).stdout
        return [l for l in out.splitlines() if "run_tests.py" in l]
    if command("T=rook/restart_basic")[-1:] != ["python3 tests/run_tests.py rook/restart_basic"]:
        return f"T was not passed on: {command('T=rook/restart_basic')!r}"
    if command()[-1:] != ["python3 tests/run_tests.py rook/ error/"]:
        return f"the default subset is wrong: {command()!r}"
    return None


def check_new_error_test():
    """tools/new-error-test.py writes a failing placeholder first, shows the real error, and
    --accept turns it into a passing test whose line numbers did not move."""
    import tempfile
    tool = os.path.join(ROOT, "tools", "new-error-test.py")
    program = "fn f() {\n    x: u64 = 1\n    x = 2\n}\n"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "demo.rook")
        step1 = subprocess.run([sys.executable, tool, "demo", "A demo.", "--dir", tmp],
                               input=program, capture_output=True, text=True)
        if step1.returncode != 0 or "declare it with `mut`" not in step1.stdout or "^" not in step1.stdout:
            return f"step 1 did not show the error: {step1.stdout!r} {step1.stderr!r}"
        if "PLACEHOLDER" not in open(path).read() or ErrorTest.from_file(path).run()[0]:
            return "the placeholder should fail until it is accepted"
        step2 = subprocess.run([sys.executable, tool, "--accept", "demo", "--dir", tmp],
                               capture_output=True, text=True)
        test = ErrorTest.from_file(path)
        if step2.returncode != 0 or (test.line, test.col) != (6, 7) or "PLACEHOLDER" in open(path).read():
            return f"--accept did not fill the header: {step2.stdout!r} {step2.stderr!r}"
        if not test.run()[0]:
            return "the accepted test fails"
        clean = subprocess.run([sys.executable, tool, "clean", "No error.", "--dir", tmp],
                               input="fn f() {\n}\n", capture_output=True, text=True)
        if clean.returncode == 0 or "without an error" not in clean.stderr:
            return "a program that compiles should be refused"
    return None


def check_batch_and_body_tools():
    """The bug-check batch reads its list correctly, and pr-body fills every template section."""
    batch = load_tool("bug-check-batch")
    blocks = batch.parse("# c\n@@ First one: a/b.py\n- x = 1\n-     y = 2\n+ x = 2\n\n@@ Second: c.py\n- q\n+\n")
    want = [{"title": "First one", "path": "a/b.py", "old": "x = 1\n    y = 2", "new": "x = 2"},
            {"title": "Second", "path": "c.py", "old": "q", "new": ""}]
    if blocks != want:
        return f"mutation list misread: {blocks!r}"
    body = load_tool("pr-body")
    import argparse
    args = argparse.Namespace(summary="SUM \\x41 \\n", roadmap="ROAD", review="REV", design="", limits="LIM",
                              tests=None, checks="CHK", bugs=None, session=None)
    text = body.fill(open(body.TEMPLATE).read(), args)
    for piece in ("SUM \\x41 \\n", "ROAD", "REV", "LIM", "CHK", "No decision in this PR", "CI is green"):
        if piece not in text:
            return f"the PR body lacks {piece!r}"
    if "<!--" in text:
        return "a template comment was left in the body"
    return None


def check_selfhost_stage2():
    """Milestone 5e: the Rook compiler (build/compiler/asmdump.img) compiles its own source to assembly A,
    A is built into an image, and that image compiles the same source to B; A equals Fledgling's assembly
    and B equals A (tools/selfhost-stage2.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("selfhost_stage2", os.path.join(ROOT, "tools", "selfhost-stage2.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if board_mode():
        return None
    if not os.path.exists(os.path.join(ROOT, "build", "compiler", "asmdump.img")):
        return missing_image("build/compiler/asmdump.img")
    ok, message = module.stage2()
    return None if ok else message


def check_feed_does_not_block():
    """A program that never reads its input (here a test that only prints) given half a megabyte must
    not hang the harness: the input is typed from a thread, so the timeout is seen (a miscompiled
    compiler stops reading and used to block the whole run for good)."""
    image = os.path.join(ROOT, "build", "rook-tests", "actors_basic.img")
    if board_mode() or not os.path.exists(image):
        return None
    start = time.monotonic()
    boot(image, lambda lines: False, 5.0, MACHINES[0], os.path.join(LOG_DIR, "harness_feed.qemu"),
         [(b"x" * 4_000_000, "")])
    if time.monotonic() - start > 60:
        return "the harness waited for a program that does not read its input"
    return None


def check_rookc():
    """tools/rookc.py (decision 170): Raven and the test support files compile to exactly Fledgling's assembly,
    the same input with the same compiler is answered from build/cache/, and an error is one line and exit 1."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rookc", os.path.join(ROOT, "tools", "rookc.py"))
    rookc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rookc)
    image = os.path.join(ROOT, "build", "stage2", "asmdump.img")
    if board_mode():
        return None
    if not os.path.exists(image):
        return missing_image(image)
    files = [f for f in ROOK_LIB]
    text, _ = rookc.compile_files(files, TRUSTED_DIRS, image)
    again, cached = rookc.compile_files(files, TRUSTED_DIRS, image)
    stream = make_stream([(f, is_trusted(f, TRUSTED_DIRS), open(f).read()) for f in files])
    if text != dump_asm(stream):
        return "rookc's assembly differs from Fledgling's"
    if not cached or again != text:
        return "the second compile of the same input was not answered from the cache"
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "bad.rook")
        with open(bad, "w") as f:
            f.write("fn test_main(x: u64) {\n    y: u64 = z\n}\n")
        out = os.path.join(tmp, "out.s")
        done = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "rookc.py"), bad, "-o", out,
                               "--image", image], capture_output=True, text=True)
        if done.returncode != 1 or not done.stderr.startswith("error ") or os.path.exists(out):
            return f"a compile error should print one error line and exit 1: {done.returncode} {done.stderr!r}"
    for name in os.listdir(rookc.CACHE):
        if open(os.path.join(rookc.CACHE, name)).read(8).startswith("error "):
            return "a compile error was kept in the cache"
    return None


def check_report_copies():
    """`--report-copies` (decision 173) lists each send to a `separate` actor with the size of its message
    (8 bytes for the handler number and 8 for each word), and nothing for a send inside a domain."""
    from fledgling.dump import report_copies
    source = ("separate actor Dev {\n    lossy on ping() {\n    }\n\n    lossy on add(a: u64, b: u64) {\n    }\n}\n\n"
              "actor Near {\n    lossy on ping() {\n    }\n}\n\n"
              "fn start(d: Dev, n: Near) {\n    d.ping()\n    d.add(1, 2)\n    n.ping()\n}\n")
    got = report_copies(make_stream([("a.rook", False, source)]))
    want = ("a.rook:15:7 send ping to Dev (separate): copies 8 bytes\n"
            "a.rook:16:7 send add to Dev (separate): copies 24 bytes\n")
    if got != want:
        return f"report_copies gave {got!r}, wanted {want!r}"
    if report_copies(make_stream([("a.rook", False, "fn f() {\n}\n")])) != "":
        return "a program without separate actors should report nothing"
    return None


def check_domain_budget_stress():
    """Two races no single run shows, so the same image is booted eight at a time, twice, and every run must finish and
    print what it should. A domain handler's budget runs out at a random moment: in the domain, in Raven serving it,
    or in the few instructions between loading its registers and the eret (the bug of decision 179: an interrupt there
    overwrote ELR_EL1 and SPSR_EL1, and about one run in five hung). And a message from one core can reach an actor on
    another before that actor's `new` has run, because two rings are not ordered against each other (decision 181):
    the handler ran on zeroed fields and `new` then erased its work."""
    if board_mode():
        return None
    cases = [("separate_spins_in_calls", ["Raven: boot complete, idling", "out: 5",
                                          "Raven: panic: domain 1 ran for over 100 ms"]),
             ("separate_cross_domain", ["Raven: boot complete, idling", "out: 11", "out: 1", "out: 3",
                                        "Raven: panic: pong crash at separate_cross_domain.rook:36"])]
    for name, wanted in cases:
        image = os.path.join(ROOT, "build", "rook-tests", "trusted", name + ".img")
        if not os.path.exists(image):
            return missing_image(image)

        def one(n):
            output, timed_out, qemu_log = boot(image, lambda lines: "Raven: boot complete, idling" in lines, 40.0,
                                               MACHINES[0], os.path.join(LOG_DIR, f"harness_stress_{name}_{n}.qemu"))
            lines = complete_lines(output)
            return all(want in lines for want in wanted)

        for round_number in range(2):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(one, range(8 * round_number, 8 * round_number + 8)))
            if not all(results):
                return f"{name}: {results.count(False)} of 8 runs did not print what they should"
    return None


def check_hosted_expectations():
    """A hosted test fails when the output differs, when the program never writes the
    `Hosted:` lines, and when it takes too long."""
    test = HostedTest.from_file(os.path.join(ROOT, "tests", "hosted", "numberer", "basic.in"))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        wrong = os.path.join(tmp, "wrong.out")
        for what, text in (("a different line", "1: first line\n2: SECOND line\n3: \n4: fourth after a blank\n"),
                           ("a missing line", "1: first line\n2: second line\n3: \n"),
                           ("an extra line", "1: first line\n2: second line\n3: \n4: fourth after a blank\n5: x\n")):
            open(wrong, "w").write(text)
            bad = dataclasses.replace(test, expected_path=wrong)
            if bad.run_on(MACHINES[0])[0]:
                return f"passed with {what}"
    silent = dataclasses.replace(test, image="build/rook-tests/actors_basic.img", timeout=2.0)
    if silent.run_on(MACHINES[0])[0]:
        return "passed for a program that wrote no `Hosted:` output"
    return None


def check_dump_tokens():
    """Fledgling's token dump is the format the self-hosted lexer must match: a line a token, with
    the text, the value of a number or character, and the bytes of a string in hex; and one line
    for an error."""
    got = dump_tokens("x: u8 = 0x1F\nc = 'a'\ns = \"h\\x41\"\n")
    want = ("1:1 ident x\n1:2 : :\n1:4 ident u8\n1:7 = =\n1:9 int 0x1F 31\n1:13 newline \\n\n"
            "2:1 ident c\n2:3 = =\n2:5 char 'a' 97\n2:8 newline \\n\n"
            "3:1 ident s\n3:3 = =\n3:5 str \"h\\x41\" x6841\n3:12 newline \\n\n4:1 eof\n")
    if got != want:
        return f"the dump is not as documented: {got!r}"
    if dump_tokens("x\ty\n") != "error 1:2: tabs aren't allowed; indent with spaces\n":
        return "a lexer error should be one `error LINE:COL: MESSAGE` line"
    return None


def check_dump_tree():
    """Fledgling's tree dump is the format the self-hosted parser must match: a node a line, two
    spaces of indent a level, kind then place then text; a part that is not there is `none`; and
    one line for an error. Worked out by hand from the grammar."""
    got = dump_tree("fn f(a: u64) -> u64 {\n    return -a\n}\n")
    want = ("Program\n  Fn 1:1 f\n    Params\n      Param 1:6 a\n        Type 1:9 u64\n    Type 1:17 u64\n"
            "    Block 1:21\n      Return 2:5\n        Unary 2:12 -\n          Name 2:13 a\n")
    if got != want:
        return f"the dump is not as documented: {got!r}"
    if dump_tree("fn f() {\n}\n}\n") != ("error 3:1: expected `fn`, `extern fn`, `struct`, `enum`, `actor`, "
                                          "`interface` or a constant at top level, found `}`\n"):
        return "a parse error should be one `error LINE:COL: MESSAGE` line"
    if dump_tree("x\ty\n") != "error 1:2: tabs aren't allowed; indent with spaces\n":
        return "a lexer error should be the same one line"
    if not dump_tree("pub separate actor A {\n}\n").startswith("Program\n  Actor 1:14 pub separate A\n"):
        return "a `separate` actor should say so in its words, after `pub` ('actor' is at column 14)"
    try:
        tree(object())
    except AssertionError:
        return None
    return "a node the dump has no form for should be an error, not silence"


def check_dump_expanded():
    """Fledgling's expanded-program dump is the format the self-hosted generics pass must match:
    files in a stream, the template gone, its instance last with the place of the type argument in
    the file it was written in, and `VisibleIn`, `GenericBase` and `File` lines. Worked out by hand."""
    stream = make_stream([("a.rook", True, "struct Box<T> {\n    x: T\n}\n"),
                          ("b.rook", False, "fn f(p: Box<u8>) {\n}\n")])
    want = ("Program\n  Fn 1:1 f\n    Params\n      Param 1:6 p\n        Type 1:9 Box<u8>\n"
            "          VisibleIn b.rook\n    none\n    Block 1:18\n    File b.rook\n"
            "  Struct 1:1 Box<u8>\n    TypeParams\n    Fields\n      Field 2:5 x\n"
            "        Type b.rook:1:13 u8\n          VisibleIn b.rook\n    Methods\n    GenericBase Box\n"
            "    File trusted a.rook\n")
    got = dump_expanded(stream)
    if got != want:
        return f"the dump is not as documented: {got!r}"
    if dump_expanded("@@@ a.rook\nstruct B<T> {\n}\nfn f(x: B) {\n}\n") != (
            "error a.rook:3:9: `B` needs type arguments: write `B<T>` with the types\n"):
        return "an error should be one `error FILE:LINE:COL: MESSAGE` line"
    return None


def check_utf8_split():
    """A character that arrives in two reads is still one character: the serial output of a busy
    machine is cut anywhere (this made hosted/lexdump/non_ascii fail in CI)."""
    reader, writer = os.pipe()
    sel = selectors.DefaultSelector()
    sel.register(reader, selectors.EVENT_READ)
    try:
        os.write(writer, b"h\xc3")
        first = read_available(sel, 0.05)
        os.write(writer, b"\xa9llo")
        second = read_available(sel, 0.05)
    finally:
        sel.close()
        os.close(reader)
        os.close(writer)
    if first + second != "h\u00e9llo":
        return f"a character split between two reads came out as {first + second!r}"
    return None


def check_dump_declarations():
    """Fledgling's declarations dump is the format the self-hosted checker must match: sizes, field
    and method lines, an actor's fields with their flags, handlers with their numbers, signatures.
    Worked out by hand."""
    stream = make_stream([("a.rook", False, "struct Pt {\n    x: u64\n    pub y: bool\n}\n"
                                            "enum Op {\n    Nop\n    Mov(from: Pt)\n}\n"
                                            "interface Go {\n    lossy on go(n: u8)\n}\n"
                                            "actor Bot: Go {\n    mut hits: u64\n    new() {\n        .hits = 0\n    }\n    lossy on go(n: u8) {\n    }\n}\n"
                                            "fn at(p: mut Pt) -> Op {\n    return Nop\n}\n")])
    want = ("generics \ngeneric-variants \nhandler-numbers go=0\n"
            "struct Pt words=2\n  field x u64\n  field y bool pub\n"
            "enum Op words=3\n  variant Nop tag=0\n  variant Mov tag=1\n    field from Pt\n"
            "actor Bot words=1\n  field hits u64 mut\n  new ()\n  handler go lossy (u8) number=0\n  implements Go\n"
            "interface Go\n  handler go lossy (u8) number=0\n"
            "fn at (mut Pt) -> Op\n")
    got = dump_declarations(stream)
    if got != want:
        return f"the dump is not as documented: {got!r}"
    if dump_declarations("@@@ a.rook\nstruct Bad {\n}\n") != "error a.rook:1:1: `Bad` has no fields\n":
        return "an error should be one `error FILE:LINE:COL: MESSAGE` line"
    return None


def check_dump_bodies():
    """Fledgling's bodies dump (decision 165) is the format the self-hosted checker must match: the
    constants, then the checked tree of the last file with `: TYPE` and the slots and values the
    checker recorded. Worked out by hand: a constant `3 * 4` is 12; in `f`, the struct literal is built
    in the frame words 0-1 (`temp=0`), `a` takes the two words 2-3, `b` the words 4-5, `c` the word 6,
    so `nslots=7`. The switches: with ownership off `a` may be read after `consume(a)`, with it on that
    is the error; with no bodies a function has no types or slots."""
    src = ("LIMIT: u64 = 3 * 4\n"
           "struct Reading {\n    temp: i32\n    seq: u32\n}\n"
           "fn f() {\n    a: Reading = Reading(temp: 1, seq: 1)\n    b: Reading = consume(a)\n"
           "    c: i32 = a.temp\n}\n")
    stream = make_stream([("a.rook", False, src)])
    want_head = ("const LIMIT u64 12\nProgram\n  Const 1:1 LIMIT\n    Type 1:8 u64\n"
                 "    Binary 1:16 * : u64\n      Int 1:14 3 : u64\n      Int 1:18 4 : u64\n    File a.rook\n")
    want_fn = ("  Fn 6:1 f nslots=7\n    Params\n    none\n    Block 6:8\n"
               "      Decl 7:5 a slot=2\n        Type 7:8 Reading\n"
               "        StructLit 7:18 Reading : Reading temp=0\n"
               "          FieldInit 7:26 temp\n            Int 7:32 1 : i32\n"
               "          FieldInit 7:35 seq\n            Int 7:40 1 : u32\n"
               "      Decl 8:5 b slot=4\n        Type 8:8 Reading\n"
               "        Consume 8:18 : Reading\n          Name 8:26 a : Reading slot=2\n"
               "      Decl 9:5 c slot=6\n        Type 9:8 i32\n"
               "        Access 9:16 temp : i32 struct=Reading\n          Name 9:14 a : Reading slot=2\n"
               "    File a.rook\n")
    got = dump_bodies(stream)
    if want_head not in got or not got.endswith(want_fn):
        return f"the dump is not as documented: {got!r}"
    if dump_bodies(stream, ownership=True) != "error a.rook:9:14: `a` was consumed at 8:18, so it has no value here; assign it again first\n":
        return "with ownership on, reading `a` after `consume(a)` should be the error"
    # The other ownership checks, on the error tests that need nothing but themselves: a lock held by a
    # `match` and a value lent twice. Both are errors with ownership on, and accepted with it off.
    for name in ("match_lock", "lend_twice"):
        text = open(os.path.join(ROOT, "tests", "rook", "errors", name + ".rook")).read()
        stream_n = make_stream([("a.rook", False, text)])
        if not dump_bodies(stream_n, ownership=True).startswith("error a.rook:"):
            return f"{name}: with ownership on this should be an error"
        if dump_bodies(stream_n).startswith("error"):
            return f"{name}: with ownership off this should be accepted"
    # An actor's `new` and handlers are checked only with `actors`: the type error in the handler shows then.
    actor = ("actor Bot {\n    mut n: u64\n    new() {\n        .n = 0\n    }\n"
             "    lossy on go() {\n        .n = true\n    }\n}\n")
    stream_a = make_stream([("a.rook", False, actor)])
    if dump_bodies(stream_a).startswith("error"):
        return "an actor's handlers are not checked unless asked"
    if dump_bodies(stream_a, actors=True) != "error a.rook:7:14: expected u64, found bool\n":
        return "with actors on, the handler's type error should be reported: " + dump_bodies(stream_a, actors=True)
    boxed = ("struct Box<T> {\n    value: T\n    fn get() -> T {\n        return copy(.value)\n    }\n}\n"
             "fn f() -> u64 {\n    b: Box<u64> = Box(value: 1)\n    return b.get()\n}\n")
    if "Copy 4:16 : u64 scalar" not in dump_bodies(make_stream([("a.rook", False, boxed)])):
        return "copying a number inside a generic type is noted as `scalar`"
    unchecked = dump_bodies(make_stream([("a.rook", False, "fn f() {\n    c: u64 = 1\n}\n")]), bodies=False)
    if unchecked != ("Program\n  Fn 1:1 f\n    Params\n    none\n    Block 1:8\n      Decl 2:5 c\n"
                     "        Type 2:8 u64\n        Int 2:14 1\n    File a.rook\n"):
        return f"with no bodies, nothing in a function is checked, so it has no types: {unchecked!r}"
    return None


def check_timeout_header():
    """A `// timeout: N` header gives a Rook test N seconds (10 without it)."""
    import tempfile
    for text, want in (("// A test.\n// expect: 1\n// timeout: 60\n", 60.0), ("// A test.\n// expect: 1\n", 10.0)):
        with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "build")) as tmp:
            path = os.path.join(tmp, "t.rook")
            open(path, "w").write(text + "fn test_main() {\n}\n")
            got = RookTest.from_file(path).timeout
            if got != want:
                return f"the timeout should be {want}, not {got}"
    return None


HARNESS_TESTS = [
    HarnessTest("harness/utf8_split", "Serial output cut inside a character still decodes",
                check_utf8_split),
    HarnessTest("harness/dump_declarations", "Fledgling's declarations dump has the documented format",
                check_dump_declarations),
    HarnessTest("harness/timeout_header", "A Rook test's timeout header sets its time limit",
                check_timeout_header),
    HarnessTest("harness/dump_bodies", "Fledgling's bodies dump has the documented format and switches",
                check_dump_bodies),
    HarnessTest("harness/dump_expanded", "Fledgling's expanded-program dump has the documented format",
                check_dump_expanded),
    HarnessTest("harness/dump_tree", "Fledgling's tree dump has the documented format", check_dump_tree),
    HarnessTest("harness/dump_tokens", "Fledgling's token dump has the documented format", check_dump_tokens),
    HarnessTest("selfhost/stage2", "The Rook compiler compiles itself, and the image made from that assembly "
                "compiles it to the same assembly", check_selfhost_stage2),
    HarnessTest("harness/feed_does_not_block", "Typing input into a program that does not read it "
                "does not hang the harness", check_feed_does_not_block),
    HarnessTest("harness/rookc", "The Rook compiler as a command: Fledgling's assembly, a cache, one-line errors",
                check_rookc),
    HarnessTest("harness/report_copies", "--report-copies lists the sends that cross a domain, with their size",
                check_report_copies),
    HarnessTest("harness/domain_budget_stress", "A domain handler's budget runs out at any moment without hanging "
                "Raven (16 runs, 8 at a time)", check_domain_budget_stress),
    HarnessTest("harness/hosted_expectations", "A hosted test fails on a different, missing or "
                "extra output line, and on a program that writes none", check_hosted_expectations),
    HarnessTest("harness/parallel_run", "Tests run side by side report in order and overlap in "
                "time", check_parallel_run),
    HarnessTest("harness/make_quick", "make quick runs the tests named in T, or every Rook and "
                "error test", check_make_quick),
    HarnessTest("harness/new_error_test", "A new error test starts as a failing placeholder and "
                "--accept fills in the real error", check_new_error_test),
    HarnessTest("harness/tools_batch_body", "The bug-check list and the PR body are built "
                "correctly", check_batch_and_body_tools),
    HarnessTest("harness/bringup_stages", "The first-boot stage list names real tests and takes the expected "
                "serial output from their headers", check_bringup_stages),
    HarnessTest("harness/retries", "A test with `// retries: N` passes if any of N runs does; "
                "without it a failure is final", check_retries),
    HarnessTest("harness/failures_open", "`expect-failure: msg xN+` reads as N or more, `xN` as exactly N",
                check_failures_open),
    HarnessTest("harness/panic_wildcard", "A panic site in Raven's own files may be a wildcard "
                "line; the test's own file keeps its exact line", check_panic_wildcard),
    HarnessTest("harness/decisions_index", "The decision index matches decisions.md",
                check_decisions_index),
    HarnessTest("harness/quiet_output", "A run shows failures in full and a summary; -v lists "
                "every test", check_quiet_output),
    HarnessTest("harness/qemu_log_check", "QEMU's device log must match what a test expects, "
                "exactly", check_log_cases),
    HarnessTest("harness/machines", "Tests run on a 1 GB and a 512 MB Raspberry Pi machine",
                check_machines),
    HarnessTest("harness/wrong_expectations", "A Rook test with a wrong fault or QEMU-log "
                "expectation fails", check_wrong_expectations_fail),
    HarnessTest("harness/echo_expectations", "A Rook test that expects an echo fails unless the "
                "typed line comes back", check_echo_expectations),
    HarnessTest("harness/suggest_imports", "The compiler can list the imports a file lacks",
                check_suggest_imports),
    HarnessTest("harness/public_surface", "The names application code can reach in Raven "
                "are the ones listed, and a new one is looked at first", check_public_surface),
    HarnessTest("harness/failure_expectations", "A test that expects contained failures fails "
                "when the failures differ", check_failure_expectations),
    HarnessTest("harness/line_count", "A test that expects a line a number of times fails when "
                "it was written another number of times", check_line_counts),
    HarnessTest("harness/import_ambiguous", "Two files of one name can't be imported",
                check_import_ambiguous),
    HarnessTest("harness/idle_sleeps", "A core with nothing to run sleeps instead of spinning",
                check_idle_sleeps),
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
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "hosted", "*", "*.in"))):
        tests.append(HostedTest.from_file(path))
    # the smallest compiler program, built by the Rook compiler (make rook-compilers), runs as the other one does
    numberer = HostedTest.from_file(os.path.join(ROOT, "tests", "hosted", "numberer", "basic.in"))
    tests.append(dataclasses.replace(numberer, name="hosted/numberer/rook-built",
                                     image="build/rook/compiler/numberer.img"))
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "hosted", "*", "live.txt"))):
        tests.extend(HostedTest.live_files(os.path.basename(os.path.dirname(path)), path))
    return tests


def main(argv):
    tests = all_tests()
    global VERBOSE, DEFAULT_JOBS
    if "-v" in argv:
        argv = [a for a in argv if a != "-v"]
        VERBOSE = True
    if "-j" in argv:
        at = argv.index("-j")
        os.environ["JOBS"] = argv[at + 1]
        argv = argv[:at] + argv[at + 2:]
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


# Quiet by default (a passing run is a summary, not 500 lines); -v or VERBOSE=1
# prints a line per test. A failure always prints in full.
VERBOSE = bool(os.environ.get("VERBOSE"))
# More jobs than cores is faster: a QEMU boot mostly waits. 3 x 8 runs of the suite passed.
DEFAULT_JOBS = min(8, 2 * (os.cpu_count() or 1))


def run_one(test):
    """Run one test: (passed, reason, output, seconds). A crash fails this test only."""
    start = time.monotonic()
    try:
        result = test.run()
    except (Exception, SystemExit):
        # A crash inside the compiler (or a tool that exits) fails this test only; the
        # rest of the suite still runs.
        result = (False, "compiler crashed", traceback.format_exc())
    return result[0], result[1], (result[2] if len(result) > 2 else ""), time.monotonic() - start


def run_parallel_group(tests, jobs):
    """Results for `tests` in order, running up to `jobs` at once.

    Rook program tests run side by side (threads): each is one QEMU boot on prebuilt
    images with its own log file. Error tests only compile, so they run in processes.
    Boot and harness tests measure timing or CPU use, so they run one at a time, first."""
    kinds = {"rook": [], "error": [], "alone": []}
    for i, test in enumerate(tests):
        kind = "rook" if isinstance(test, (RookTest, HostedTest)) else "error" if isinstance(test, ErrorTest) else "alone"
        kinds[kind].append((i, test))
    results = [None] * len(tests)
    for i, test in kinds["alone"]:
        results[i] = run_one(test)
    for kind, pool_class in (("error", concurrent.futures.ProcessPoolExecutor),
                             ("rook", concurrent.futures.ThreadPoolExecutor)):
        group = kinds[kind]
        if jobs > 1 and len(group) > 1:
            # Compiling is CPU-bound Python, so error tests need processes, not threads.
            with pool_class(max_workers=jobs) as pool:
                outcomes = pool.map(run_one, [t for _, t in group], chunksize=16 if kind == "error" else 1)
                for (i, _), result in zip(group, outcomes):
                    results[i] = result
        else:
            for i, test in group:
                results[i] = run_one(test)
    return results


def run_all(selected, jobs=None):
    """Run `selected` tests and return the exit status.

    Prints only failures and a summary (with the slowest tests), or one line per
    test with -v / VERBOSE=1. Rook program tests run `jobs` at a time (default JOBS
    or DEFAULT_JOBS; -j N); everything is reported in the order of `selected`."""
    os.makedirs(LOG_DIR, exist_ok=True)
    jobs = jobs or int(os.environ.get("JOBS", DEFAULT_JOBS))
    failures = 0
    times = []
    width = max(len(t.name) for t in selected)
    for test, (passed, reason, output, elapsed) in zip(selected, run_parallel_group(selected, jobs)):
        times.append((elapsed, test.name))
        if VERBOSE or not passed:
            print(f"{'PASS' if passed else 'FAIL'}  {test.name:<{width}}  {elapsed:4.1f}s  {test.description}")
        if not passed:
            failures += 1
            print(f"      reason: {reason}")
            if output:
                print("      output:")
                for line in output.replace("\r", "").splitlines():
                    print(f"        | {line}")

    if not VERBOSE:
        slow = ", ".join(f"{n} {t:.1f}s" for t, n in sorted(times, reverse=True)[:5])
        print(f"slowest: {slow}")
    print(f"\n{len(selected) - failures}/{len(selected)} passed (logs in build/test-logs/)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
