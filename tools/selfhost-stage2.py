#!/usr/bin/env python3
"""Stage 5e: the Rook compiler compiles itself, twice, with identical output (docs/self-hosting.md).

    tools/selfhost-stage2.py [--program NAME] [--skip-fledgling]

The input is the compiler program NAME (default asmdump) as the tests compile it: Raven's files, the test
support files, compiler/lib/*.rook and compiler/NAME.rook.
  1. build/compiler/NAME.img (built by Fledgling) compiles that input: assembly A.
  2. A is assembled and linked into build/stage2/NAME.img (`make`; the same assembler, linker and kernel objects).
  3. That image compiles the same input: assembly B.
  A and B must be identical. Unless --skip-fledgling, A is also compared with `python3 -m fledgling --dump-asm`
  (stage 5d-4's proof, repeated on this input). Exits 1 on any difference. Slow (minutes per QEMU run); not in
  `make test`.
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import run_tests  # noqa: E402


def program_files(name):
    """The files of compiler/NAME.rook as a stream stage compiles them (as tools/selfhost-check.py does)."""
    lib = run_tests.ROOK_LIB + sorted(glob.glob(os.path.join(ROOT, "compiler", "lib", "*.rook")))
    path = os.path.join(ROOT, "compiler", name + ".rook")
    return tuple(os.path.relpath(f, ROOT) for f in lib + [path])


def run_image(image, stream, label, timeout):
    """Boot `image` on the first machine, type `stream`, return the lines between Hosted: begin and end."""
    data = stream.encode() + run_tests.END_OF_INPUT
    os.makedirs(run_tests.LOG_DIR, exist_ok=True)
    machine = run_tests.MACHINES[0]
    output, timed_out, qemu_log = run_tests.boot(
        os.path.join(ROOT, image), lambda lines: run_tests.HOSTED_END in lines, timeout, machine,
        run_tests.log_base(label, machine) + ".qemu", [(data, "")])
    run_tests.save_log(label, machine, output, qemu_log)
    lines = run_tests.complete_lines(output)
    if run_tests.HOSTED_BEGIN not in lines or run_tests.HOSTED_END not in lines:
        sys.exit(f"{label}: no Hosted: begin/end output" + (" before the timeout" if timed_out else "") +
                 "\n" + "\n".join(lines[-5:]))
    return lines[lines.index(run_tests.HOSTED_BEGIN) + 1:lines.index(run_tests.HOSTED_END)]


def first_difference(a, b):
    for i, (x, y) in enumerate(zip(a + [None] * len(b), b + [None] * len(a))):
        if x != y:
            return f"line {i + 1}: {x!r} vs {y!r}"
    return None


def main(argv):
    name = argv[argv.index("--program") + 1] if "--program" in argv else "asmdump"
    names = program_files(name)
    files = [(rel, run_tests.is_trusted(os.path.join(ROOT, rel), run_tests.TRUSTED_DIRS),
              open(os.path.join(ROOT, rel)).read()) for rel in names]
    stream = run_tests.make_stream(files)
    timeout = float(os.environ.get("STAGE2_TIMEOUT", 3600))
    os.makedirs(os.path.join(ROOT, "build", "stage2"), exist_ok=True)

    print(f"stage 1: build/compiler/{name}.img compiles {len(names)} files ({len(stream)} bytes)", flush=True)
    a = run_image(f"build/compiler/{name}.img", stream, "stage2_A", timeout)
    if len(a) == 1 and a[0].startswith("error "):
        sys.exit("stage 1 stopped: " + a[0])
    with open(os.path.join(ROOT, "build", "stage2", name + ".s"), "w") as f:
        f.write("\n".join(a) + "\n")
    print(f"  assembly A: {len(a)} lines", flush=True)

    if "--skip-fledgling" not in argv:
        d = first_difference(a, run_tests.dump_asm(stream).splitlines())
        print("  A equals Fledgling's assembly" if d is None else f"  A differs from Fledgling: {d}", flush=True)
        if d:
            return 1

    subprocess.run(["make", "-s", f"build/stage2/{name}.img"], cwd=ROOT, check=True)
    print(f"stage 2: build/stage2/{name}.img compiles the same input", flush=True)
    b = run_image(f"build/stage2/{name}.img", stream, "stage2_B", timeout)
    with open(os.path.join(ROOT, "build", "stage2", name + ".B.s"), "w") as f:
        f.write("\n".join(b) + "\n")
    print(f"  assembly B: {len(b)} lines", flush=True)
    d = first_difference(a, b)
    if d:
        print(f"A and B DIFFER at {d}")
        return 1
    print("A and B are identical")
    return 0


sys.exit(main(sys.argv[1:]))
