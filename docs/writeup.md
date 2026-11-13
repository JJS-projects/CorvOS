# What CorvOS shows, against its four claims (draft)

**Status: a draft written before any board has run.** Everything below was seen on QEMU (`raspi3b` and `raspi3ap`) or is
argued from the code; each claim says which. Milestone 8 is to boot a real Pi Zero 2 W and rewrite the "not shown" parts
with what the board did. Hardware assumptions are listed, with how sure each is, in [hardware.md](hardware.md) (items 1-26).

The hypothesis ([design.md](design.md) section 1): an OS built from actors, where an actor reference is a capability, is
better on four fronts. The sizes behind the discussion, counted on 2026-11-13: the trusted part of Raven is 1808 lines of
Rook (`raven/trusted/`, of which `mailbox.rook` is 1221) and 1960 lines of assembly (`kernel/`); the rest of Raven (the
console driver and friends) is 568 lines of Rook with no `unsafe`; the compiler is 11360 lines of Rook (it compiles itself,
twice, with identical output) with Fledgling, 5671 lines of Python, as the seed; 202 programs and 447 error tests run in QEMU.

## 1. Security: you can only use what you were handed

**Argument.** There are no global names and no way to make a reference from a number; `unsafe` and `extern fn` exist only
under `raven/trusted/` (decision 90). A reference to an actor is an address no domain can dereference, and Raven checks
that a target is an actor it made. A driver is handed an `Mmio` for exactly its device's pages (decision 182) and an
`Irq`; it is never handed a DMA device's address registers, only a buffer named as an offset into its own pool
(decision 185).

**Shown on QEMU.**
- An untrusted actor handed one `ByteSink` cannot reach the UART any other way ([capabilities.md](capabilities.md), the
  capability demo, route by route).
- A `separate` actor runs at EL0 in a page table of its own: it cannot read another domain's memory, Raven's data, a device
  it was not given, or the control page where Raven keeps its header; it cannot run its data as code or use a privileged
  instruction; a request with a forged address, target or handler number is refused ([isolation.md](isolation.md), each row
  names the test that fails if the claim breaks; `two_core_demo` is the readable one).
- A device given to a domain is reachable in its pages and nowhere else (`separate_device`, `separate_device_fault`).

**Not shown.** Anything on a board: the MMU, caches, ASIDs, Device ordering and cache coherence between cores are things
QEMU does not model (hardware.md items 20-25). Side channels and timing are not addressed at all. DMA is designed
(`dma_window` checks a buffer) but no DMA device is driven, so the claim "a driver cannot use DMA to escape" rests on the
design and one function, not on a device that has done it. The trusted base is not proved; `mailbox.rook` is 1221 lines
with 48 `unsafe` blocks, and a bug there is a hole.

## 2. Fault tolerance: failed components are restarted and the system stays up

**Argument.** A panic, a fault or a handler that runs over its 100 ms budget ends that handler; the actor restarts in place
with the same reference, a domain restarts as a unit with its memory wiped, and supervisors are ordinary actors built from
`watch`, restarts and the authority to stop ([fault-tolerance.md](fault-tolerance.md)).

**Shown on QEMU.**
- The supervisor demo restarts a crashing console within a limit and then stops it, with its clients told
  (`supervisor_demo`, and the same across a domain boundary in `separate_supervised`).
- A failure on one core leaves an actor on another core alone (`two_core_demo`, `separate_cross_domain`).
- **The milestone 7 test:** the UART driver in a domain of its own is killed in the middle of draining the receive FIFO; its
  domain restarts, the bytes still in the FIFO are echoed and the next line is too (`separate_uart_driver`, decision 184).
- A handler that never returns, also inside a call to Raven, is stopped by the budget timer (`domain_timeout*`,
  `separate_spins*`).

**Not shown.** A driver killed while a device transfer is in flight (no DMA device yet; hardware.md item 26); a restart
storm of the driver; the cost of a restart on real memory (a pool wipe is 2 MB). Raven's own bugs cannot be restarted by a
supervisor: they stop the machine. A message to an actor on another core that finds the ring full for 5 ms is dropped
(decision 186), which `lossy` allows and `reliable` reports as `MailboxFull`.

## 3. Simplicity: messages replace syscalls, threads, locks and ad hoc IPC

**Argument.** One mechanism, the message, is used for a call to a driver, an interrupt, a failure notice, a restart and a
cross-core request. A driver is an actor with no `unsafe`. There is one way to be told about failure (`watch`) and one to
stop (`stop`).

**Shown.** The UART driver is 33 lines of Rook in the test (`separate_uart_driver`) and the same code runs as a plain actor
on core 0 or in a domain on core 1 or 3 (`separate_interrupt*`), changing only the word `separate`. An interrupt is a
`fired` message (decisions 134, 183). Across cores the only shared memory is the rings (decision 181).

**Not shown, and against it.** The cost of simplicity here is concentrated in the trusted part: the rings, the wake-up of a
sleeping core (a mailbox interrupt plus a timer backstop, decision 183, found only after QEMU left a core asleep once in
thirty runs), the bounded wait (decision 186), the domain entry and exit in assembly. Four real bugs were found in the
multicore work by tests and fixed (a lost message to an actor whose `new` had not run, a deadlock between two flooding
cores, a spurious interrupt halting a core, a missed wake-up). I cannot say the whole is simple; I can say the part the
programmer meets is.

## 4. Concurrency safety: no shared mutable state, so no data races and no locks, by construction

**Argument.** An actor's fields are its own; a message carries values, `val` (read-only) or `iso` (owned) data and
references, copied between domains; a `separate` actor shares no memory with another.

**Shown.** Ownership is checked by both compilers (`error/` tests for each rule). Actors in different domains on different
cores share nothing but the rings, and a core's page tables map only its own pools. No test of Rook code needed a lock.

**Not shown, and against it.** The kernel itself shares state between cores: the rings, the global count of unfinished
messages and the per-core tables are written with atomics and acquire/release ordering written in assembly, correct on
QEMU's threads and unchecked on cores whose caches are not coherent (hardware.md items 22, 23). Races were found in this
very layer (the `STARTING` flag, the interrupt/ring wake-up). "By construction" holds for Rook programs; it does not hold
for Raven, which is where the races were.

## What I would do next

1. Boot the stages in [`tools/bringup-stages.py`](../tools/bringup-stages.py) one at a time (decision 187) and update
   hardware.md and this page with what happened.
2. A DMA actor and one DMA-capable device (the SD card or USB), which decides whether decision 185 survives contact with a
   bus address and cache maintenance.
3. Rewrite the claims with the board's numbers, and remove "draft".
