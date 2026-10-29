# Design principles

These principles decide new questions about Rook and CorvOS. They are **ordered**:
when two conflict, the higher one wins. Each one names what it costs, because a
principle that costs nothing doesn't decide anything.

They were drawn out of the decisions in [decisions.md](decisions.md) once enough
had been made to see a pattern, then extended by a survey of other systems and
actor languages (see [Sources](#sources)). When a new decision is made, the options
are checked against these first, and the decision log says which principle settled
it.

## Scope: Rook is the language for all of CorvOS

Rook is used at every level of the system: Raven, drivers, services and
applications. A feature is judged by whether *any* level of CorvOS needs it, not
just the kernel, so Rook will need things kernel code doesn't (text, collections,
richer data types, and probably floating point).

What stays confined is what's dangerous: `unsafe` and `extern fn` exist for Raven
only, so application code gets the same guarantees as everything else. As the
language grows, principle 7 still applies: a need is met by a library before a
language feature, so the compiler, part of the trusted base, stays small.

## Premise: no ambient authority, and least authority

Code can do only what it has been handed a reference to do. There are no global
names, no global mutable state, and no way to forge a reference. **And each
component is handed as little authority as will do the job**: a function that only
writes bytes takes a `ByteSink`, not the whole `Uart`.

This is the hypothesis CorvOS exists to test (see [design.md](design.md#1-goal)),
so it sits above the principles: no language convenience may undermine it.

## 1. Safe by default

The default choice is always the harmless one. Anything risky costs a visible word:
`mut`, `unsafe`, `consume`, `wrap`.

*Gives up:* some convenience.
*Seen in:* immutable declarations, overflow panics, `shl` that won't lose bits, no
truthiness, `unsafe` only in Raven, capabilities written on data.

## 2. Failure never leaves state in doubt

A function leaves only through a visible `return`, carrying either a value or an
error the caller must handle. A bug ends the whole isolated unit (the domain), so
no code ever sees half-finished state, and nothing needs cleaning up. There is
nothing in between: no exceptions, no unwinding, and panics can't be caught.

*Gives up:* the brevity of exceptions, and some error-passing boilerplate.
*Seen in:* `Result` with explicit `match` (no `?`), `discard`, overflow panics,
domains restarting as a unit.

## 3. Optimise for the reader

Prefer what is clearer to read over what is quicker to write. A line should be
understandable from the line itself and the names it uses, without remembering a
rule about its surroundings.

*Gives up:* brevity.
*Limit:* repetition that adds no information is noise. That's why `tag` is left off
actor references (they are always `tag`) and literals take their type from context.
*Seen in:* a type on every declaration, named loops, `break rows`, explicit `match`
instead of `?`, always `return`.

## 4. Forbid ambiguity instead of resolving it

When code could reasonably mean two things, make it an error, not a precedence rule
or a convention to memorise.

*Gives up:* some expressions need brackets or extra words.
*Seen in:* no shadowing, no chained comparisons, brackets required where languages
disagree about precedence, closers name a block rather than "the nearest", a
declaration can't be mistaken for an assignment.

## 5. Enforced, not advised

A rule that matters is checked by the compiler. There are no warnings, only errors,
and layout belongs to the formatter rather than to style guides.

*Gives up:* leniency while experimenting.
*Seen in:* enforced naming, required loop names, unused results are errors, tabs
are errors.

## 6. Few concepts, applied uniformly

One way to do each thing, and the same idea always takes the same shape.

*Gives up:* specialised shortcuts.
*Seen in:* one `loop` keyword, `name: type` for variables, parameters, fields,
constants, loop variables and loop labels, interfaces as the only abstraction,
`as` and `wrap` each meaning exactly one thing.

## 7. Small enough to trust

Prefer mechanisms small enough to audit, in the language, the kernel and the
toolchain. The compiler is part of the trusted base.

*Gives up:* performance and features we could borrow (LLVM's optimiser, a garbage
collector's convenience).
*Seen in:* our own code generator, ownership instead of a GC, a microkernel, no
separate runtime.

## 8. Costs are visible

Allocation, copying, messaging and blocking show up in the code. Nothing expensive
happens behind a line that looks cheap.

*Gives up:* some elegance.
*Seen in:* replies are explicit references, `consume` marks a move, messaging goes
through the kernel and is measured before it is optimised.

## 9. Predictable before fast

Prefer bounded, explainable costs over peak throughput: no pauses, no surprises,
timing that can be reasoned about.

*Gives up:* some speed.
*Seen in:* no garbage collector, refcounting only for frozen data, the
kernel-first messaging plan.

## 10. Familiar unless there's a reason

Borrow established syntax and meaning, mostly from Rust. Diverge only where a
higher principle justifies it, and record the reason.

*Gives up:* novelty for its own sake.
*Seen in:* braces, `fn`, `->`, `match`, `=>`; divergences such as `and`/`or`
(principle 4), named loops (principles 3 and 4) and `on` (readability).

## Raven: the kernel stays minimal

A concept goes into Raven only if putting it outside the kernel would make the
system's required functionality impossible (Liedtke's minimality principle for L4).
This is principle 7 applied to the kernel. It decides what is trusted: drivers,
scheduling policy and services live outside Raven unless they can't.

## Non-goal: compatibility

No compatibility promises until Rook compiles itself. Anything may change while we
learn; the decision log records what changed and why.

## Worked example

Should enum variants be written qualified (`Option.Some`) or plain (`Some`)?

- Principle 4 says that if two enums both have a `None`, a plain `None` is
  ambiguous and must not be resolved silently.
- Principle 3's limit says writing `Option.` everywhere repeats information the
  type already gives.

Answer: plain names, and a compile error when a plain name is ambiguous, where it
must then be qualified. Both principles are satisfied.

## What isn't here

Some good habits are conventions rather than principles, because they never decide
between two designs. They live elsewhere:

- **Error messages teach**: when the compiler rejects something, it says what Rook
  expects. See [rook.md](rook.md).
- **Tests must prove they can fail**, and **everything is reviewable**. See
  [workflow.md](workflow.md#practices).

## Sources

The survey that shaped principles 2, 5 and 9, least authority and the Raven
principle, and that confirmed several others:

- [Pony philosophy](https://www.ponylang.io/discover/pony-philosophy/): ordered
  priorities, each yielding to those above.
- [Introducing Austral](https://borretti.me/article/introducing-austral):
  capabilities, no implicit behaviour, "no language lawyering", mechanical checks
  over discipline.
- [Joe Duffy, The Error Model](https://joeduffyblog.com/2016/02/07/the-error-model/)
  (Midori): bugs abandon the process, errors are values, processes are the unit
  of failure.
- [Zig overview](https://ziglang.org/ko-KR/learn/overview/): no hidden control flow or
  allocations; errors are values that can't be ignored.
- [Object-capability model](https://en.wikipedia.org/wiki/Object-capability_model):
  "only connectivity begets connectivity", least authority, attenuation.
- [L4 microkernel family](https://en.wikipedia.org/wiki/L4_microkernel_family):
  Liedtke's minimality principle.
- [Ada design goals](https://pdv.cs.tu-berlin.de/Ada/lrm83/lrm-01-03.html):
  readability over ease of writing.
- [Go proverbs](https://discuss.dgraph.io/t/go-proverbs-by-rob-pike/174): "errors are values"; gofmt's
  style is no one's favourite, yet gofmt is everyone's favourite.
- [Hare as a 100-year language](https://harelang.org/blog/2023-11-08-100-year-language/):
  a stability commitment, which we deliberately don't make yet.
