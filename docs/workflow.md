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
| `make run` | Boot the kernel in QEMU with the UART on your terminal (quit: Ctrl-A then X) |
| `make disasm` | Disassemble the kernel |
| `make sdcard` | Collect files for a real Pi Zero 2 W (see `tools/sdcard/`) |

## Tests

`make test` builds everything and runs `tests/run_tests.py`, which has three kinds
of test:

| Kind | Where | What it checks |
|---|---|---|
| `boot/...` | `BOOT_TESTS` in `tests/run_tests.py` | The kernel boots in QEMU; expected UART lines appear in order, forbidden ones never do. |
| `rook/...` | `tests/rook/*.rook` | A Rook program, compiled and booted in QEMU, prints exactly the `// expect:` lines in its header (and panics with the `// expect-panic:` message, if given). |
| `error/...` | `tests/rook/errors/*.rook` | The compiler rejects the program with the `// error: LINE:COL: message` given in its header. |

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

## Merging

- Use **Squash and merge**, so `main` has one commit per PR.
- Recommended GitHub setting: *Settings → Branches → Add rule for `main`*:
  require a pull request and require the `build-and-test` check to pass. This
  makes the rules above enforced rather than just agreed.

## Real hardware

QEMU is the reference for testing. Real Pi Zero 2 W runs are manual and
recorded in the PR when done; see `tools/sdcard/README.md`.
