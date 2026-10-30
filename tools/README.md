# tools

- `install-deps.sh`: install the toolchain and QEMU (used by CI and the dev container too)
- `run-qemu`: boot a kernel image with the UART on your terminal
- `sdcard/`: config and instructions for a real Pi Zero 2 W
- `bug-check.py`: break one thing on purpose, run `make test`, and print which tests failed
- `coverage.py`: which lines of the compiler the tests reach (`make coverage`); fails on a gap not explained in `coverage-gaps.txt`
- `difftest.py`: random arithmetic programs checked against a model written in Python (`make difftest`)
