# What a protection domain does and does not guarantee

Milestone 6c. A `separate actor` (decision 172) runs in a protection domain: EL0, its own page tables and ASID, a
private 2 MB pool (decisions 174 to 176). This page lists what that guarantees, which mechanism provides it, and which
test fails if it breaks. Everything here ran on QEMU only; the last section says what QEMU cannot show.

## What a domain cannot do

| A domain cannot... | Because | Tests that fail if it can |
|---|---|---|
| read or write another domain's memory | its page table has no entry for another pool | `separate_isolation_other_domain`, `domain_fault_other_domain` |
| read or write Raven's data (the actor arena, the tables) | the kernel's data and bss are not in its table | `separate_isolation_raven`, `domain_fault_raven_memory` |
| reach a device | the peripherals are not in its table | `separate_isolation_raven` (a write to the UART) |
| read or change Raven's record of the actor (header, mailbox) | the control page is mapped for EL1 only | `domain_fault_control_page`, `domain_fault_tramp` |
| change the kernel's code or constants | they are mapped read-only | `domain_fault_code_write`, `mmu_code_read_only`, `mmu_constants_read_only` |
| run its own data as code | the pool is never executable (execute-never bit, and WXN) | `domain_no_exec_data` (both removed together), `mmu_ram_not_executable` |
| use a privileged instruction | it runs at EL0 | `domain_privileged` |
| keep the CPU | the handler budget timer ends the run (100 ms), also when it runs out while Raven serves a request (decision 177) | `domain_timeout`, `separate_spins`, `domain_timeout_before_entry`, `domain_timeout_in_service`, `separate_spins_in_calls` |
| run off the end of its stack unnoticed | a page below the stack is not mapped | `domain_stack_overflow` |
| ask Raven for something it should not | every address, target and handler number in a request is checked | `separate_forged_requests` |
| leave anything behind after a failure | a restart wipes its stack and data | `separate_restart_wipes`, `domain_wipe_data` |
| disturb another domain by failing | a failure is a reason Raven sees; the others' memory is untouched | `separate_cross_domain`, `separate_isolation_other_domain` |

A reference to an actor is an address no domain can dereference (its table does not map it) and Rook has no way to make
one from a number; a domain can only name an actor it was handed, and Raven checks that the target is an actor it made
(`valid_actor`), so a forged reference is refused.

## What it does not guarantee

- **Delivery.** A domain can fill another actor's mailbox with messages (a mailbox is bounded, decision 112: a `lossy`
  send is dropped, a `reliable` one is told `MailboxFull`). Isolation is of memory and failure, not of capacity.
- **Timing and side channels.** Domains share the scheduler, the caches and the timer; nothing here addresses speculative
  execution, cache timing or covert channels through run-queue order.
- **DMA.** No domain has a device mapped, so none can program one; drivers in their own domains and the DMA question
  (design.md section 9) are milestone 7.
- **Raven itself.** Raven is trusted and runs at EL1. A bug in `mailbox.rook`'s checks is a hole; they are small (about
  200 lines) and each has a test, but they are not proved.
- **Several actors in one domain.** There is one actor per domain for now (nothing can `spawn` inside a domain).

## What QEMU cannot show

QEMU flushes its TLB on every write to TTBR0, so a wrong ASID or a missing non-global bit is invisible to it (a mutation
that gave every domain ASID 0 changed nothing a test can see). It does not model cache coherency between EL0 and EL1
accesses, Device-memory ordering, or the cost of a table switch. `docs/hardware.md` items 20 and 21 say what to try first
if a board misbehaves.
