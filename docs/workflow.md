# Development workflow

CorvOS is built by Claude and reviewed by the project owner. This document
describes how work arrives, how to check it, and how it gets into `main`.

## The short version

1. Each piece of work arrives as a **pull request** from its own branch.
2. **CI** builds the kernel and boots it in QEMU on every push. A red CI is never
   ready for review.
3. The PR description says **what to look at first** and includes test output.
4. You review on GitHub: comment, request changes, or approve.
5. **You merge.** Nothing reaches `main` without your merge.

## How work is split

- Roughly one PR per milestone step, small enough to review in one sitting.
  Large milestones are split into several PRs.
- Design changes go into `docs/` in the same PR as the code, with a new entry in
  [decisions.md](decisions.md) for anything decided.
- Branch names: `m<milestone>-<topic>`, e.g. `m1-boot`, `m2-lexer`.

## Checking a PR

You don't need to run anything locally; CI runs the full test suite. If you want
to try it yourself, there are three ways, from easiest:

| Where | How |
|---|---|
| **GitHub only** | Look at the CI run on the PR. The `corvos-<sha>` artifact has the kernel image and the full QEMU output of every test. |
| **Codespace** | On the PR, *Code → Codespaces → Create codespace on this branch*. The dev container installs everything and runs the tests. Then `make run`. |
| **Your machine** | Install `make`, `python3`, AArch64 binutils and `qemu-system-aarch64` (on Ubuntu: `tools/install-deps.sh`; on macOS: `brew install aarch64-elf-binutils qemu`, then `make CROSS=aarch64-elf-`). |

### Commands

| Command | What it does |
|---|---|
| `make` | Build `build/kernel8.img` |
| `make test` | Build and run every QEMU test |
| `make difftest` | Random integer arithmetic, checked against a Python model (`SEED=n PROGRAMS=n`; see decision 106) |
| `make coverage` | Fail if a compiler line is reached by no test, except those listed in `tools/coverage-gaps.txt` (Python's `trace`, no extra install) |
| `make run` | Boot the kernel in QEMU with the UART on your terminal (quit: Ctrl-A then X) |
| `make disasm` | Disassemble the kernel |
| `make sdcard` | Collect files for a real Pi Zero 2 W (see `tools/sdcard/`) |

## Tests

`make test` builds everything and runs `tests/run_tests.py`, which has four kinds
of test:

| Kind | Where | What it checks |
|---|---|---|
| `boot/...` | `BOOT_TESTS` in `tests/run_tests.py` | The kernel boots in QEMU; expected UART lines appear in order, forbidden ones never do. |
| `rook/...` | `tests/rook/*.rook` | A Rook program, compiled and booted in QEMU, prints exactly the `// expect:` lines in its header (and panics with the `// expect-panic:` message, if given). |
| `error/...` | `tests/rook/errors/*.rook` | The compiler rejects the program with the `// error: LINE:COL: message` given in its header. |
| `harness/...` | `HARNESS_TESTS` in `tests/run_tests.py` | The harness and build themselves: things a Rook program can't check, such as that changing the Makefile rebuilds everything. |

- `boot/` and `rook/` tests run on two QEMU machines: `raspi3b` (1 GB) and
  `raspi3ap` (512 MB, like the Zero 2 W). QEMU also logs accesses to unimplemented
  or misused devices, and any such report fails the test. A Rook program that
  causes one on purpose says so in its header:
  `// expect-qemu-log: pl011_write: Bad offset 0x100`. Other optional headers:
  `// machines: raspi3b` (run only there) and
  `// expect-fault: FAR_EL1:  0x0000000030000000` (must end in a reported CPU fault). An error test can have `// alone`: it is compiled
  without Raven's code.
  `MACHINES=raspi3b make test` runs one machine only.
- Run a subset with `python3 tests/run_tests.py rook/` (any test name or prefix),
  after `make test` has built the images.
- Full QEMU output of each run is saved in `build/test-logs/`.
- The assembly Fledgling generates for each Rook test is kept in
  `build/rook-tests/*.s`, for reading or debugging.
- Every PR that changes behaviour adds or updates a test. Tests are checked to fail
  when the behaviour is broken, not just to pass when it works. The PR description
  says how.

## Practices

These are how the work is done, as opposed to the design
[principles](principles.md) that decide what is built.

- **Tests must prove they can fail.** Every behaviour has a test, and a change's
  tests are checked against a deliberately broken implementation. The PR records
  which bug each test catches.
- **Everything is reviewable.** PRs are small enough for one sitting, decisions are
  logged with their alternatives and reasons, and generated output (such as the
  compiler's assembly) is kept so it can be read.
- **Decisions name their principle.** A new entry in `decisions.md` says which
  principle settled it, or why none did.
- **Design questions are asked one at a time**, each with code samples that show
  every option fairly, in realistic Rook rather than toy snippets. How the
  language feels matters, so nothing is built until the owner has chosen. A rule
  derived while building (not chosen by the owner) is marked "derived; open to
  review" in `decisions.md` and listed in the PR.
- **Error messages are read before they become tests.** Each new error test's
  program is compiled first, and its message and caret position are checked by
  eye before the `// error:` line is written from them.
- **Deliberate-bug checks are scripted.** For each new rule, a script edits the
  compiler (or Rook code) to break it, runs `make test`, restores the file, and
  records which tests failed (`tools/bug-check.py FILE OLD NEW` does this for one
  edit). The table goes in the PR. A check no test catches
  is either covered by a new test or listed as untested, with the reason.

## Merging

- Use **Squash and merge**, so `main` has one commit per PR.
- Recommended GitHub setting: *Settings → Branches → Add rule for `main`*:
  require a pull request and require the `build-and-test` check to pass. This
  makes the rules above enforced rather than just agreed.

## Real hardware

QEMU is the reference for testing. Real Pi Zero 2 W runs are manual and
recorded in the PR when done; see `tools/sdcard/README.md`. The plan for
automating them is in [roadmap.md](roadmap.md#later-real-hardware-in-ci).
