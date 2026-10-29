# bootstrap

**Fledgling**, the throwaway bootstrap compiler for Rook, written in Python 3
(standard library only, and run without a bytecode cache, so a stale
copy can never be used). It compiles the subset of Rook described in
[docs/rook.md](../docs/rook.md#11-fledglings-subset) to GNU AArch64 assembly. It is
retired at milestone 5, when the Rook compiler can compile itself.

```sh
PYTHONPATH=bootstrap python3 -m fledgling program.rook -o program.s
```

Errors are reported as `file:line:col: error: message`, with the source line.

| File | Stage |
|---|---|
| `fledgling/lexer.py` | source text to tokens; decides where statements end |
| `fledgling/parser.py` | tokens to syntax tree (`syntax.py`) |
| `fledgling/checker.py` | name resolution, types, mutability, return paths, constants |
| `fledgling/codegen.py` | syntax tree to assembly: stack slots, checked arithmetic, AAPCS64 calls |
| `fledgling/rtypes.py` | the built-in types |

The code generator is deliberately naive (every value goes through `x0` and the
stack). Speed doesn't matter here: correctness and readability do.

Tests live in `tests/rook/` and run with `make test`.
