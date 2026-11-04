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

## Memory: `Region` and `Block` (step 5b, decision 153)

A hosted program gets memory from one 64 MB region at physical 64 MB, far larger than the 64 KB
actor arena. `make_region(peripherals)` makes the one `Region` (only from the whole peripherals,
and only once). `region.block(bytes)` returns a zeroed `Block`; nothing is ever freed, because a
job runs once and ends. A `Block` is read and written by byte (`get`, `set`) or by 64-bit word
(`get64`, `set64`), every access checked against its length; safe code never sees an address and
cannot build a `Block` itself (decision 96). `Region.left()` says how much is left. A full region panics.
A `Region` is a handle: the free address is kept in the actor arena, so a copy of it allocates from
the same pool (decision 156), and a `List` can hold one and grow itself.

## Lists (step 5c, decision 156)

`List<T>` (in `raven/trusted/region.rook`, `import region: List`) is a growable list of any `T`:
`List(region: region)` builds an empty one (the element type comes from the place it goes to, as for
`Some(...)`), `push`, `pop`, `get`, `set`, `len`, `clear`. It takes room from the region on the first
`push` and doubles when full; indexes are checked; a struct or enum is stored word by word, and `get`
returns a copy. Built on three trusted-only builtins (`store_element`, `load_element`, `words_of`).

## Option, Result and Text (step 5c, decision 157)

`raven/option.rook` has the library `Option<T>` (`Some(value: v)`, `None`) and `Result<T, E>`
(`Ok(value: v)`, `Err(error: e)`), written like any generic enum; their variant names belong to them, as
variant names are global. `raven/text.rook` has `Text`, a byte string that grows (`push`, `append` of quoted
text, `append_text`, `append_dec`, `at`, `len`, `equals`, `same_as`, `hash`, `clear`), written in ordinary
Rook on `List<u8>` (a byte takes a slot of 8 bytes: wasteful, with no unsafe code). The name table the
compiler needs will be keyed by `Text`, not by any type: that needs no hashing in the language.

## Stages

| Step | What | Test |
|---|---|---|
| 5a (this PR) | Hosted runner and test kind; `numberer` | `hosted/numberer/*` |
| 5b (built) | Memory for a batch job: `Region` and `Block` in `raven/trusted/region.rook` (below) | `rook/region_*`, `error/region_*` |
| 5c | Language growth the compiler needs, one owner question at a time: generics (`Option<T>`, `Result<T, E>`; the first step is built, decision 154), then how a library type such as a growable list is made, a map, a text builder | Rook and error tests |
| 5d | Port, in this order, each checked against Fledgling on the same input: lexer (tokens), parser (tree dump), checker (accept/reject and message), code generator (assembly) | hosted tests comparing with Fledgling's output |
| 5e | Stage 2: the Rook compiler compiles itself; compile that result again; the two assembly files are identical. Retire Fledgling | the milestone's "done when" |

## Limits, said plainly

- Everything is QEMU. The compiler is not run on a Pi.
- A hosted program is slow (QEMU, one byte at a time over the UART); the tests keep inputs small.
- Bytes 2, 3 and 4 cannot appear in input or output.
