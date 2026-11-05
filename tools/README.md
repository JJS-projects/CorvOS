# tools

- `install-deps.sh`: install the toolchain and QEMU (used by CI and the dev container too)
- `run-qemu`: boot a kernel image with the UART on your terminal
- `sdcard/`: config and instructions for a real Pi Zero 2 W
- `selfhost-check.py`: a self-hosted compiler stage against Fledgling on every Rook file (`make selfhost-check`)
- `bug-check.py`: break one thing on purpose, run `make test`, and print which tests failed
- `coverage.py`: which lines of the compiler the tests reach (`make coverage`); fails on a gap not explained in `coverage-gaps.txt`
- `difftest.py`: random arithmetic programs checked against a model written in Python (`make difftest`)
- `decisions-index.py`: regenerate `docs/decisions-index.md` from `decisions.md` (`--check` in `make test`)
- `new-error-test.py`: make an error test in two steps (placeholder, then `--accept` once the real message has been read)
- `bug-check-batch.py`: run a list of bug checks and write one summary table
- `pr-body.py`: build the PR body from the template, test output and bug-check table; `--open` creates the PR
