# Design principles

These principles decide new questions about Rook and CorvOS. They are **ordered**:
when two conflict, the higher one wins. Each one names what it costs, because a
principle that costs nothing doesn't decide anything.

They were drawn out of the decisions in [decisions.md](decisions.md) once enough
had been made to see a pattern. When a new decision is made, the options are
checked against these first, and the decision log says which principle settled it.

## Premise: no ambient authority

Code can do only what it has been handed a reference to do. There are no global
names, no global mutable state, and no way to forge a reference.

This is the hypothesis CorvOS exists to test (see [design.md](design.md#1-goal)),
so it sits above the principles: no language convenience may undermine it.

## 1. Safe by default

The default choice is always the harmless one. Anything risky costs a visible word:
`mut`, `unsafe`, `consume`.

*Gives up:* some convenience.
*Seen in:* immutable declarations, overflow panics, no truthiness, `unsafe` only in
Raven, capabilities written on data.

## 2. Optimise for the reader

Prefer what is clearer to read over what is quicker to write. A line should be
understandable from the line itself and the names it uses, without remembering a
rule about its surroundings.

*Gives up:* brevity.
*Limit:* repetition that adds no information is noise. That's why `tag` is left off
actor references (they are always `tag`) and literals take their type from context.
*Seen in:* a type on every declaration, named loops, `break rows`, explicit `match`
instead of `?`, always `return`.

## 3. Forbid ambiguity instead of resolving it

When code could reasonably mean two things, make it an error, not a precedence rule
or a convention to memorise.

*Gives up:* some expressions need brackets or extra words.
*Seen in:* no chained comparisons, `x and MASK != 0` is a type error, closers name a
block rather than "the nearest", a declaration can't be mistaken for an assignment,
and unused values are rejected.

## 4. Few concepts, applied uniformly

One way to do each thing, and the same idea always takes the same shape.

*Gives up:* specialised shortcuts.
*Seen in:* one `loop` keyword, `name: type` for variables, parameters, fields,
constants and loop labels, interfaces as the only abstraction, capabilities written
the same way in parameters and at the call site.

## 5. Small enough to trust

Prefer mechanisms small enough to audit, in the language, the kernel and the
toolchain. The compiler is part of the trusted base.

*Gives up:* performance and features we could borrow (LLVM's optimiser, a garbage
collector's convenience).
*Seen in:* our own code generator, ownership instead of a GC, a microkernel, no
separate runtime.

## 6. Costs are visible

Allocation, copying, messaging and blocking show up in the code. Nothing expensive
happens behind a line that looks cheap.

*Gives up:* some elegance.
*Seen in:* replies are explicit references, `consume` marks a move, messaging goes
through the kernel and is measured before it is optimised.

## 7. Familiar unless there's a reason

Borrow established syntax and meaning, mostly from Rust. Diverge only where a
higher principle justifies it, and record the reason.

*Gives up:* novelty for its own sake.
*Seen in:* braces, `fn`, `->`, `match`, `=>`; divergences such as `and`/`or`
(principle 3), named loops (principles 2 and 3) and `on` (readability).

## Worked example

Should enum variants be written qualified (`Option.Some`) or plain (`Some`)?

- Principle 3 says that if two enums both have a `None`, a plain `None` is
  ambiguous and must not be resolved silently.
- Principle 2's limit says writing `Option.` everywhere repeats information the
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
