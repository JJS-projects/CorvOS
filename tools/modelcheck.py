#!/usr/bin/env python3
"""A small explicit-state model checker for the protocols between Raven's cores (decision 195).

    python3 tools/modelcheck.py [--trace]

Each scenario is a handful of cores, rings and flags, written as a function from a state to its possible next states; the
checker walks every reachable state (breadth first, so a counterexample is the shortest) and looks for a state that breaks
an invariant or a state where nothing can happen and the work is not finished (a deadlock or a lost wake-up). The
scenarios are the ones that produced real bugs in milestones 6 and 7, each in the form it had and in the form it has now:

  flood   core 0 sends to core 1, whose handler sends a reply back, through rings of limited size.
          `unbounded`: a sender waits for room for ever (the first form: it deadlocked, decision 186).
          `bounded`:   a sender gives up after a while (the current form).
  wake    core 0 sends one message to a core that idles in `wfi`.
          `none`:        nothing wakes the core (the form before decision 183: `sev` does not wake `wfi`).
          `ipi_misses`:  an interrupt that arrives between the last look at the ring and the `wfi` is lost (what QEMU did).
          `ipi`:         a pending interrupt makes `wfi` return at once (the architecture).
          `ipi_timer`:   as `ipi_misses`, plus a timer that wakes the core within 1 ms (the current form).
  start   a message for an actor can reach its core before the record that starts the actor (two rings are not ordered).
          `no_flag`: the message is handled when it arrives (the first form of decision 181).
          `flag`:    the actor is marked STARTING: the message waits for the start (the current form).

`EXPECTED` says which form must be verified and which must show a counterexample. The tool exits 1 if a result differs: a
model that no longer finds the old bug, or finds one in the current design, is wrong or the design changed. It checks models
that I wrote by hand from the code: it finds what the models allow, not what the assembly does (decision 195 says so).
"""
import sys
from collections import deque

CAP = 1         # slots in a ring: the smallest size that shows the problems (the real rings hold 32)


def explore(init, step, finished, invariant=None, limit=200000):
    """Breadth-first search. Returns ("ok", states) or (kind, trace) for the shortest path to a violation: kind is
    "invariant", or "stuck" for a state with no step that is not a finished one."""
    seen = {init: None}
    queue = deque([init])
    while queue:
        state = queue.popleft()
        if invariant is not None and not invariant(state):
            return "invariant", trace(seen, state)
        nexts = list(step(state))
        if not nexts and not finished(state):
            return "stuck", trace(seen, state)
        for label, nxt in nexts:
            if nxt not in seen:
                seen[nxt] = (state, label)
                queue.append(nxt)
        if len(seen) > limit:
            raise RuntimeError("the state space is larger than expected")
    return "ok", len(seen)


def trace(seen, state):
    path = []
    while seen[state] is not None:
        state, label = seen[state]
        path.append(label)
    return list(reversed(path))


# ---- flood: two cores, two rings -------------------------------------------------------------------------------------

def flood(mode, messages=4):
    # state: (sent, pc0, ring01, ring10, owe, drops)   pc0: "send" | "drain"
    # Core 0 sends `messages` messages to core 1 in a loop that does not drain its own ring (test_main's loop), then drains
    # until its replies have come. Core 1 takes a message from its ring, then owes a reply, which it pushes into its ring to
    # core 0. A push waits while the ring is full; in `bounded` mode it may give up (the message is dropped).
    def step(s):
        sent, pc0, r01, r10, owe, drops = s
        if pc0 == "send":
            if sent == messages:
                yield "core 0 starts draining", (sent, "drain", r01, r10, owe, drops)
            elif r01 < CAP:
                yield "core 0 pushes a message", (sent + 1, pc0, r01 + 1, r10, owe, drops)
            elif mode == "bounded":
                yield "core 0 gives up waiting for room", (sent + 1, pc0, r01, r10, owe, drops + 1)
        else:
            if r10 > 0:
                yield "core 0 takes a reply", (sent, pc0, r01, r10 - 1, owe, drops)
        if owe == 0:
            if r01 > 0:
                yield "core 1 takes a message", (sent, pc0, r01 - 1, r10, 1, drops)
        else:
            if r10 < CAP:
                yield "core 1 pushes its reply", (sent, pc0, r01, r10 + 1, 0, drops)
            elif mode == "bounded":
                yield "core 1 gives up waiting for room", (sent, pc0, r01, r10, 0, drops + 1)

    def finished(s):
        sent, pc0, r01, r10, owe, drops = s
        return pc0 == "drain" and r01 == 0 and r10 == 0 and owe == 0

    return explore((0, "send", 0, 0, 0, 0), step, finished)


# ---- wake: a core that sleeps in wfi -------------------------------------------------------------------------------------

def wake(mode):
    # state: (pushed, ring, pc1, ipi, handled)   pc1: "poll" | "checked" | "asleep"
    # `ipi` is the pending interrupt (a flag the push sets and the wake clears). In `ipi_misses` and `ipi_timer` the wfi does
    # not see an interrupt that became pending after the last look at the ring (the QEMU quirk), only one that arrives
    # while the core is already asleep.
    def step(s):
        pushed, ring, pc1, ipi, handled = s
        if not pushed:
            yield "core 0 pushes a record", (1, 1, pc1, 1 if mode != "none" else 0, handled)
        if handled:
            return
        if pc1 == "poll":
            if ring:
                yield "core 1 takes the record", (pushed, 0, pc1, ipi, 1)
            else:
                yield "core 1 looks at the ring: empty", (pushed, ring, "checked", 0 if mode == "ipi" else ipi, handled)
        elif pc1 == "checked":
            if mode == "ipi" and ipi:
                yield "wfi returns at once: an interrupt is pending", (pushed, ring, "poll", 0, handled)
            else:
                yield "core 1 executes wfi", (pushed, ring, "asleep", 0 if mode != "ipi" else ipi, handled)
        else:
            if mode == "ipi" and ipi:
                yield "the pending interrupt wakes core 1", (pushed, ring, "poll", 0, handled)
            if mode in ("ipi_misses", "ipi_timer") and pushed and ipi:
                yield "an interrupt wakes core 1", (pushed, ring, "poll", 0, handled)
            if mode == "ipi_timer":
                yield "the 1 ms timer wakes core 1", (pushed, ring, "poll", ipi, handled)

    # In `ipi_misses` a push while core 1 is asleep wakes it (the edge arrives); a push earlier is lost: the flag set by a push
    # when core 1 is not asleep is cleared when core 1 goes to sleep (modelled in "executes wfi" above, which drops `ipi`).
    def finished(s):
        return s[4] == 1

    return explore((0, 0, "poll", 0, 0), step, finished)


# ---- start: a message can overtake the record that starts its actor -----------------------------------------------------------

def start(mode):
    # state: (stage0, ring01, ring21, scan, started, mailbox, ran)
    # Core 0 pushes the start record into its ring to core 1 and then lets core 2 push a message for the actor into core 2's
    # ring to core 1. Core 1 scans its rings in the order 0, 2 once (a drain pass): what arrives after it looked at a ring
    # waits for the next pass. A handler may only run once the actor has been started.
    def step(s):
        stage0, r01, r21, scan, started, mailbox, ran = s
        if stage0 == 0:
            yield "core 0 pushes the start record", (1, 1, r21, scan, started, mailbox, ran)
        if stage0 == 1:
            yield "core 2 pushes the message", (2, r01, 1, scan, started, mailbox, ran)
        if scan == 0:
            if r01:
                yield "core 1 takes the start record and runs `new`", (stage0, 0, r21, 2, 1, mailbox, ran)
            else:
                yield "core 1 finds no record from core 0", (stage0, r01, r21, 2, started, mailbox, ran)
        elif scan == 2:
            if r21:
                if mode == "no_flag":
                    yield "core 1 takes the message and runs the handler", (stage0, r01, 0, 3, started, mailbox, 1)
                else:
                    yield "core 1 takes the message: it waits in the mailbox", (stage0, r01, 0, 3, started, 1, ran)
            else:
                yield "core 1 finds no record from core 2", (stage0, r01, r21, 3, started, mailbox, ran)
        else:
            if r01:
                yield "core 1 (next pass) takes the start record and runs `new`", (stage0, 0, r21, 3, 1, mailbox, ran)
            if r21:
                if mode == "no_flag":
                    yield "core 1 (next pass) takes the message and runs the handler", (stage0, r01, 0, 3, started, mailbox, 1)
                else:
                    yield "core 1 (next pass) takes the message: it waits in the mailbox", (stage0, r01, 0, 3, started, 1, ran)
            if mailbox and started and not ran:
                yield "core 1 runs the handler from the mailbox", (stage0, r01, r21, 3, started, 0, 1)

    def finished(s):
        stage0, r01, r21, scan, started, mailbox, ran = s
        return stage0 == 2 and r01 == 0 and r21 == 0 and started == 1 and mailbox == 0 and ran == 1

    def invariant(s):
        return not (s[6] == 1 and s[4] == 0)        # a handler ran before the actor was started

    return explore((0, 0, 0, 0, 0, 0, 0), step, finished, invariant)


# What each form must give: "ok" (verified) or the kind of counterexample ("stuck", "invariant").
EXPECTED = {
    ("flood", "unbounded"): "stuck",
    ("flood", "bounded"): "ok",
    ("wake", "none"): "stuck",
    ("wake", "ipi_misses"): "stuck",
    ("wake", "ipi"): "ok",
    ("wake", "ipi_timer"): "ok",
    ("start", "no_flag"): "invariant",
    ("start", "flag"): "ok",
}
SCENARIOS = {"flood": flood, "wake": wake, "start": start}


def run_all():
    results = {}
    for (name, mode), want in EXPECTED.items():
        kind, detail = SCENARIOS[name](mode)
        results[(name, mode)] = (kind, detail)
    return results


def main(argv):
    show_trace = "--trace" in argv
    bad = 0
    for (name, mode), (kind, detail) in run_all().items():
        want = EXPECTED[(name, mode)]
        verdict = "verified" if kind == "ok" else f"counterexample ({kind}, {len(detail)} steps)"
        flag = "" if kind == want else "   <-- NOT AS EXPECTED"
        bad += kind != want
        extra = f" in {detail} states" if kind == "ok" else ""
        print(f"{name:6s} {mode:11s} {verdict}{extra}{flag}")
        if show_trace and kind != "ok":
            for step_label in detail:
                print(f"           {step_label}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
