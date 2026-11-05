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

## Text-keyed map (step 5c, decision 158)

`raven/textmap.rook` has `TextMap<V>` (`import textmap: TextMap`): `m: TextMap<Info> = TextMap(region: region)`,
`put(key, value)` (replaces a value for a key already there), `find(key)` (an `Option<V>`), `contains`, `len`,
`key_at(i)` and `value_at(i)` (the entries in the order they were added, the same every run). The key is
copied in. A hash table with linear probing over `List`s, doubling when half full; no removal.

## The lexer, 5d-1 (decision 159)

`compiler/lib/lexer.rook` is `Lexer`, a port of `bootstrap/fledgling/lexer.py`; `compiler/lexdump.rook` is
the hosted program that reads a Rook file from the UART and prints its tokens. The format, which Fledgling
prints for the same file with `python3 -m fledgling FILE --dump-tokens` (`bootstrap/fledgling/dump.py`):

    LINE:COL KIND [TEXT] [VALUE]

one line per token; `KIND` is `ident`, `int`, `char`, `str`, `newline`, `eof` or the keyword or punctuation
itself; a number or character has its value in decimal, a string its bytes as `x` and hex; a lexer error is
the single line `error LINE:COL: MESSAGE`. The two match when they print the same text.

- `make test` runs `tests/hosted/lexdump/`: 42 small cases (every kind of token and every lexer error, the
  rules for a line that goes on, columns after non-ASCII text) with the expected output from Fledgling
  committed beside each, and the files listed in `live.txt` against Fledgling's output at test time.
- `make selfhost-check` runs `tools/selfhost-check.py lexdump` on every Rook file of the repository
  (1029 of them): **all give the same tokens, or the same error, as Fledgling.**
- Differences, none of which any file of the repository shows: a number beyond 64 bits is an error here;
  a letter beyond ASCII outside a string or comment is not part of a name; `\x` needs both hex digits.

## The parser, 5d-2 (decisions 160, 161)

`compiler/lib/parser.rook` is `Parser`, a port of `bootstrap/fledgling/parser.py` function by function;
`compiler/parsedump.rook` is the hosted program that reads a Rook file from the UART, lexes and parses it, and
prints the tree. Fledgling prints the same for the same file with `python3 -m fledgling FILE --dump-tree`
(`bootstrap/fledgling/dump.py`, `tree()`):

    KIND [LINE:COL] [TEXT]

one node per line, two spaces of indent per level; a parse (or lexer) error is the single line
`error LINE:COL: MESSAGE`. The two match when they print the same text.

**The tree in Rook** is an arena: `Parser.nodes` is a `List<Node>`, and a node names its children by index
into it (no pointers). `Node` has `kind: Text`, `line`/`col` (0 when the node has no place), `text: Text`
and `kids: List<u64>`; it also has `parens` (an expression written in brackets: the parser needs it, the
dump does not show it). Node 0 is `none`, the part that is not there. There are no exceptions in Rook: the
first error is kept in `failed`/`error`/`error_line`/`error_col` and every loop stops once it is set.

**Kinds** (text is what follows the place; a group has no place; flags come before a name, in this order):

| Kind | Text | Children |
|---|---|---|
| `Program` | | items |
| `Module` | `public` | |
| `Import` | | `ModuleName` (text: the module), `ImportName`... |
| `Const` | `[pub] NAME` | `Type`, value |
| `Fn` | `[pub] [mut] [lossy\|reliable] NAME` | `Params` (of `Param`: text NAME, child `Type`), return `Type` or `none`, `Block` or `none` (extern, interface) |
| `Struct` | `[pub] NAME` | `TypeParams` (text: `T U`), `Fields`, `Methods` |
| `Enum` | `[pub] NAME` | `TypeParams`, `Variants` (of `Variant`: text NAME, child `Fields` or `none`), `Methods` |
| `Actor` | `[pub] NAME` | `Implements` (of `Interface`), `Fields`, `Fn` (the `new`) or `none`, `Handlers` |
| `Interface` | `[pub] NAME` | `Handlers` |
| `Field` | `[pub] [mut] NAME` | `Type` |
| `Type` | `[mut\|val\|iso] NAME` | type arguments |
| `Block` | | statements |
| `Decl` | `[mut] NAME` | `Type`, value |
| `Assign` | the operator | place, value |
| `ExprStmt` | `discard` or nothing | expression |
| `If` | | condition, `Block`, `Block` or `If` or `none` |
| `Loop` | label | condition or `none`, `Block` |
| `ForRange` | `LABEL VAR` | `Type`, start, end, `Block` |
| `Match`, `MatchExpr` | | subject, `Arms` (of `Arm`: `Patterns` or `none` for `else`, then a statement, `Block` or expression) |
| `PatternVariant` | NAME | `Bind` (text `FIELD NAME`)... |
| `PatternValue` | | value, end of range or `none` |
| `Unsafe`, `Return`, `Break`, `Continue` | label (for the last two) | `Block` / value or `none` |
| `Int`, `Char` | decimal value (negative if written `-5`) | |
| `Bool`, `Str` | `true`/`false`; `x` and hex of the bytes | |
| `Name`, `Unary`, `Binary`, `Cast` | name; operator; operator; `as` or `wrap` | operand(s); expression and `Type` |
| `Call`, `Spawn` | callee, actor | arguments (`MutArg` for `mut x`) |
| `StructLit` | NAME | `FieldInit` (text: field, child: value) |
| `Access`, `MethodCall` | field, method | the object or `none` (`.x`), then the arguments |
| `Copy`, `Consume`, `Narrow` | | value (and `Interface` for `Narrow`) |
| `SelfRef`, `SelfValue`, `IfExpr` | | `self`; `.`; condition, then, else |

- `make test` runs `tests/hosted/parsedump/`: small cases (every kind of node and one for each parser error)
  with Fledgling's output committed beside each, and the files in `live.txt` against Fledgling at test time.
- `make selfhost-check` runs `tools/selfhost-check.py parsedump` on every Rook file of the repository.
- The Python parser's other fields (the ones the checker fills in) are not part of the tree dump.

## Stages

| Step | What | Test |
|---|---|---|
| 5a (this PR) | Hosted runner and test kind; `numberer` | `hosted/numberer/*` |
| 5b (built) | Memory for a batch job: `Region` and `Block` in `raven/trusted/region.rook` (below) | `rook/region_*`, `error/region_*` |
| 5c | Language growth the compiler needs, one owner question at a time: generics (`Option<T>`, `Result<T, E>`; the first step is built, decision 154), then how a library type such as a growable list is made, a map, a text builder | Rook and error tests |
| 5d | Port, in this order, each checked against Fledgling on the same input: lexer (tokens; **built, 5d-1**), parser (tree dump; **built, 5d-2**), checker (accept/reject and message), code generator (assembly) | hosted tests comparing with Fledgling's output |
| 5e | Stage 2: the Rook compiler compiles itself; compile that result again; the two assembly files are identical. Retire Fledgling | the milestone's "done when" |

## Limits, said plainly

- Everything is QEMU. The compiler is not run on a Pi.
- A hosted program is slow (QEMU, one byte at a time over the UART); the tests keep inputs small.
- Bytes 2, 3 and 4 cannot appear in input or output.
