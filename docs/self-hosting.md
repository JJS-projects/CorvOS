# Self-hosting: the compiler in Rook

Milestone 5 (roadmap): write the real compiler in Rook, in `compiler/`, until it compiles itself
and the result is identical. Fledgling (the Python bootstrap, `bootstrap/fledgling/`) builds it
at first and is kept for history afterwards. Order agreed with the owner; decision 152.

## How a compiler runs on Raven: hosted

Raven has no files. A compiler program is a Rook program in `compiler/NAME.rook`, built into a
test-style image (`make` builds `build/compiler/NAME.img`) and run in QEMU **hosted**:

- The host types the **input** into the UART and ends it with the byte 4. Input is text without
  the bytes 2, 3 and 4 (source files and assembly qualify).
- The program writes its **output** between the lines `Hosted: begin` and `Hosted: end`; it ends in a
  newline. Everything else on the UART (`Raven:` lines) is ignored.
- The test harness does this for `tests/hosted/NAME/CASE.in` and compares the output with
  `CASE.out` (test kind `hosted/NAME/CASE`, on both QEMU machines, in parallel with the Rook tests).

Why this and not a filesystem: nothing to build or trust beyond the UART, which already works;
the milestone's check ("rebuilds itself and the result is identical") only needs bytes in and out.
When there is a use for files, a filesystem actor can be added later without changing the
protocol. `compiler/numberer.rook` is the stage-0 program: it numbers the lines of its input.

## Stages

| Step | What | Test |
|---|---|---|
| 5a (this PR) | Hosted runner and test kind; `numberer` | `hosted/numberer/*` |
| 5b | Memory for a batch job: a region allocator in trusted Rook, much larger than the actor arena, no freeing | Rook tests |
| 5c | Language growth the compiler needs, one owner question at a time: generics (`Option<T>`, `Result<T, E>`), a growable list and a map, a text builder | Rook and error tests |
| 5d | Port, in this order, each checked against Fledgling on the same input: lexer (tokens), parser (tree dump), checker (accept/reject and message), code generator (assembly) | hosted tests comparing with Fledgling's output |
| 5e | Stage 2: the Rook compiler compiles itself; compile that result again; the two assembly files are identical. Retire Fledgling | the milestone's "done when" |

## Limits, said plainly

- Everything is QEMU. The compiler is not run on a Pi.
- A hosted program is slow (QEMU, one byte at a time over the UART); the tests keep inputs small.
- Bytes 2, 3 and 4 cannot appear in input or output.
