# AktorOS design

Status: agreed starting point, October 2026. Expect this to change; record changes in
[decisions.md](decisions.md).

The language currently has no name of its own. "Aktor" is used here as a working name.

## 1. Goal

Personal research. The hypothesis is that an OS built from actors, where actor
references are capabilities, is better on four fronts:

| Claim              | What it means here                                                     |
|--------------------|------------------------------------------------------------------------|
| Security           | No ambient authority. You can only use what you were handed.           |
| Fault tolerance    | Failed components are restarted by supervisors; the system stays up.   |
| Simplicity         | Messages replace syscalls, threads, locks and ad hoc IPC.              |
| Concurrency safety | No shared mutable state, so no data races and no locks, by construction.|

**Security is the headline.** The first demo has to show that a buggy or malicious
actor cannot reach anything it was not given. The other three claims are tested later.

## 2. Target hardware

**Raspberry Pi Zero 2 W**: quad-core Cortex-A53, ARMv8-A, run in AArch64 mode.

- Four cores give real parallelism, so the scheduler is multicore from the design
  stage, even if early milestones run on one core.
- The ARMv8 MMU is used for protection domains (section 5).
- The board has **no IOMMU**, so devices doing DMA can write anywhere in physical
  memory. See the open question in section 9.
- Development happens in QEMU (`raspi3b` machine, same SoC family) before real hardware.

## 3. The language

### 3.1 Actors

- An actor has private state, a mailbox, and behaviours (message handlers).
- Actors communicate only by asynchronous message send.
- Interrupts are delivered to driver actors as ordinary messages.

### 3.2 Authority: pure object capabilities

An actor reference **is** the authority to send to that actor. There are no other
permission bits, ACLs, or global names.

An actor can get a reference only by:

1. creating the actor,
2. being sent the reference in a message, or
3. being given it when it was created.

References cannot be forged: no casting integers to references, no global registry,
no reflection. To give someone *less* authority, you hand them a reference to a proxy
actor that forwards only the messages you allow (attenuation) and that you can later
stop forwarding through (revocation).

### 3.3 Messages: immutable sharing

- **Immutable** data may be shared between actors freely.
- **Mutable** data cannot be shared. It is either moved (the sender loses it) or copied.

This is close to Pony's reference capabilities, simplified.

### 3.4 Memory: ownership plus refcounted frozen data

- Everything an actor holds privately is managed by **compile-time ownership**
  (move semantics and borrows, Rust style). No garbage collector.
- Borrows never cross an actor boundary, because nothing mutable is shared. Borrow
  checking is therefore local to one actor's code, which keeps the checker far simpler
  than Rust's general case.
- Data that is **frozen** for sharing becomes **atomically reference counted**
  (like Rust's `Arc`, but built into the language). Atomic because there are four cores.
- Freezing applies to a whole structure at once, so frozen data cannot contain cycles
  and refcounting is enough to free it.

Why: the smallest trusted base and no runtime collector, which suits a security
claim. Only deliberately shared data pays any runtime cost.

### 3.5 Unsafe code

Poking hardware registers, setting up page tables and the like needs an `unsafe`
escape hatch. Any actor containing unsafe code is untrusted by definition for the
purposes of section 5.

## 4. Compiler and toolchain

| Decision          | Choice                                                             |
|-------------------|--------------------------------------------------------------------|
| Backend           | Own AArch64 code generator. No LLVM, no C.                         |
| Bootstrap         | A throwaway compiler in **Python**, supporting just enough of the language to write the real compiler. |
| Real compiler     | Written in the language itself (**self-hosted early**).            |

Owning the codegen keeps the whole toolchain small and auditable, which matters
because the compiler is part of the trusted base (section 5).

## 5. Isolation: hybrid, by trust level

Isolation is enforced in two layers:

- **Within a protection domain**: the language guarantees isolation. Actors share one
  address space and rely on the compiler (ownership, no forgeable references).
- **Between domains**: the MMU guarantees isolation. Each domain has its own address
  space, and messages crossing a domain boundary are copied.

**Trust decides the boundaries:**

- Code the compiler fully verifies (no `unsafe`) may share a domain.
- Anything containing `unsafe` code (drivers, for example) or from an untrusted
  origin gets its own domain.
- More boundaries than this are always allowed; fewer are never allowed.

**A domain fails and restarts as a unit.** If anything in a domain faults, its
supervisor restarts the whole domain. This keeps failure and protection boundaries
aligned.

The trusted computing base is therefore: the compiler, the kernel, and the
`unsafe` code inside the kernel.

## 6. The kernel

- A microkernel, **written in the language itself**, using `unsafe` where it must.
- Responsibilities: boot, exception vectors, MMU and domain setup, scheduling actors
  across four cores, delivering messages (including cross-domain copies), and turning
  interrupts into messages.
- Everything else (drivers, services) runs as actors in domains above it.

## 7. Supervision

Erlang-style supervision trees. Supervisors are actors that hold references to their
children and restart them (at domain granularity) when they fail. This is how the
fault-tolerance claim will be tested.

## 8. Prior art

- **Singularity / Midori** (Microsoft): safe-language OS, typed channels between processes.
- **Erlang / BEAM**: actors, supervision, "let it crash".
- **Pony**: reference capabilities, data-race freedom at compile time.
- **seL4**: capability microkernel, formally verified.
- **E language / object capabilities literature**: attenuation and revocation via proxies.

## 9. Open questions

- **DMA without an IOMMU** (decide by milestone 7). Proposed answer: only a small
  trusted kernel actor can program DMA, and it checks that every address range belongs
  to the requesting domain.
- **Mailbox bounds and backpressure**: bounded or unbounded, and what happens when full.
- **Scheduling policy** across cores: work stealing, per-core run queues, affinity.
- **Language name.**
