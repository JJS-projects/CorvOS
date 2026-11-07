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

A hosted program gets memory from one 128 MB region at physical 64 MB, far larger than the 64 KB
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

## The generics pass, 5d-3a (decision 162)

`compiler/lib/generics.rook` is `Generics`, a port of `bootstrap/fledgling/generics.py` (decision 154) that
changes the arena of the parser in place; `compiler/lib/program.rook` loads a whole program; and
`compiler/expanddump.rook` is the hosted program that reads the files, expands the generics and prints the
program. Fledgling prints the same for the same files with
`python3 -m fledgling FILE... [--trusted DIR] --dump-expanded`.

**Several files in one stream.** From this stage a program is more than one file (the checker needs Raven's
library beside the file it checks). The UART stream is the files one after another, each starting with a
line `@@@ PATH` or `@@@ trusted PATH` (`@` cannot start anything in Rook, so no file has such a line; the
text of a file ends at the next header). Each file is lexed and parsed with its own number, into one arena.
The first file that fails stops the load; an error is the one line `error PATH:LINE:COL: MESSAGE`.

**The format** is the tree dump of 5d-2, with these changes: a place in another file than the one its
top-level item is in is written `PATH:LINE:COL` (a generic's instance has places from the file where its
type arguments were written); a top-level item ends with a `File [trusted ]PATH` line; a type reference the
pass rewrote has a `VisibleIn PATH` line (the file whose imports its name is checked in); an instance has a
`GenericBase NAME` line. The templates are no longer items, and the instances come last, in the order found.
Node `file`, `visible_in` and `generic_base` are the fields behind those lines.

- `make test` runs `tests/hosted/expanddump/`: cases of one to three files (no generics, `Option`, two
  parameters, nesting, instances in instances, a template in another file, modifiers, trusted files,
  templates never used) and every error of the pass, plus the programs in `live.txt` against Fledgling.
- `make selfhost-check` runs `tools/selfhost-check.py expanddump` on every test program and every compiler
  program, each with Raven's files as the tests compile it (error tests as their `// alone` header says).

## The checker's declarations, 5d-3b (decision 163)

`compiler/lib/checker.rook` is `Checker`, a port of the part of `bootstrap/fledgling/checker.py` that runs
before constants and function bodies (`Checker.program` up to `implements`): imports and what each file may
see, the names of types, variants, actors, interfaces and functions, fields, sizes, signatures, handler
numbers, `implements`. `compiler/declsdump.rook` is the hosted program that reads the files, parses them,
expands the generics, checks the declarations and prints what the checker knows. Fledgling prints the same for
the same files with `python3 -m fledgling FILE... [--trusted DIR] --dump-declarations` (it runs its checker
with `stop="declarations"`).

**The tables.** Types, fields, variants, signatures and parameters are rows of lists that refer to each other by
number (row 0 of each means none); a type is found by its name (`type_names`). A struct's, actor's or variant's
fields point to their owner; a signature has a role (function, method, `new`, handler). Nothing in them points
into Fledgling's classes, so the later steps (bodies, the code generator) read the same rows.

**The format** (one line each; an error is the one line `error FILE:LINE:COL: MESSAGE`):

    imports FILE: NAME<-FROMFILE ...           one line per file that imports, names in order
    generics NAME ...                          the templates
    generic-variants VARIANT:ENUM ...
    handler-numbers NAME=N ...
    struct NAME words=N                        then, indented: field NAME TYPE [pub]; new (PARAMS); method NAME [mut] (PARAMS) -> TYPE
    enum NAME words=N                          then: variant NAME tag=N with field lines below it; methods
    actor NAME words=N                         then: field NAME TYPE [mut] [val] [iso]; new (PARAMS); handler NAME lossy|reliable (PARAMS) number=N; implements NAME
    interface NAME                             then: handler lines
    fn NAME (PARAMS) -> TYPE

`PARAMS` is `(T, mut T)` for a function or method and `(iso T, val T)` for a handler or a `new`. Constants,
function bodies and everything they need are the next steps.

- `make test` runs `tests/hosted/declsdump/`: positive cases (types and sizes, functions and methods, actors
  and interfaces, generics, imports, trusted files, handler numbers) and a case for every error of this part,
  plus two whole programs (Raven's files and a test) in `live.txt`.
- `make selfhost-check` runs `tools/selfhost-check.py declsdump` on every test program and every compiler
  program with Raven's files: the declarations of each are the same as Fledgling's, or the error is.
- The generics pass had a latent difference: copying a template copied the shared `none` node into a new one, which
  the dump of 5d-3a could not show (they print alike) but the checker's tests for a missing return type can.
  `copy_tree` now leaves node 0 alone.

## The checker's constants and bodies, 5d-3c (decision 165)

Function bodies mix typing with ownership, and the checker reports the first error of a whole program, so a
half-finished port cannot be compared on whole programs. Fledgling's `check` therefore has three switches
(`bootstrap/fledgling/checker.py`): `ownership=False` (moves, locks and loop states are not tracked;
`consume` still checks its operand and `require_fresh` and mutability stay), `bodies=` `"all"`, `"none"` or
one file's path (only the items declared there), and `actor_bodies=False` (an actor's `new` and handlers).
The Rook checker is compared under the same switches, in stages:

| Step | What is checked | Fledgling's flags |
|---|---|---|
| 5d-3c-0 (built) | constants, nothing in a function | `--dump-bodies --no-bodies` |
| 5d-3c-1 (built) | + plain function and method bodies of the last file (and of the instances of generics declared there), ownership off, actors skipped | `--dump-bodies` |
| 5d-3c-2 (built) | + actor `new` and handlers (a handler's declaration also carries its `number`) | `--dump-bodies --actors` |
| 5d-3d-1/2 | + ownership, then every file's bodies | `--dump-bodies --ownership --actors` |

`compiler/bodiesdump.rook` is the hosted program: it reads a stream of files (as `declsdump` does), runs the
generics pass and the declarations, then `Checker.constants()`, and prints the constants and the tree of the
**last file** after the checker. Fledgling prints the same with `python3 -m fledgling FILE... [--trusted DIR]
--dump-bodies [--no-bodies] [--ownership] [--actors]` (`dump_bodies`, `bootstrap/fledgling/dump.py`).

**The format.** First a line `const NAME TYPE VALUE` per constant, in the order checked (a number or bool in
decimal, text as `x` and the hex of its bytes). Then `Program` and the items of the last file, as the tree dump
of 5d-2/5d-3a writes them, but every node the checker looked at ends with what it recorded:

    KIND [POS] [TEXT] : TYPE [slot=N] [end_slot=N] ... [const=N] [nslots=N] [const_str=xHEX] [variant=NAME] ...

The numbers come in a fixed order (`slot end_slot subject_slot self_slot result_slot temp args_slot args_words
payload payload_words handler_index words number stopped lo hi const nslots`), then `const_str=`, `variant=`,
`struct=`, the flags `scalar` and `send`, and `slots=SLOT/OFFSET/AGG,...`. The checker also gives each method, `new` and handler the file of its type, which is a
`File [trusted ]PATH` line below it, and an actor or interface declaration its type (`: Name`). A value the parser already gives
a node (`words=1`, `stopped=0`, `payload_words=0`, `args_words=0`) is not written; a function that was not
checked has no notes. In Rook the notes are **side tables** of the checker (`note_rows`, `notes`: a row per
node that has any), not fields of the nodes, so the parser's arena stays what the parser built.

- 5d-3c-1 adds to `Checker`: `bodies()`, `function()`, the statements, every expression, calls and built-ins, struct and enum values, fields and methods, sends, `match`, `copy`, `consume`, `narrow`, `spawn`, `watch`, `stop` (ownership off: `consume` checks its operand but no move is recorded; the divergence code arrives with ownership in 5d-3d). Slots, temps and values are recorded as notes; `a += b` is rewritten to `a = a + b` in the tree, as Fledgling does. 5d-3c-2 adds an actor's `new` and handlers (the hosted program is compared with Fledgling's `--dump-bodies --actors`). Not yet: moves, locks and loop states (5d-3d).
- 5d-3c-0 was: literals, names, `not`/`-`, `if` as an expression, the binary operators, `as`/`wrap`, text in
  quotes, and every error a constant can give (`constants can't call functions`, ...). A construct that a
  constant can reach but that needs the machinery of bodies (a field of a text constant, `copy(HELLO)`) says
  `not checked yet: KIND`; nothing in the repository reaches it (`make selfhost-check` shows it if one does).
- `make test` runs `tests/hosted/bodiesdump/`: 90 cases (written by `tools/bodiesdump-cases.py`; the constants
  of arithmetic, bitwise, shifts, casts, comparisons and int_literals are worked out on paper in the
  harness test and the case names) and 389 programs in `live.txt` (twelve test programs with Raven's files, and every error test that needs no imports, one file each); `harness/dump_bodies` pins the format
  and the switches with a hand-worked example.
- `make selfhost-check` runs `tools/selfhost-check.py bodiesdump` on every test program and compiler program.

## Stages

| Step | What | Test |
|---|---|---|
| 5a (this PR) | Hosted runner and test kind; `numberer` | `hosted/numberer/*` |
| 5b (built) | Memory for a batch job: `Region` and `Block` in `raven/trusted/region.rook` (below) | `rook/region_*`, `error/region_*` |
| 5c | Language growth the compiler needs, one owner question at a time: generics (`Option<T>`, `Result<T, E>`; the first step is built, decision 154), then how a library type such as a growable list is made, a map, a text builder | Rook and error tests |
| 5d | Port, in this order, each checked against Fledgling on the same input: lexer (tokens; **built, 5d-1**), parser (tree dump; **built, 5d-2**), generics pass (**built, 5d-3a**), checker (accept/reject and message; declarations **built, 5d-3b**; constants **built, 5d-3c-0**, then bodies), code generator (assembly) | hosted tests comparing with Fledgling's output |
| 5e | Stage 2: the Rook compiler compiles itself; compile that result again; the two assembly files are identical. Retire Fledgling | the milestone's "done when" |

## Limits, said plainly

- The boot stack is 256 KB and every function checks its frame against a limit (decision 164): a program that
  recurses too deep panics `stack overflow`. The code generator written in Rook (5d-4) must emit the same check
  (`ldr x16, =boot_stack_limit; cmp sp, x16; b.lo` to a panic stub) to give byte-identical assembly.
- Everything is QEMU. The compiler is not run on a Pi.
- A hosted program is slow (QEMU, one byte at a time over the UART); the tests keep inputs small.
- Bytes 2, 3 and 4 cannot appear in input or output.
