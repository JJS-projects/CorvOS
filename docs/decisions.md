# Decision log

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
