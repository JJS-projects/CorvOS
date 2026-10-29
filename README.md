# AktorOS

An actor-based systems programming language, and a capability-secure microkernel OS
for the Raspberry Pi Zero 2 W written in it.

This is a personal research project. The question it explores:

> If actors are the only way anything in the system can communicate, and an actor
> reference is the only form of authority, do we get an OS that is more secure,
> more fault tolerant, simpler and free of data races by construction?

The first thing it sets out to demonstrate is **security**: an actor provably cannot
reach anything it was not explicitly handed.

## Documents

- [Design](docs/design.md): the concept and the key design decisions
- [Roadmap](docs/roadmap.md): milestones, in order
- [Decision log](docs/decisions.md): what was decided, the alternatives, and why

## Repository layout

This is a monorepo. The language and the OS will change together for a long time,
so they live in one place and can be changed in the same commit. The language can be
split out later with `git subtree split` if it ever needs a life of its own.

| Directory    | Contents                                                     |
|--------------|--------------------------------------------------------------|
| `docs/`      | Design documents and decision log                            |
| `bootstrap/` | Throwaway bootstrap compiler, written in Python              |
| `compiler/`  | The self-hosted compiler, written in the language itself     |
| `runtime/`   | Actor runtime: mailboxes, scheduling, refcounted frozen data |
| `kernel/`    | The microkernel                                              |
| `tests/`     | Compiler tests and QEMU boot tests                           |
| `tools/`     | QEMU scripts, SD card / flashing helpers                     |

## Status

Design agreed; nothing built yet. Next up is milestone 1 (boot and UART in QEMU).
