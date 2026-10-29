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
