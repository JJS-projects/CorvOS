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

- An actor has private state, a mailbox, and handlers (`on` message handlers).
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
- Data that is **frozen** for sharing becomes **reference counted**, built into the
  language. The count is a plain integer, not an atomic one, while frozen data stays
  inside one domain, which lives on one core (section 6.3); a send between domains
  copies the data instead of sharing it (decision 115).
- Freezing applies to a whole structure at once, so frozen data cannot contain cycles
  and refcounting is enough to free it.

Why: the smallest trusted base and no runtime collector, which suits a security
claim. Only deliberately shared data pays any runtime cost.

### 3.5 Hardware access and unsafe code

- Hardware is reached through **capabilities**: Raven hands a driver an `Mmio` value
  covering exactly its device's registers, plus an `Irq` value that routes the
  interrupt to it as a message. Access is bounds checked.
- Ordinary drivers therefore contain **no `unsafe` code**. `unsafe` exists only
  inside Raven, in files under `raven/trusted/` (decision 90).
- `unsafe` is used to build a few capability types (`Mmio`, `Irq`, page tables,
  mailboxes) that check their own rules; the rest of Raven uses them safely.
- Some trusted code runs outside the kernel: `Mmio.read32` is compiled into each
  driver, and its load runs in the driver's domain on registers Raven mapped
  there. Two layers make that safe: the compiler (an `Mmio` can't be forged or
  read out of bounds) and the MMU (a domain reaches only its own pages).
- A bug in Raven's own code can't be restarted by a supervisor: it stops the
  machine. That is one more reason to keep the trusted files few and small.
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

The trusted computing base is therefore: the compiler, Raven, and the files
under `raven/trusted/`, which are the only ones allowed `unsafe` and `extern fn`.

## 6. Raven, the kernel

- A microkernel, **written in Rook**, using `unsafe` where it must. Its Rook code
  is in `raven/` (only `raven/trusted/` may use `unsafe`); boot, the exception
  vectors and an emergency console for fault reports stay in assembly in `kernel/`.
- Responsibilities: boot, exception vectors, MMU and domain setup, scheduling actors
  across four cores (one copy of Raven per core, section 6.3), delivering messages
  (including cross-domain copies), and turning interrupts into messages.
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
- A mailbox belongs to the core its actor's domain is pinned to (section 6.3), so a
  send within a core needs no atomic instructions. Mailboxes are laid out so that a
  fast path can be added later without changing the language.
- **Possible fast path, added only if measurements justify it:** same-domain sends
  write directly into the receiver's mailbox, and call Raven only when the receiver
  is asleep and must be woken (the same trick as Linux futexes). A busy actor drains
  its whole mailbox before yielding, so the kernel is involved once per wake-up, not
  once per message. Scheduling still lives only in Raven, but the kernel no longer
  sees every message.
- **Mailboxes are bounded** (decision 112). A full mailbox never crashes the sender
  or the receiver: a send to a `reliable` handler returns an error the sender must
  handle, and a send to a `lossy` handler drops the new message. Which a handler is,
  is written in its declaration. A flood therefore can't use up the receiver's
  memory, and one slow actor can't make its senders' domains restart.
- This costs nothing in security: within a domain, the compiler already guarantees
  references can't be forged, so the kernel checking each send adds no protection.
- Measuring kernel-mediated sends against the fast path is a research result in its
  own right (milestone 3).
- **First measurement (step 3d, QEMU only).** `tests/rook/send_cost.rook` sends 1024
  messages in rounds of 16 and reads the generic timer around the sending and around
  the scheduler delivering them; the numbers are in the test log
  (`build/test-logs/rook__send_cost.*.log`). On QEMU the timer follows the host's clock,
  not the Pi's pipeline, so the values (tens of ticks of a 62.5 MHz counter per send)
  say only that nothing is badly wrong, and they vary from run to run. The real figure
  needs a board (docs/hardware.md, item 17); until then the fast-path question stays
  open.

### 6.3 Raven across cores

**There is one Raven per core** (decision 111). Each of the four cores runs its own
copy of Raven with its own run queue, mailboxes, tables, memory pool and timer. The
code running on a core never touches another core's data, so almost all of Raven is
ordinary single-threaded Rook, written the way the programs on top of it are.

- **A protection domain, and so every actor in it, belongs to one core.** Actors in
  different domains can run in parallel on different cores; actors in one domain
  share a core. A program that wants parallelism splits into domains.
- **A send to another core is a send across domains**: the message is copied and
  passed through a ring (a queue with exactly one writer core and one reader core)
  for that pair of cores, and the receiving core is woken. It is the same operation
  as a cross-domain send on one core, so its cost is the same (see the open question
  on showing that cost).
- **Only the rings are shared between cores.** Frozen data never is: a send to
  another core is a send across domains, which copies (decision 115). The rings, and
  the setup of each core, are where Raven needs `unsafe` for concurrency; none of its
  run queues, mailboxes, tables or reference counts do.
- **Accepted costs:** actors in one domain can't run in parallel with each other,
  and moving a domain to another core is not free (open below).

Chosen over one shared kernel (with lock-free queues, or with one big lock) for
fewer places where `unsafe` and memory-ordering bugs can hide (principle 1), one
kind of send instead of two (principle 6) and a smaller part of Raven to audit
(principle 7). Milestone 3 still runs on core 0 only; the point is that Raven's
tables are written as one core's own from the start, which is cheap now and costly
to retrofit.

### 6.4 Scheduling

Each core's Raven schedules the actors in its own domains (section 6.3).

- **A handler runs to completion.** Once Raven starts a handler on a core it is never
  paused; it runs on the core's own stack, so an actor with no handler running needs
  no stack at all (decision 121). Raven's interrupt code still runs at any time: it
  puts a message in the driver actor's mailbox and returns to what was running.
- **A handler has a time budget** (built in step 3e, decision 133: 100 ms for every
  handler). Raven sets a one-shot timer when a handler starts
  and clears it when the handler returns. If the timer fires first, the handler has
  failed, exactly as if it had panicked: its domain is restarted by its supervisor.
  So a handler that never returns is an ordinary failure, not a hung core.
- **Long work is written in steps**, each ending by sending a message to oneself
  (`rook.md` section 4.4).
- **Order.** Runnable actors take turns, one message per turn, with no priorities to
  begin with. Messages from one sender to one receiver arrive in the order sent
  (decision 122).
- **Why not pause handlers?** Pausing needs a stack for every actor that is partway
  through a handler, and Raven has to save and restore full register state and cope
  with an interrupt landing on any instruction: more memory, and a larger part of
  the kernel to trust (principle 7). It is not ruled out. Code written for run to
  completion stays valid if pausing is added later, so it waits until a real program
  needs it.

## 7. Supervision

Erlang-style supervision trees. Supervisors are actors that hold references to their
children and restart them (at domain granularity) when they fail. This is how the
fault-tolerance claim will be tested.

How failure and ending look to other actors (decisions 117 to 120):

- **A reference names a place, not an instance.** After a restart, references others
  hold reach the new instance, so clients that keep no state in the server carry on
  with no recovery code. The new instance starts from `new`; its mailbox starts empty
  and counts as full while it starts.
- **`watch` is how a client that does keep state finds out.** Raven sends it
  `restarted()` once the new instance is ready, and it rebuilds what the restart
  lost. Forgetting to `watch` is silent; the compiler cannot know.
- **Actors end explicitly.** An actor can `stop()` itself, and the actor that spawned
  one holds the authority to stop it: an authority of its own, separate from the
  send-only reference, so no holder of a reference can kill an actor (Erlang's `exit`
  by process id is exactly that ambient authority). Actors are never collected
  automatically: counting every reference would add a hidden cost to every send
  (principle 8), make freeing unpredictable (principle 9), and needs a cycle-handling
  protocol in the kernel (principle 7).
- A supervisor is an ordinary actor built from these pieces. How its restart policy
  is written is decided at milestone 7.

## 8. Prior art

- **Singularity / Midori** (Microsoft): safe-language OS, typed channels between processes.
- **Erlang / BEAM**: actors, supervision, "let it crash".
- **Pony**: reference capabilities, data-race freedom at compile time.
- **seL4**: capability microkernel, formally verified.
- **E language / object capabilities literature**: attenuation and revocation via proxies.
- **Akka, Pony, Orleans, Aether** (surveyed for how an actor ends, decision 119):
  Akka stops actors explicitly (`context.stop`, `PoisonPill`); Pony collects dead
  actors automatically with a reference-counting protocol; Orleans' virtual actors
  always exist logically and the runtime activates and deactivates them; Aether is
  an early-stage actor language that compiles to C, ends actors explicitly (a
  `kill(ref)` works from anywhere), and restarts a child under the same handle its
  clients hold, as decision 117 does.

## 9. Open questions

- **DMA without an IOMMU**: the approach is settled (a trusted DMA actor in Raven
  checks every address range; section 3.5). The details of its interface are decided
  at milestone 7.
- **Mailbox sizes and fairness**: bounded mailboxes and what a full one means are
  settled (decision 112). Still open: how big a mailbox is, whether an actor can ask
  for a different size, whether one sender can fill a mailbox so that every other
  sender gets `MailboxFull` (per-sender quotas), and whether a receiver is told how
  many messages it lost.
- **Scheduling policy details** (the start is settled, section 6.4): the value of the
  handler time budget, whether it can differ per actor, whether interrupt-driven
  drivers need priority over others, whether a busy actor should drain its whole
  mailbox in one turn (the fast path of section 6.2), and when pausing handlers
  becomes worth its cost. Decide with measurements, from milestone 3 on.
- **Details of one Raven per core** (the structure itself is settled, section 6.3):
  how a domain is given a core; whether a domain can ever move to another core, and
  how; per-core memory pools and how memory moves between them; which core takes
  each interrupt; how a ring wakes its reader (an inter-core interrupt or `sev`);
  how supervision works across cores. Each is decided when milestone 6 needs it.
- **Same-domain fast path**: decide after measuring at milestone 3 (section 6.2).
- **How Rook asks for a separate domain** (decision 116): decide at milestone 6, with
  the question below. The leaning is `separate` on an actor that needs its own
  domain, `spawn separate` at the spawn, and a compile error where they disagree.
- **The form of the stop authority** that `spawn` gives its caller (decision 119), and
  whether stopping an actor stops the actors it spawned. Decide at milestone 6 or 7,
  when a program first needs an actor per request.
- **How a supervisor's restart policy is written** (retry limits, what to restart
  together). Decide at milestone 7.
- **Visible cost of cross-domain sends** (principle 8): a send to an actor in
  another domain copies the message, while an identical-looking send within a
  domain doesn't. Should the difference show in the code? A send to another core is a
  cross-domain send, so it has the same cost (section 6.3). Decide by milestone 6.
