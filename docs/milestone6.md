# What milestone 6 shows

Milestone 6 (protection domains, multicore) against its "done when": **actors in two domains talk across the boundary
on multiple cores, and a domain cannot read another's memory.** Everything here ran on QEMU only; nothing has run on a
board.

## The demo

`tests/rook/trusted/two_core_demo.rook` (run it with `python3 tests/run_tests.py rook/trusted/two_core_demo` after a
`make quick T="rook/trusted/two_core_demo"`). It is a test, so the output below is checked on every `make test`.

| Step | What happens | Printed |
|---|---|---|
| 1 | `ann` and `bob` are `separate` actors; Raven puts them on cores 1 and 2 (decision 180) | `1`, `2` |
| 2 | A ball is rallied ten times between them. Every pass is a message copied into a ring between the cores (decision 181), taken off by the other core and handed to a domain at EL0 after Raven's checks | `6`, `5` (hits of `ann` and `bob`) |
| 3 | `bob` is asked to read an address inside `ann`'s pool. Core 2's page tables do not map it: `bob` faults, Raven restarts his domain, his memory wiped | `Raven: panic: domain 1 faulted` (the failure report) |
| 4 | `ann` is untouched: her secret is still there and her count is unchanged. `bob`'s count is back to 0 | `4242`, `6`, `0` |
| 5 | The rally works again across the boundary after the restart | `8`, `1` |

## Against "done when"

| Claim | Shown by |
|---|---|
| Actors in two domains talk across the boundary | steps 1, 2, 5; also `separate_cross_core_order` (ten messages in order), `separate_cross_core_flood` (300 at once), `separate_cross_domain` |
| ...on multiple cores | step 1 (cores 1 and 2), `separate_placement` (1, 2, 3, 0, 1) |
| A domain cannot read another's memory | step 3 and 4; the other tests are listed in [isolation.md](isolation.md) |
| A failure on one core leaves the other alone | step 4; `separate_cross_domain` |

## What it does not show

- **A board.** Hardware items 20 to 23 in [hardware.md](hardware.md) are the assumptions this rests on (MMU and caches,
  domains, waking cores 1-3, rings between cores) and none has been checked outside QEMU. QEMU cannot show wrong ASIDs,
  cache incoherence between cores, Device ordering or a lost `sev`.
- **Everything in "What it does not guarantee" in [isolation.md](isolation.md)**: capacity, timing and side channels,
  DMA, Raven's own bugs, several actors in one domain.
- **Known weak spots in the cross-core rings** (decision 181): the line that marks a remote actor as `STARTING` has no
  deterministic test (the race it closes appeared about 1 run in 64); a `reliable` send across cores cannot say
  `MailboxFull`; a sender whose ring is full waits, so two cores flooding each other could deadlock; a `watch` on
  another core's actor is dropped by a broadcast when its watcher ends (untested); core 0's `run_forever` idles in
  `wfi` and does not wake for a ring record (fixed in 7f: a ring push to core 0 raises its mailbox interrupt, decision 183); only core 0 prints and takes interrupts.
- Plain (non-`separate`) actors all live on core 0.

Next: milestone 7's rest (drivers as actors in their own domains, interrupts as messages to driver domains, the DMA
question), which starts with a plan.
