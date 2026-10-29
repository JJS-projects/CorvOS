# CorvOS

**Rook**, an actor-based systems programming language, and **CorvOS**, a
capability-secure microkernel OS for the Raspberry Pi Zero 2 W written in it.

This is a personal research project. The question it explores:

> If actors are the only way anything in the system can communicate, and an actor
> reference is the only form of authority, do we get an OS that is more secure,
> more fault tolerant, simpler and free of data races by construction?

The first thing it sets out to demonstrate is **security**: an actor provably cannot
reach anything it was not explicitly handed.

## Names

The project is named after the crow family (*Corvus*).

| Part               | Name          |
|--------------------|---------------|
| Operating system   | **CorvOS**    |
| Language           | **Rook**      |
| Kernel             | **Raven**     |
| Bootstrap compiler | **Fledgling** |

Kept in reserve for parts that don't exist yet: Huginn, Muninn, Sentinel, Magpie,
Nutcracker.

## Documents

- [Design](docs/design.md): the concept and the key design decisions
- [Roadmap](docs/roadmap.md): milestones, in order
- [Decision log](docs/decisions.md): what was decided, the alternatives, and why

## Repository layout

This is a monorepo. Rook and CorvOS will change together for a long time, so they
live in one place and can be changed in the same commit. Rook can be split out later
with `git subtree split` if it ever needs a life of its own.

| Directory    | Contents                                                     |
|--------------|--------------------------------------------------------------|
| `docs/`      | Design documents and decision log                            |
| `bootstrap/` | Fledgling, the throwaway bootstrap compiler, in Python       |
| `compiler/`  | The self-hosted Rook compiler, written in Rook               |
| `kernel/`    | Raven, the microkernel (also the scheduler and message delivery) |
| `tests/`     | Compiler tests and QEMU boot tests                           |
| `tools/`     | QEMU scripts, SD card / flashing helpers                     |

There is deliberately no runtime directory: Raven is the runtime. See
[design.md](docs/design.md#6-raven-the-kernel).

## Status

Design agreed; nothing built yet. Next up is milestone 1 (boot and UART in QEMU).
