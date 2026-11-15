# Checking the protocols between cores

Every race found in milestones 6 and 7 was in the layer between cores: the rings, the wake-up of a sleeping core, the start of
an actor on another core. `tools/modelcheck.py` (decision 195) is a small explicit-state model checker, in plain Python, that
walks every reachable state of a hand-written model of such a protocol and looks for a state where an invariant breaks or
where nothing can happen and the work is not finished. It finds the shortest counterexample (`--trace` prints it).

| Model | Form | Verdict | Corresponds to |
|---|---|---|---|
| flood: core 0 sends to core 1, whose handler replies, through full rings | a sender waits for room for ever | **deadlock** (6 steps) | the first form of decision 181; found by `separate_cross_core_both_ways` |
| | a sender gives up after a while | verified (43 states) | decision 186 |
| wake: a core sleeps in `wfi`, a record arrives | nothing wakes it | **lost wake-up** (3 steps) | before decision 183: `sev` does not wake `wfi` |
| | an interrupt that arrives after the last look at the ring is lost | **lost wake-up** (3 steps) | what QEMU did about one run in thirty |
| | a pending interrupt makes `wfi` return at once | verified | the architecture, and the form of decisions 183 and 191 |
| | the above plus a 1 ms timer | verified | the current form: a missed wake costs 1 ms |
| reuse: an actor ends and a new one is given its memory while a holder keeps the old reference | a reference is just an address | **invariant broken** (3 steps) | what reuse would do today, and why actors are never freed (decision 119) |
| | a reference carries the slot's generation, checked on a send | verified | the design of the handle tables (decision 197) |
| recycle: a domain is given to a new tenant | the old tenant's translations may remain in the TLB | **invariant broken** (4 steps: a device the new tenant was never given is reached) | QEMU flushes its TLB on every TTBR0 write and cannot show it |
| | the domain's ASID is invalidated before the new tenant runs | verified | the design of domain reuse |
| retire: a domain is wiped while a holder still has the old reference | the wipe zeroes the header, which is written again afterwards | **invariant broken** (3 steps: a message is queued in a place nobody runs) | found while designing decision 197: the generation would be 0 again for a moment |
| | the generation and the ENDED flag are written first and the wipe leaves them alone | verified | the order in `retire_domain` |
| inflight: a record for the first tenant is in a ring while the domain gets a second | the record carries the address only | **invariant broken** (2 steps) | a send from a third core overtaking the end of the actor |
| | the record carries the whole reference, and the core that takes it compares the generation | verified | `handle_record` |
| start: a message can overtake the record that starts its actor | the message is handled when it arrives | **invariant broken** (4 steps) | the first form of decision 181 (a handler ran on zeroed fields) |
| | the actor is marked STARTING | verified | decision 181 |

`harness/model_check` runs all of them and fails if a form gives another verdict: the old forms must still show their bug (a
model that stops finding it is wrong) and the current forms must be verified.

**What this is not.** The models are written by hand from the code; the checker finds what a model allows, not what the
assembly does. They have two cores or three, rings of one slot and a handful of messages, which is where these bugs appear and
all a state space this size needs; memory ordering (`stlr`/`ldar`), the real ring size and the timing of a real board are not in
them. A model is evidence that the *design* has no deadlock or lost wake-up of these kinds, and that a change which brings one back
is caught; the soak (`tools/soak.py`) and the instruction-counter replay (`ICOUNT`) test the implementation.

**Adding one.** A scenario is a function from a state to its next states (`step`), a test for "the work is finished" and
optionally an invariant; add the forms with their expected verdicts to `EXPECTED`. The handle tables of the next milestone
(a generation in each reference) are to be modelled before they are built.

## Replaying a race

`ICOUNT=N python3 tests/run_tests.py TEST` runs a test under QEMU's instruction counter: the cores take turns in a fixed pattern,
so a schedule repeats exactly (the number of messages a flood drops is 17 at shift 0 on every run, and differs from run to run
without it). Another shift is another schedule. `tools/soak.py --icount 0,1,2 TEST` sweeps shifts. A failure found at a shift can
be replayed at will.

## Failing a supervised system on purpose

`inject_failures(core, first, period)` (`raven/trusted/mailbox.rook`, trusted tests only) fails the `first`-th handler a core
runs, and every `period`-th after it, as if it had panicked: reported as "injected failure", the message lost, the actor
restarted or stopped through the normal path. `trusted/fault_injection` fails every 7th handler of a counter under batches of
messages and checks, with hand-worked numbers, the restarts (3), the messages lost (9) and what the counter holds (3);
`trusted/fault_injection_domain` does it to an actor in a domain on core 1.
