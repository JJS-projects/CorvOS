#!/usr/bin/env python3
"""Stage images for the first boot on a board: the same programs the tests run, in the order that finds a wrong
assumption of docs/hardware.md soonest.

    tools/bringup-stages.py --check     every stage's test exists and has the expected output the table below needs
    tools/bringup-stages.py             copy each stage's image (built by `make test` or `make quick T=...`) to
                                        build/sdcard/stages/NN_name.img and write build/sdcard/stages/STAGES.md

On the SD card the stage's image is named kernel8.img (one stage at a time); STAGES.md says what the serial port
should print (taken from the test's own `// expect` lines, so it cannot drift from what QEMU does), which
hardware.md items the stage tests, and what to do when it differs. Nothing here has run on a board.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (name, test, what the stage shows, hardware.md items it depends on first, if it fails look at)
STAGES = [
    ("hello", "boot", "Raven starts, the console works", "1-16", "the LED code in hardware.md; serial wiring and baud (7, 8, 16)"),
    ("actors", "rook/actors_basic", "actors and messages on core 0 with the MMU and caches on", "20", "item 20 first: SCTLR_VALUE in mmu.s, caches off"),
    ("echo_irq", "rook/echo_interrupt", "a UART receive interrupt reaches an actor; type a line and it comes back", "17-19", "items 18 and 19 (timer and interrupt controller routing)"),
    ("cores", "rook/trusted/cores_online", "cores 1 to 3 come up", "22", "item 22: the spin table addresses; SMPEN"),
    ("el0", "rook/trusted/domain_returns", "code runs at EL0 in a domain and comes back", "21", "item 21: tlbi and ic around the TTBR0 writes"),
    ("fault", "rook/trusted/domain_fault_other_domain", "a domain touching another's memory faults and is reported", "21", "item 21 again; the page tables"),
    ("separate", "rook/separate_basic", "a compiled separate actor runs in its own domain", "21", "item 21"),
    ("rings", "rook/separate_cross_core_order", "messages between cores arrive in order", "23", "item 23: cache coherence between cores (SMPEN, item 22)"),
    ("two_domains", "rook/separate_two_domains", "two domains on two cores keep their own data", "21, 23", "items 21 and 23"),
    ("demo6", "rook/trusted/two_core_demo", "milestone 6: the two-core demo, a failure on one core leaves the other alone", "20-23", "docs/milestone6.md"),
    ("device", "rook/separate_device", "a domain reads a device's registers (the UART's ID registers)", "24", "item 24: Device attributes at EL0"),
    ("irq_domain", "rook/separate_interrupt", "the UART interrupt reaches a driver in a domain on another core; type a line", "25", "item 25: the local mailbox interrupt and the timer"),
    ("driver", "rook/separate_uart_driver", "milestone 7: the UART driver in a domain, killed mid-use; type hello, then !llo (the driver crashes and restarts), then world", "24, 25", "decision 184"),
]


def source_of(test):
    if test == "boot":
        return None
    return os.path.join(ROOT, "tests", test + ".rook")


def image_of(test):
    if test == "boot":
        return os.path.join(ROOT, "build", "kernel8.img")
    return os.path.join(ROOT, "build", "rook-tests", test[len("rook/"):] + ".img")


def expectations(test):
    """What the serial port should show, from the test's header: (lines, typed input)."""
    if test == "boot":
        return ["Raven: hello from CorvOS", "... (the boot lines)", "Raven: boot complete, idling"], []
    lines, typed = [], []
    for raw in open(source_of(test)):
        if not raw.startswith("//"):
            break
        text = raw[2:].strip()
        if text.startswith("expect:"):
            lines.append("out: " + text[len("expect:"):].strip())
        elif text.startswith("expect-failure:"):
            lines.append("Raven: panic: " + text[len("expect-failure:"):].strip())
        elif text.startswith("expect-echo:"):
            lines.append("(echoed back) " + text[len("expect-echo:"):].strip())
        elif text.startswith("input:"):
            typed.append(text[len("input:"):].strip())
    return lines, typed


def check():
    problems = []
    for name, test, *_ in STAGES:
        if test != "boot" and not os.path.exists(source_of(test)):
            problems.append(f"{name}: no test {test}")
            continue
        lines, typed = expectations(test)
        if not lines:
            problems.append(f"{name}: {test} has no `// expect` lines to show")
    return problems


def table():
    out = ["# Stages for the first boot", "",
           "Nothing here has run on a board. One stage at a time: copy `NN_name.img` to the card as `kernel8.img` "
           "(with `config.txt`, see tools/sdcard/README.md), power on, compare the serial output. Stop at the first stage that differs: "
           "its row says which items of docs/hardware.md to doubt. A stage may pass although an earlier one was skipped; "
           "the order is the order in which a wrong assumption shows soonest.", "",
           "| # | Image | Shows | hardware.md items | If it differs | Serial output |", "|---|---|---|---|---|---|"]
    for n, (name, test, shows, items, look) in enumerate(STAGES, 1):
        lines, typed = expectations(test)
        shown = "<br>".join(f"`{l}`" for l in lines)
        if typed:
            shown = "type " + " then ".join(f"`{t}`" for t in typed) + " (Enter after each); the program prints:<br>" + shown
        out.append(f"| {n} | `{n:02d}_{name}.img` | {shows} | {items} | {look} | {shown} |")
    return "\n".join(out) + "\n"


def main(argv):
    if argv == ["--check"]:
        problems = check()
        for p in problems:
            print(p)
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    problems = check()
    if problems:
        print("\n".join(problems))
        return 1
    dest = os.path.join(ROOT, "build", "sdcard", "stages")
    os.makedirs(dest, exist_ok=True)
    missing = []
    for n, (name, test, *_) in enumerate(STAGES, 1):
        image = image_of(test)
        if os.path.exists(image):
            shutil.copy(image, os.path.join(dest, f"{n:02d}_{name}.img"))
        else:
            missing.append(image)
    with open(os.path.join(dest, "STAGES.md"), "w") as f:
        f.write(table())
    print(f"wrote {len(STAGES) - len(missing)} images and STAGES.md in build/sdcard/stages/")
    if missing:
        print("not built (run `make test`):", *missing, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
