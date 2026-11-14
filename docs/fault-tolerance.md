# Fault tolerance in Raven and Rook

How a failure is contained, repaired and reported (milestone 7, decisions 116 to 120 and
147 to 151). Everything here runs in QEMU only; actors share one address space until milestone 6
(no MMU), so containment is by the language, not the hardware. The runnable example is
`tests/rook/supervisor_demo.rook`.

## The pieces

| Piece | What it does | Where |
|---|---|---|
| Failure | A `panic`, or a handler running past its 100 ms budget, abandons the handler | decisions 121, 133 |
| Restart | Raven restarts the failed actor at once: fields cleared, mailbox emptied (the loss is counted), `new` run again on its kept arguments. References stay valid. Children it made in `new` restart in place; those made in a handler end | 148 |
| `watch(actor, narrow(self, I))` | The watcher is sent `restarted()` after the actor's `new` has run again. If `I` also has `lossy on stopped()`, it is sent `stopped()` when the actor ends | 149, 150 |
| `stop()` / `stop(ref)` | An actor ends itself, or the actor that spawned `ref` ends it. No one else can | 147, 150 |
| `Gone` | A `reliable` send to an ended actor answers `Gone` (not `MailboxFull`) | 150 |
| `lost_messages()`, `restart_count()` | Public counters | 148 |

Raven's rule is only "restart it". Everything else (how often, how many times, what to do
when it keeps failing) is ordinary Rook code, so the policy can be read and changed without
touching the kernel (decision 151, principle 7).

## What a client does

- **Keeps no state in the server:** nothing. A reference names the server's place, so after a restart
  the next send reaches the new instance.
- **Keeps state in the server** (a subscription, a setting): `watch` it in `new` and rebuild in
  `restarted()`. Forgetting is silent; the compiler cannot know which clients keep state.
- **Wants to know if the server is gone:** declare `lossy on stopped()` too. Sends to it answer
  `Gone`, and a client that gets `Gone` should give up, not retry.

## Writing a supervisor

A supervisor is an actor that spawns the child in its `new` (so it is the only one who can stop
it), watches it, and decides. The demo's `Guard`:

```rook
actor Guard: Lifecycle {
    console: Console
    limit: u64
    mut restarts: u64

    new(limit: u64) {
        .limit = limit
        .restarts = 0
        .console = spawn Console()
        watch(.console, narrow(self, Lifecycle))
    }

    lossy on restarted() {
        .restarts += 1
        if .restarts > .limit {
            stop(.console)            // give up; its watchers are told `stopped`
        }
    }
    lossy on stopped() { }
}
```

- **Policy is code.** "Give up after N restarts" is the `if`. Other policies (restart everything
  together, stop the parent, tell an operator) are other bodies.
- **One child per interface.** `restarted()` carries no argument (the owner's choice, decision 149).
  A supervisor of several children gives each a small interface of its own, or a small watcher actor,
  so it knows which one failed. Rook has no generics, so a supervisor is written per child type.
- **A tree** is a supervisor whose child is another supervisor: children made in `new` restart
  in place with their parent, so a failure of the upper one rebuilds the whole subtree, and an
  ended child is brought back with it.
- **Escalating** is a supervisor calling `stop()` on itself: its children end with it and its
  own watchers are told.

## Limits, said plainly

- **Counts can undercount.** A notice already waiting stands for a second one, so two children
  restarting through one interface before the watcher runs count once (`rook/watch_absorbed`). A count
  is a safety valve against a crash loop, not an exact log. There is no time window: "N restarts"
  means since the supervisor itself last started (a supervisor restart resets its count).
- **14 watches** share one table for the whole system; a 15th panics (decision 149).
- **A stack that runs out is a panic.** Every compiled function checks, as it begins, that its frame stays above
  a limit 8 KB over the bottom of the 256 KB boot stack, and panics `stack overflow` if not (decision 164;
  `rook/stack_overflow_panics`, `rook/stack_overflow_actor`). In a handler that is a restart like any other. Code
  that is not compiled Rook (assembly, interrupt entry) is not checked; the 8 KB left are for it and for the report.
- **No isolation yet.** A failed handler's damage to memory through `unsafe` code is not undone
  (milestone 6 adds the MMU), and a panic outside any handler, or a CPU fault, still halts.
- **Restarts are not free.** A failed actor loses its mailbox (counted), and an actor that fails
  every time restarts at full speed until its supervisor stops it.
- **A handler-spawned child ends when its parent restarts** and its place stays as a tombstone
  that answers `Gone`; the 64 KB actor arena has no reuse yet (decision 148).
- **Not tested on hardware.** The demo is a QEMU program; killing a real driver mid-use needs the
  drivers-as-actors work of milestone 7's other half and a Pi.

## A driver in a domain (milestone 7g)

`tests/rook/separate_uart_driver.rook`: the UART driver is a `separate` actor that holds the UART's registers and takes its interrupt. It is killed in the middle of draining the receive FIFO; its domain restarts as a unit, its `new` switches the receive interrupt on again, and the bytes still in the FIFO and a second line are echoed. Raven's own console is a separate, polled one, so the report of the driver's failure does not depend on the driver (decision 184).

`tests/rook/separate_driver_storm.rook` (decision 190): the other way a driver can fail, again and again. A driver that panics on every interrupt restarts as fast as its core allows; its spawner, which holds the authority to stop it, counts the restarts and stops it, and the system carries on. The number of failures is not fixed (at least the guard's limit plus one), and the guard writes only after being told `stopped`, because the failure reports of one core and the output of another are not serialised.

## A restart budget (decision 193)

`RestartBudget` (raven/restart_budget.rook) is the policy a supervisor can use instead of counting restarts for ever: it holds up to `capacity` restarts and gains one every `ticks_per_restart` ticks of the clock. In `restarted()` the supervisor asks `.budget.allow(read_counter())` and stops the actor when the answer is false; a driver that fails once a day is never stopped, one that fails every millisecond is stopped after `capacity` failures. See `tests/rook/separate_driver_storm.rook` for a guard that uses it and `tests/rook/budget_policy.rook` for the arithmetic.
