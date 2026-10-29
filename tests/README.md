# tests

`make test` builds everything and runs `run_tests.py`. See [docs/workflow.md](../docs/workflow.md#tests) for the three kinds of test and how to add one.

- `run_tests.py`: the harness, and the kernel boot tests
- `rook/*.rook`: Rook programs run in QEMU, with expected output in their header comments
- `rook/errors/*.rook`: programs the compiler must reject, with the expected error
