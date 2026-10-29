# CorvOS design

Status: agreed starting point, October 2026. Expect this to change; record changes in
[decisions.md](decisions.md).

- **Rook**: the language
- **CorvOS**: the operating system
- **Raven**: the kernel
- **Fledgling**: the bootstrap compiler

## 1. Goal

Personal research. The hypothesis is that an OS built from actors, where actor
references are capabilities, is better on four fronts:

| Claim              | What it means here                                                      |
|--------------------|-------------------------------------------------------------------------|
| Security           | No ambient authority. You can only use what you were handed.            |
| Fault tolerance    | Failed components are restarted by supervisors; the system stays up.    |
| Simplicity         | Messages replace syscalls, threads, locks and ad hoc IPC.               |
| Concurrency safety | No shared mutable state, so no data races and no locks, by construction.|

**Security is the headline.** The first demo has to show that a buggy or malicious
actor cannot reach anything it was not given. The other three claims are tested later.

## 2. Target hardware

**Raspberry Pi Zero 2 W**: quad-core Cortex-A53, ARMv8-A, run in AArch64 mode.

- Four cores give real parallelism, so the scheduler is multicore from the design
  stage, even if early milestones run on one core.
- The ARMv8 MMU is used for protection domains (section 5).
- The board has **no IOMMU**, so devices doing DMA can write anywhere in physical
  memory. See the open questions in section 9.
- Development happens in QEMU (`raspi3b` machine, same SoC family) before real hardware.

## 3. Rook, the language

This section covers the semantics that matter to the OS. Syntax, a sample program
and the full capability table are in [rook.md](rook.md).

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

This is enforced by Pony-style reference capabilities, cut down to fit ownership:
`iso` (owned, sendable by move), `val` (frozen, shared), `tag` (actor references),
plus `mut` and `read` borrows within one actor. See [rook.md](rook.md#5-reference-capabilities).

Replies use plain references: a sender passes a reference (usually narrowed to an
interface such as `Reply<T>`) that the receiver sends its answer to.

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

### 3.5 Hardware access and unsafe code

- Hardware is reached through **capabilities**: Raven hands a driver an `Mmio` value
  covering exactly its device's registers, plus an `Irq` value that routes the
  interrupt to it as a message. Access is bounds checked.
- Ordinary drivers therefore contain **no `unsafe` code**. `unsafe` exists only
  inside Raven.
- **DMA-capable devices** are the exception, because without an IOMMU they can write
  anywhere. Their DMA registers are never handed to drivers; drivers ask a trusted
  DMA actor in Raven, which checks every address range.

### 3.6 Errors

Expected failures return `Result<T, E>`. Bugs (panics, failed bounds checks,
overflow) crash the actor, and its domain is restarted by its supervisor.

## 4. Compiler and toolchain

| Decision          | Choice                                                             |
|-------------------|--------------------------------------------------------------------|
| Backend           | Own AArch64 code generator. No LLVM, no C.                         |
| Bootstrap         | **Fledgling**, a throwaway compiler in **Python**, supporting just enough of Rook to write the real compiler. |
| Real compiler     | Written in Rook itself (**self-hosted early**).                    |

Owning the codegen keeps the whole toolchain small and auditable, which matters
because the compiler is part of the trusted base (section 5).

The small amount of support code compiled programs need (refcount increments and
decrements, freezing, copying message data) is emitted by the compiler, like Rust's
`core`. It is not a separate runtime layer (see section 6).

## 5. Isolation: hybrid, by trust level

Isolation is enforced in two layers:

- **Within a protection domain**: the language guarantees isolation. Actors share one
  address space and rely on the compiler (ownership, no forgeable references).
- **Between domains**: the MMU guarantees isolation. Each domain has its own address
  space, and messages crossing a domain boundary are copied.

**Trust decides the boundaries:**

- Code the compiler fully verifies may share a domain. Since `unsafe` exists only in
  Raven, this includes drivers that use only `Mmio` and `Irq` capabilities.
- Anything from an untrusted origin, or a driver for a DMA-capable device, gets its
  own domain.
- More boundaries than this are always allowed; fewer are never allowed.

**A domain fails and restarts as a unit.** If anything in a domain faults, its
supervisor restarts the whole domain. This keeps failure and protection boundaries
aligned.

The trusted computing base is therefore: the compiler, Raven, and the `unsafe`
code inside Raven.

## 6. Raven, the kernel

- A microkernel, **written in Rook**, using `unsafe` where it must.
- Responsibilities: boot, exception vectors, MMU and domain setup, scheduling actors
  across four cores, delivering messages (including cross-domain copies), and turning
  interrupts into messages.
- Everything else (drivers, services) runs as actors in domains above it.

### 6.1 No separate runtime

Most actor languages (Erlang, Pony, Akka) need a runtime because they sit on an OS
that knows nothing about actors. CorvOS's kernel already understands actors, so
**Raven is the runtime**. Scheduling lives only in Raven; there is no per-domain
scheduler.

### 6.2 Message delivery: kernel first, then measure

- **To start with, every send goes through Raven**, including sends between actors
  in the same domain. This is the simplest design, easy to debug, and gives the
  kernel a trace of every message.
- Mailboxes are **lock-free multi-producer queues** from the start (needed for four
  cores in any case), laid out so that a fast path can be added later without
  changing the language.
- **Possible fast path, added only if measurements justify it:** same-domain sends
  write directly into the receiver's mailbox, and call Raven only when the receiver
  is asleep and must be woken (the same trick as Linux futexes). A busy actor drains
  its whole mailbox before yielding, so the kernel is involved once per wake-up, not
  once per message. Scheduling still lives only in Raven, but the kernel no longer
  sees every message.
- This costs nothing in security: within a domain, the compiler already guarantees
  references can't be forged, so the kernel checking each send adds no protection.
- Measuring kernel-mediated sends against the fast path is a research result in its
  own right (milestone 3).

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

- **DMA without an IOMMU**: the approach is settled (a trusted DMA actor in Raven
  checks every address range; section 3.5). The details of its interface are decided
  at milestone 7.
- **Mailbox bounds and backpressure**: bounded or unbounded, and what happens when full.
- **Scheduling policy** across cores: work stealing, per-core run queues, affinity.
- **Same-domain fast path**: decide after measuring at milestone 3 (section 6.2).
