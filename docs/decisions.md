# Decision log

New decisions are checked against the ordered [design principles](principles.md),
and say which principle settled them.

Entries 56–63 cite the principles' first numbering (1 safe by default, 2 reader,
3 ambiguity, 4 uniformity, 5 trust, 6 visible costs, 7 familiarity). Decision 64
renumbered them; later entries use the current numbering.

Newest at the bottom. To change a decision, add a new entry that supersedes the old
one rather than editing it.

| #  | Decision            | Choice                                   | Alternatives considered                         | Why |
|----|---------------------|------------------------------------------|-------------------------------------------------|-----|
| 1  | Project goal        | Research idea, personal exploration      | Learning, usable product, hobby                 | Want to find out whether the idea holds up. |
| 2  | Headline claim      | Security (all four claims in scope)      | Fault tolerance, simplicity, concurrency safety | Most distinctive result an ocap actor OS can show. |
| 3  | Isolation           | Hybrid: language within domains, MMU between | Language only, hardware only                | Cheap actors where code is trusted, hardware backstop where it is not. |
| 4  | Board               | Pi Zero 2 W (AArch64)                    | Original Pi Zero (ARMv6)                        | Four cores, cleaner MMU, 64-bit. |
| 5  | Authority           | Pure object capabilities                 | Refs + rights bits, typed protocols             | Simplest to reason about; attenuation via proxies. |
| 6  | Message data        | Immutable sharing; mutable moved or copied | Ownership transfer only, always copy          | Safe sharing without copying large immutable data. |
| 7  | Memory reclamation  | Compile-time ownership                   | Per-actor GC, refcounting everywhere            | No collector in the trusted base; borrows stay local to an actor. |
| 8  | Shared frozen data  | Atomic refcounting, whole structure frozen at once (no cycles) | Owner outlives readers, regions per conversation | Only shared data pays a cost; owner-outlives fights async messaging. |
| 9  | Domain boundaries   | By trust level; domain restarts as a unit; more boundaries allowed, never fewer | Developer-defined, supervision subtrees | Unsafe code is always isolated by hardware. |
| 10 | Kernel language     | The language itself                      | Rust (temporary or permanent), C                | Strongest research claim; language must mature first. |
| 11 | Backend             | Own AArch64 codegen                      | LLVM, emit C, Cranelift/QBE                     | Small, auditable trusted toolchain. |
| 12 | Compiler language   | Self-host early                          | Rust, OCaml                                     | Dogfoods the language as soon as possible. |
| 13 | Bootstrap language  | Python                                   | Rust, OCaml, C                                  | Fastest to write; it gets thrown away. |
| 14 | Milestone order     | Capability demo before self-hosting      | Self-host first                                 | Test the hypothesis early. |
| 15 | Repository          | Monorepo                                 | Separate repos, submodules                      | Language and OS change together; one person; split later if needed. |
| 16 | Names               | Crow family: CorvOS (OS), Rook (language), Raven (kernel), Fledgling (bootstrap); Huginn, Muninn, Sentinel, Magpie, Nutcracker in reserve | AktorOS, Pactos/Seal, CellOS/Cyto, Keos/Kea, Argos/Io | Easy to say, a shared theme with room to grow; rooks are social and clever, and a rook is a castle. Supersedes the working name AktorOS. |
| 17 | Runtime             | None: the kernel (Raven) is the runtime; compiler emits small support code | Separate runtime layer, per-domain schedulers | The kernel already understands actors, so a runtime would duplicate it. |
| 18 | Same-domain messaging | Every send through Raven first; mailboxes designed for a later wake-only fast path; measure | Fast path from the start, kernel-only forever | Simplest to build and debug; the measurement is a research result; the fast path costs no security within a domain. |
| 19 | Rook syntax         | Rust-like braces (`actor`, `be`, `fn`, `let`/`var`) | Pony/Ruby `end` keywords, Python indentation, ML style | Familiar to systems programmers; simple to parse. |
| 20 | Replies             | Pass a reply-to reference                | Promises, `await`, blocking calls               | Pure actor model; no hidden machinery; blocking risks deadlock. |
| 21 | Narrowing references | Interface types (`self as Reply<T>`), no downcasts | Proxy actors only, both                | Zero-cost and compiler-enforced; revocation via proxy actors in library code. |
| 22 | Reference capabilities | `iso`, `val`, `tag` + `mut`/`read` borrows | Full Pony set (`ref`, `box`, `trn`), `Frozen<T>` type, explicit move/copy | Pony's precision without needing a GC; consistent with decision 7. |
| 23 | Errors              | `Result` for expected failures; bugs crash the actor and its domain restarts | Let it crash only, Pony partial functions | Explicit where failure is normal; supervision handles bugs. |
| 24 | Hardware and `unsafe` | Hardware as capabilities (`Mmio`, `Irq`); `unsafe` only in Raven; DMA via a trusted DMA actor | Unsafe actors, unsafe blocks       | Most drivers become provably confined safe code; DMA is the only hole without an IOMMU. Refines decision 9. |
| 25 | Language basics     | Rust-like types, enums and `match`; monomorphised generics; interfaces as the only abstraction; one module per file; no global mutable state | (defaults accepted) | Simple to compile with own codegen; globals would be ambient authority. |
| 26 | Workflow            | Claude builds on branches and opens PRs; CI boots every change in QEMU; the owner reviews and squash-merges | Pushing straight to main, owner writes the code | Owner wants to review and test, not write; CI makes every PR checkable without local setup. |
| 27 | Toolchain and tests | GNU binutils (`aarch64-linux-gnu-`), Make, QEMU `raspi3b` as the reference machine, Python test harness checking UART output | Clang/LLD, a semihosting exit for tests | Standard and packaged everywhere; output-matching tests work identically on QEMU and real hardware. |
| 28 | Fledgling output    | Emits GNU AArch64 assembly text; binutils assembles and links | Emitting machine code directly | Still our own code generator; assembly is far easier to read in review and to debug. |
| 29 | Statement endings   | Newlines end statements; no semicolons; continuation inside brackets or after an operator | Semicolons | Matches the agreed Rook samples; the rule is simple to lex. |
| 30 | Integer literals    | Typed from context, defaulting to U64 (char literals to U8) | Require explicit types, default to I32 | Rook code is mostly unsigned (sizes, addresses, registers). |
| 31 | Integer overflow    | Panics at every width, including `MIN / -1`, division by zero and oversized shifts; `as` truncates without panicking | Wrapping by default, Rust's debug/release split | One behaviour everywhere; a bug crashes the domain (decision 23) rather than corrupting state. Resolves the open question in rook.md. |
| 32 | Calling assembly    | `extern fn` declarations; all functions use AAPCS64 | A separate foreign-function syntax | Lets the kernel move from assembly to Rook one function at a time. |
| 33 | Not operator        | `!` is logical not on Bool and bitwise not on integers; no `~` | Separate `~` | As in Rust; one fewer operator. |
| 34 | Test kinds          | Boot tests, Rook programs run in QEMU with expected output in header comments, and compile-error tests with expected line, column and message | Python unit tests of compiler internals | Tests what matters (behaviour on the target) and stays valid when Fledgling is replaced by the self-hosted compiler. |
| 35 | Built-in type names | Lowercase: `u8`…`u64`, `i8`…`i64`, `bool` | Capitalised (`U64`), word names (`UInt32`) | Built-ins stand out from CamelCase user types. Updates decision 25. |
| 36 | Declarations        | `name: type = value`; `mut name: type = value`; immutable by default; the type is always written | `let`/`var`, `let`/`let mut`, `:=` | Immutability is the default and unspoken; one form for locals, parameters, fields and constants; a declaration can't be confused with an assignment. |
| 37 | Return types        | `fn f() -> u64` | `fn f(): u64`, `fn f() u64` | Visually separates inputs from output. |
| 38 | Returning values    | Always explicit `return`; `if` may be used as a value when each branch is one expression and `else` is present | Last-expression results, ternary, Python-style conditional | Every result is visible; the one value-producing form stays small. |
| 39 | Logic and bit operators | Words for both: `and`, `or`, `xor`, `not`, `shl`, `shr`; logical on bool (short-circuit), bitwise on integers. Supersedes decision 33 | Symbols for both; words for logic and symbols for bits | Reads clearly; safe because Rook has no truthiness, so mixing up the meanings is a type error. Precedence: `or`/`xor` < `and` < `not` < comparisons. |
| 40 | Loops               | One keyword: `loop i in a..b`, `loop cond`, `loop`; ranges exclude the end, which is evaluated once | `while` only; `while` + `loop` + `for` | One concept, three forms. |
| 41 | Capability position | Before the type: `iso Buffer`, `val Block`, `mut Buffer` | After the type (Pony) | Reads as an adjective; borrow parameters match the call site. Updates decision 22's syntax. |
| 42 | Handler keyword     | `on` | `be`, `msg`, `receive` | Reads as "on this message, do this". |
| 43 | Creating actors     | `spawn Uart(args)` runs the actor's `new(...)`, which sets every field; non-`mut` fields are set once there | Field values only, both | Setup that needs code (hardware init) has one obvious home. |
| 44 | Passing errors on   | Explicit `match` only; no `?` or `try` | `?`, `try` | Every error path is visible in the code. |
| 45 | `match` arms        | `Pattern => result`, one per line, no commas | Rust commas, Swift `case` | Consistent with newline-terminated statements. |
| 46 | Constants           | A top-level `NAME: type = value` is a compile-time constant; top-level `mut` is an error. Supersedes the `const` keyword | `const` keyword | Same form as every other declaration; enforces no global mutable state. |
| 47 | Naming              | Enforced by the compiler: snake_case values/functions, CamelCase types, SCREAMING_CASE constants | Warnings, unchecked | Consistency for free, and the parser can tell types from values by their case. |
| 48 | Comments            | `//` notes, `///` documentation; no block comments | C-style `/* */`, `#` | Simple to lex; documentation attaches to the item below. |
| 49 | Visibility          | Private by default; `pub` to export | Public by default, export lists | Explicit API surface; organisation only, since authority comes from references. |
| 50 | Own fields          | Written `.field`; `self` is the actor's own reference | `self.field`, bare names | Short but explicit; parameters can reuse field names. |
| 51 | Closing blocks      | Braces, with an optional named closer `} name` that closes every open block back to the nearest block of that name (function name, `loop`, `if`); checked by the compiler | Indentation, `end` keywords, Ada/Scala named ends, stacked closers, editor hints only | Keeps explicit, formatter-friendly delimiters while removing closing-brace cascades. Explored at length: the context is tagged only where it changes. |
| 52 | Tabs                | Indentation is spaces only; a tab is a compile error | Allow tabs | One canonical layout for the future formatter. |
| 53 | Loop names          | Every loop is named, written like a declaration: `rows: loop ...` | Optional labels; keyword closers (`} loop`) for unnamed loops | "The nearest loop" is an implicit rule; a name makes the context explicit wherever it's referred to. |
| 54 | `break` and `continue` | Always name their loop (`break rows`), which may be an outer one | Bare `break` for the innermost loop | Same reason as 53; also gives multi-level exits for free. |
| 55 | Closer names        | `} name` accepts only names written in the code (functions, handlers, actors, loop labels); keyword closers `} loop` / `} if` are removed. Refines 51 | Keyword shorthand for the nearest block | A closer can never be ambiguous. |
| 56 | Design principles   | An ordered set (premise of no ambient authority, then seven principles) in `principles.md`; new decisions name the principle that settled them | Unordered list; principles that restate decisions | Ordered so conflicts resolve; each names its cost so it actually decides things. |
| 57 | No shadowing        | A name can't be redeclared, hide a parameter, a function or an outer variable; sibling blocks may reuse names | Rust-style shadowing | Principle 3 (and 2): a name means one thing wherever it is visible. Found in the principles review. |
| 58 | Required brackets   | `and` inside `or`/`xor`, mixing `or` with `xor`, `not` before a comparison, and shifts mixed with arithmetic need brackets | Rely on the precedence table | Principle 3; principle 7 for shifts, whose precedence differs from C and Rust. Found in the principles review. |
| 59 | Range variable type | `name: loop i: u64 in a..b`; the type is always written. Updates 40 | Inferred from the bounds | Principle 4: the same declaration rule everywhere (decision 36). Found in the principles review. |
| 60 | `extern fn`         | Recorded as a known gap: it lets any file call any symbol (ambient authority). Must become Raven-only before actors exist | Leave unrestricted | The premise. Not enforceable until Fledgling has modules. Found in the principles review. |
| 61 | Integer conversions | `as` only where no value can be lost; `wrap` for narrowing and sign changes (keeps the low bits); `wrap` is an error where nothing could be lost | Rust's all-purpose `as`; `as` that panics when a value doesn't fit | Principle 1: the risky conversion costs a visible word. Principle 4: each word has one meaning. |
| 62 | `shl` and lost bits | `shl` panics if shifting back wouldn't give the original value; mask first to drop bits | Bits drop silently, as in Rust and C | Principle 1: `x shl 1` is as safe as `x * 2`; dropping bits is written visibly. |
| 63 | Unused results      | A call's unused result is an error; `discard f()` ignores it on purpose | Assign to `_`; only `Result` must be used | Principles 1 and 3: an error can never be ignored without a trace. `discard` follows Nim, like the operator words. |
| 64 | Principles extended | Premise adds least authority; new principles 2 (failure never leaves state in doubt), 5 (enforced, not advised) and 9 (predictable before fast); a Raven section (minimal kernel, after Liedtke); compatibility recorded as a non-goal. The list is renumbered | Leave the list as it was | A survey of Pony, Austral, Midori, Zig, Go, Ada, E, L4 and Hare found ideas our list lacked; principle 2 also records the owner's reason for preferring errors as values: exceptions leave state and cleanup in doubt. |
| 65 | No warnings         | Every compiler diagnostic is an error; there is no warning category | Warnings for style or likely mistakes | Principle 5. |
| 66 | Panics can't be caught | No exceptions, `try`/`catch`, `finally` or unwinding; a panic ends the domain | Recoverable panics, exceptions | Principle 2. |
| 67 | Scope of Rook       | The language for every level of CorvOS (kernel, drivers, services, applications); features are judged by any level's needs; `unsafe` and `extern fn` stay Raven-only; libraries before language features | Rook only serves the kernel and OS ("specialise") | Owner's intent. Recorded so features aren't rejected just because Raven doesn't need them. Principle 7 keeps the compiler small as the language grows. |
