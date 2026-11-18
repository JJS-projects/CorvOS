# What CorvOS shows, against its four claims (draft)

**Status: a draft written before any board has run.** Everything below was seen on QEMU (`raspi3b` and `raspi3ap`) or is
argued from the code; each claim says which. Booting a real Pi Zero 2 W and rewriting the "not shown" parts with what the
board did is the owner's step ([`tools/bringup-stages.py`](../tools/bringup-stages.py), decision 187); nothing in milestones 8
and 9 has run there either. Hardware assumptions are listed, with how sure each is, in [hardware.md](hardware.md) (items 1-29).

The hypothesis ([design.md](design.md) section 1): an OS built from actors, where an actor reference is a capability, is
better on four fronts. The sizes behind the discussion, counted on 2026-11-13: the trusted part of Raven is 1808 lines of
Rook (`raven/trusted/`, of which `mailbox.rook` is 1221) and 1960 lines of assembly (`kernel/`); the rest of Raven (the
console driver and friends) is 568 lines of Rook with no `unsafe`; the compiler is 11360 lines of Rook (it compiles itself,
twice, with identical output) with Fledgling, 5779 lines of Python, as the seed; 255 programs and 442 error programs run in QEMU.

## 1. Security: you can only use what you were handed

**Argument.** There are no global names and no way to make a reference from a number; `unsafe` and `extern fn` exist only
under `raven/trusted/` (decision 90). A reference to an actor is an address no domain can dereference, and Raven checks
that a target is an actor it made; since milestone 8 it is also a generation, so a reference to an actor that has ended
never reaches whatever is later put in its place (decision 197). A driver is handed an `Mmio` for exactly its device's pages (decision 182) and an
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
- **A stale reference reaches nothing (milestones 8c, 9).** An ended actor's memory and a `separate` actor's whole domain are
  given back and used again; a reference to the earlier tenant, a handle to its domain, and the authority of a DMA service
  made for it, are all refused afterwards, also when the new tenant is at the very same address (`handle_*`,
  `domain_stale_handle`, `dma_stale_owner`, `domain_fast_recycled`, `domain_spawn_stale`).
- **Authority is a value, and a limit is one too.** A `Timer` (decision 196), a `Domain` (decisions 201 to 206: whoever holds it
  can place actors in that domain and nobody else can) and a quota (`Metered`, a library proxy actor: decision 198) are
  capabilities made from the peripherals or from Raven, handed out like references. An actor in a domain cannot hand a device to
  one it places (`domain_spawn_devices`), nor place one in a domain it does not hold (`separate_spawns`).
- **Raven does not trust what a domain says about itself.** The runtime that schedules the actors of a domain runs *in* the
  domain (decision 203); Raven clamps the two counts it reads from its memory to what it handed over, so a domain that lies
  about them cannot make the count of unfinished messages wrap or make Raven write outside a ring (`domain_liar`, the model
  `inbound`). Every number a domain gives Raven, in its memory or in a request, is bounded before Raven does arithmetic on it, so a
  lie fails the domain and cannot panic Raven (decision 208, `domain_forged_spawn_words`, `domain_liar_restart`,
  `domain_liar_queue`); before decision 208 three such numbers could, which an outside review found, not a test.

**Not shown, and against it.** Actors that share a domain share everything the domain has (decision 205): the registers of a
device given to one are reachable by the others, and a DMA service made for one can move data over the fields of another. That is what
sharing a domain means, and it is the author's choice to make it; the language does not warn of it. Anything on a board: the MMU, caches, ASIDs, Device ordering and cache coherence between cores are things
QEMU does not model (hardware.md items 20-25). Side channels and timing are not addressed at all. DMA is built for a copy inside a
driver's pool (`DmaService`, `dma_window`; a driver's requests for the control page, past the pool, overlapping or empty are
refused, and two drivers' services touch only their own pools, on QEMU's DMA controller); no real device is driven, so the
claim "a driver cannot use DMA to escape" rests on that service and the design, not on a device that has done it, and
cache maintenance and the bus alias are unchecked on a board. The trusted base is not proved; `mailbox.rook` is 2477 lines
with 103 `unsafe` blocks (133 in `raven/trusted/` as a whole), and a bug there is a hole. It has doubled since the first draft:
handles with generations, timers, shared domains and the runtime in the domain were all added to it.

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
- A driver that fails on every interrupt, restarting at full speed, is stopped by its spawner and the system carries on
  (`separate_driver_storm`, decision 190).
- A handler that never returns, also inside a call to Raven, is stopped by the budget timer (`domain_timeout*`,
  `separate_spins*`).
- **Timers and a watchdog (milestone 8b, decision 196):** a timer is a message, and a watchdog (a library actor) tells its owner when it has not
  been fed for its time, so that the owner can restart what stopped answering (`watchdog_demo`, `timer_deadman`). A supervisor's restart budget is a library (`budget_policy`, decision 193).
- **A domain with several actors fails and restarts as one unit (decisions 201, 202, 206):** every actor's `new` runs again on the
  arguments kept for it, so every reference stays valid, also that of an actor another actor placed
  (`domain_fail_together`, `domain_spawn_restart`, `domain_spawn_crash`). A domain that is given back is wiped and used again
  (`separate_recycle*`), so a system that restarts for ever no longer runs out of domains: each slot hands out about four billion
  generations before it is retired (decision 209; until then the 65,536th tenant of a slot made Raven panic, `domain_tenant_65536`).
- **Finding the faults before the field did:** a model checker for the protocols between cores (`python3 tools/modelcheck.py`: nine
  scenarios, each with its expected verdict), replayable schedules (`ICOUNT`) and fault injection in the harness (decision 195)
  found or confirmed bugs that a plain run would show one time in thirty. The accounting of unfinished messages was wrong at least three
  times in milestone 9 (a message counted as over when its record was taken, a count that reached zero while a domain restarted,
  messages the fast path put in a mailbox counted as finished) and each was found by a test, not by thought.

**Not shown.** A restart that runs in the same moments as a spawn into the same domain (the table of places is written by one core
and read by another, decision 202: not tested); one race of milestone 9 that showed once in about ten runs under load and not
again, and for which there is no deterministic test (decision 206, the last row of that PR's bug checks). A driver killed while a device transfer is in flight (the DMA service copies inside a driver's pool, tested
on QEMU's DMA controller, decision 189; the case of a transfer to a device is not built; hardware.md item 26); the cost of a restart on real memory (a pool wipe is 2 MB). Raven's own bugs cannot be restarted by a
supervisor: they stop the machine. A message to an actor on another core that finds the ring full for 5 ms is dropped
(decision 186), which `lossy` allows and `reliable` reports as `MailboxFull`.

## 3. Simplicity: messages replace syscalls, threads, locks and ad hoc IPC

**Argument.** One mechanism, the message, is used for a call to a driver, an interrupt, a failure notice, a restart and a
cross-core request. A driver is an actor with no `unsafe`. There is one way to be told about failure (`watch`) and one to
stop (`stop`).

**Shown.** The same mechanism now carries more: a timer, a quota, a placement and a reclaim are each a message or a capability, not a
new kind of call (decisions 196 to 206). A send between two actors of one domain is put in the receiver's mailbox without a call to
Raven, and measured on QEMU it is about 1.8 times as fast as one to another domain (decision 204, `domain_send_cost`: the host's
clock, not a Pi's, and including the scheduling of each round). The UART driver is about 25 lines of Rook in the test (`separate_uart_driver`) and the same code runs as a plain actor
on core 0 or in a domain on core 1 or 3 (`separate_interrupt*`), changing only the word `separate`. An interrupt is a
`fired` message (decisions 134, 183). Across cores the only shared memory is the rings (decision 181).

**Not shown, and against it.** The cost of simplicity here is concentrated in the trusted part: the rings, the wake-up of a
sleeping core (a mailbox interrupt plus a timer backstop, decision 183, found only after QEMU left a core asleep once in
thirty runs), the bounded wait (decision 186), the domain entry and exit in assembly. Four real bugs were found in the
multicore work by tests and fixed (a lost message to an actor whose `new` had not run, a deadlock between two flooding
cores, a spurious interrupt halting a core, a missed wake-up). Milestones 8 and 9 added to it: the actors' runtime inside a domain is 229
lines of Rook that run at EL0, and Raven keeps a second set of per-domain counters to hold the runtime's word to account. The fast path is a
performance gain, which the roadmap counts against the design, and it is the part I would remove first if simplicity had to be bought back (two
short routines in `rtstubs.s` and `domain_runtime.rook`). I cannot say the whole is simple; I can say the part the programmer meets is, and that
the programmer now meets a few more words (`Domain`, `Timer`, `spawn ... in`) than in the first draft.

## 4. Concurrency safety: no shared mutable state, so no data races and no locks, by construction

**Argument.** An actor's fields are its own; a message carries values, `val` (read-only) or `iso` (owned) data and
references, copied between domains; a `separate` actor shares no memory with another.

**Shown.** Ownership is checked by both compilers (`error/` tests for each rule). Actors in different domains on different
cores share nothing but the rings, and a core's page tables map only its own pools. No test of Rook code needed a lock. Several actors
in one domain do share a pool, but they run one at a time on one core under one runtime, and a domain restarts as a unit, so what they share
is still not shared between two threads of control. Generic actors and interfaces (decision 200) and a quota proxy (`Metered`) are
written in Rook with no `unsafe`.

**Not shown, and against it.** The kernel itself shares state between cores: the rings, the global count of unfinished
messages and the per-core tables are written with atomics and acquire/release ordering written in assembly, correct on
QEMU's threads and unchecked on cores whose caches are not coherent (hardware.md items 22, 23). Races were found in this
very layer (the `STARTING` flag, the interrupt/ring wake-up, and in milestone 9 the count of unfinished messages when a domain
has messages for its own actors). "By construction" holds for Rook programs; it does not hold for Raven, which is where the races
were, and the number of places a core can be wrong about another has grown with the domain runtime.

## What I would do next

1. Boot the stages in [`tools/bringup-stages.py`](../tools/bringup-stages.py) one at a time (decision 187) and update
   hardware.md (items 1-29; 27 and 28 are the page remap and the TLB invalidation of the milestone 8 and 9 domains, 29 what a domain's core and core 0 write into one pool) and this page
   with what happened.
2. A DMA actor and one DMA-capable device (the SD card or USB), which decides whether decision 185 survives contact with a
   bus address and cache maintenance.
3. Rewrite the claims with the board's numbers, and remove "draft". Measure the fast path of decision 204 on the board before
   keeping it.
4. What milestone 9 did not build: a domain actor that places actors in another domain, and `watch` and `stop` from a domain (decision
   206), a lock on the table of places, and a test that finds the one race left open.
