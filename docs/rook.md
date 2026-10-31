# Rook language

Status: agreed starting point, October 2026, with the style settled in detail
(decisions 35–63 in [decisions.md](decisions.md)), and checked against the
[design principles](principles.md). Expect small tweaks as real
code gets written.

Rook is the language for **every level of CorvOS**: the Raven kernel, drivers,
services and applications (see [principles.md](principles.md#scope-rook-is-the-language-for-all-of-corvos)).
The features below start with what the kernel needs first, but the language is
not limited to them.

## 1. A first look

A UART line editor: bytes come in from the UART, are echoed back, and complete
lines are handed to a shell.

```rook
/// A UART line editor: echoes typed bytes and hands complete lines to a shell.

LINE_MAX: u64 = 128

pub interface ByteSink {
    lossy on byte(b: u8)                // typed bytes may be dropped if the sink falls behind
}

pub interface LineSink {
    reliable on line(text: val Buffer)  // a dropped line would be a lost command
}

/// Raven starts Main holding the only authority in the system.
pub actor Main {
    new(boot: val Boot) {
        uart: Uart = spawn Uart(boot.uart_regs, boot.uart_irq)
        shell: Shell = spawn Shell(uart)
        editor: LineEditor = spawn LineEditor(uart, narrow(shell, LineSink))
        match uart.subscribe(narrow(editor, ByteSink)) {   // editor can only receive `byte`
            Delivered => { }
            MailboxFull => panic("uart mailbox full at start-up")
        }
} Main

pub actor Uart {
    regs: val Mmio                            // exactly the UART's registers
    irq: val Irq
    mut listener: Option<ByteSink>

    new(regs: val Mmio, irq: val Irq) {
        .regs = regs
        .irq = irq
        .listener = None
    }

    pub reliable on subscribe(to: ByteSink) {
        .listener = Some(value: to)
    }

    pub reliable on write(b: u8) {
        wait_for_space: loop (.regs.read32(FR) and TXFF) != 0 { }
        .regs.write32(DR, b as u32)
    }

    /// Raven delivers the UART interrupt as this message; one already queued
    /// absorbs a new one.
    lossy on interrupt() {
        drain: loop (.regs.read32(FR) and RXFE) == 0 {
            b: u8 = .regs.read32(DR) wrap u8
            match .listener {
                Some(value: l) => l.byte(b)       // lossy: nothing to handle
                None => { }
} Uart

actor LineEditor: ByteSink {
    out: Uart
    lines: LineSink
    mut buf: iso Buffer

    new(out: Uart, lines: LineSink) {
        .out = out
        .lines = lines
        .buf = Buffer.new(LINE_MAX)
    }

    lossy on byte(b: u8) {                     // as ByteSink declares it
        discard .out.write(b)                  // echo it back; a lost echo is fine
        if b == '\n' {
            line: val Buffer = freeze(.buf)    // iso -> val: shared from now on
            .buf = Buffer.new(LINE_MAX)
            match .lines.line(line) {
                Delivered => { }
                MailboxFull => discard .out.write('!')     // the shell is behind
            }
        } else {
            match append(mut .buf, b) {
                Ok(value: _) => { }
                Err(error: _) => discard .out.write('!')
} LineEditor

enum AppendError {
    Full
}

/// Adds `b` to the buffer and returns the new length.
fn append(buf: mut Buffer, b: u8) -> Result<u64, AppendError> {
    if buf.len() == buf.capacity() {
        return Err(error: Full)
    }
    buf.push(b)
    return Ok(value: buf.len())
}
```

## 2. Style at a glance

| Topic | Rook | Notes |
|---|---|---|
| Built-in types | `u8` … `u64`, `i8` … `i64`, `bool` | User types are CamelCase, so built-ins stand out |
| Statement endings | newline | No semicolons. A line continues after an operator, `,` or `as`, or inside `( )` / `[ ]` |
| Declarations | `limit: u64 = 100`, `mut count: u64 = 0` | Immutable by default. The type is always written. No shadowing |
| Constants | `PAGE_SIZE: u64 = 4096` at top level | Computed at compile time; top-level `mut` is an error |
| Functions | `fn add(a: u64, b: u64) -> u64 { return a + b }` | Results are always given with `return` |
| Choosing a value | `step: u64 = if fast { 8 } else { 1 }` | Each branch one expression; `else` required |
| Logic and bits | `and`, `or`, `xor`, `not`, `shl`, `shr` | Logical on `bool`, bitwise on integers. Brackets required where languages disagree. `shl` panics rather than lose set bits |
| Converting | `small as u64`, `reg wrap u8` | `as` only when nothing can be lost; `wrap` keeps the low bits on purpose |
| Ignoring a result | `discard checksum(buf)` | An unused result is otherwise an error |
| Loops | `rows: loop i: u64 in 0..n { }`, `waiting: loop cond { }`, `retry: loop { }` | One keyword; every loop is named; ranges exclude the end |
| Leaving loops | `break rows`, `continue rows` | Always name the loop, which can be an outer one |
| Blocks | `{ … }`, with an optional named closer `} name` | `} name` closes everything back to the function or loop with that name |
| Structs | `Point(x: 1, y: 2)` | Every field named, in declared order |
| Methods | `fn manhattan() -> u64`, `mut fn scale(by: u64)` inside the struct | Only a `mut fn` changes the struct |
| Passing structs | `show(p)`, `nudge(mut p)` | Lent, never copied; `mut` marked at the call |
| Copying | `b: Point = copy(a)` | `b: Point = a` is an error |
| Capabilities | `iso Buffer`, `val Block`, `mut Buffer`, `read Buffer` | Written before the type; omitted on actor references |
| Moving and freezing | `consume(.buf)`, `freeze(.buf)` | `freeze` turns `iso` into shared, refcounted `val` |
| Narrowing | `narrow(editor, ByteSink)` | Hands out a reference that can only send that interface's handlers |
| Message handlers | `reliable on write(b: u8) { }`, `lossy on byte(b: u8) { }` | Every handler says which; `new(...)` initialises an actor; `spawn Uart(...)` creates one |
| Sending | `discard uart.write(b)`, `match uart.write(b) { … }`, `sink.byte(b)` | A send to a `reliable` handler returns a `Sent` (`Delivered` or `MailboxFull`) that must be handled; a send to a `lossy` one returns nothing |
| Watching, stopping | `watch(out, narrow(self, Restarts))`, `stop()` | Planned (section 4.3): `restarted()` tells a client its server restarted; `stop()` ends the actor it is in |
| Own fields | `.regs`, `.listener` | In actors and struct methods; `self` is the actor's own reference |
| Errors | `Result` handled with explicit `match` | No `?` shortcut |
| Enums | `Busy(job: 3, since: now)`, `Idle` | Variant names are unique, so they're written bare |
| `match` arms | `Busy(job: j, since: _) => j` on separate lines | Every variant listed; numbers need `else` for the rest |
| Naming | `snake_case` values and functions, `CamelCase` types, `SCREAMING_CASE` constants | Enforced by the compiler |
| Comments | `// note`, `/// documentation` | No block comments |
| Visibility | private by default, `pub` to export | Fields and methods too |
| Text | `"Raven: hello\n"` | A `Bytes`: unchangeable bytes, UTF-8 by convention |
| Unchecked code | `unsafe { store32(address, value) }` | Only in files under `raven/trusted/` |
| Panicking | `panic("queue corrupted")` | A bug: ends the domain |

## 3. Declarations, functions and control flow

### 3.1 Declarations

```rook
limit: u64 = 100              // immutable
mut count: u64 = 0            // mutable
count += 1                    // assignment: no type
```

A declaration always names its type, so a declaration (`x: u64 = 1`) can never be
confused with an assignment (`x = 1`), and a typo can't silently create a new
variable. Variables must be initialised. The same `name: type` form is used for
parameters, actor fields, top-level constants and loop variables.

There is **no shadowing**: a name means one thing wherever it is visible. A
variable can't be declared twice, can't reuse a parameter's or a function's name,
and an inner block can't hide an outer variable. Sibling blocks may reuse a name,
because the first is gone before the second starts.

### 3.2 Functions and values

- `fn name(params) -> type { }`. A function with no `->` returns nothing.
- Results are always given with `return`; blocks don't produce values.
- The one exception is `if` used as a value: `if c { a } else { b }`, where each
  branch is a single expression and `else` is required. `else if` chains work.

### 3.3 Operators

`and`, `or`, `xor` and `not` are **logical on `bool`** (with `and`/`or`
short-circuiting) and **bitwise on integers**. `shl` and `shr` shift; `shr` is
arithmetic on signed types. Rook has no truthiness, so `if flags and MASK` is a type
error, not a silent bug.

Precedence, loosest first:

| Level | Operators |
|---|---|
| 1 | `or` `xor` |
| 2 | `and` |
| 3 | `not` (prefix) |
| 4 | `==` `!=` `<` `<=` `>` `>=` (can't be chained) |
| 5 | `+` `-` |
| 6 | `*` `/` `%` `shl` `shr` |
| 7 | `-` (prefix) |
| 8 | `as` |

Because `and` is looser than `==`, testing a bit needs brackets:
`(status and RXFE) != 0`. Writing `status and RXFE != 0` is a type error that the
compiler reports. Clearing a bit reads naturally: `flags and not MASK`.

Where programmers from other languages would read an expression differently, Rook
requires brackets instead of relying on the table above:

| Not allowed | Write instead | Why |
|---|---|---|
| `a or b and c` | `a or (b and c)` | Many readers don't know `and` binds tighter |
| `a or b xor c` | `(a or b) xor c` | No widely known order |
| `not a == b` | `not (a == b)` or `(not a) == b` | Python and C disagree |
| `a shl 2 + 1` | `(a shl 2) + 1` | In C and Rust this would shift by 3 |
Compound assignment exists for arithmetic only (`+=`, `-=`, `*=`, `/=`, `%=`).

### 3.4 Loops

Every loop has a name, written like a declaration in front of `loop`:

```rook
bytes: loop i: u64 in 0..len { }  // i = 0, 1, ..., len - 1; len is evaluated once
steps: loop j: i32 in -2..3 { }   // the variable's type is always written
polling: loop not done { }        // while a condition holds
idle: loop { }                    // until `break idle` (or forever)
```

`break` and `continue` always name the loop they act on, so they can reach an
outer loop directly:

```rook
rows: loop y: u64 in 0..height {
    cols: loop x: u64 in 0..width {
        if grid.at(x, y) == target {
            break rows                // leaves both loops
        }
    }
}
```

The loop variable is immutable. A label can't reuse the name of a block it's inside.

### 3.5 Blocks and named closers

Blocks use braces. A closer may name the block it closes: `} name` closes every
open block back to and including the block called `name`. Only names written in
the code can be used: a function, handler, struct or actor by its name, or a loop
by its label. Keywords (`} loop`, `} if`) are not names, so a closer is never ambiguous.
The compiler checks that the name matches an open block.

```rook
lossy on interrupt() {
    drain: loop (.regs.read32(FR) and RXFE) == 0 {
        b: u8 = .regs.read32(DR) wrap u8
        match .listener {
            Some(value: l) => l.byte(b)
            None => { }
    } drain                   // closes the match and the loop
} interrupt                   // a named closer for a single block is a checked label
```

Use a bare `}` for short blocks, and a named closer where several blocks end
together.

### 3.6 Structs and methods

A struct groups named fields. Its methods are written inside it and reach the
struct's own fields as `.field`, as handlers do in an actor. A `mut fn` may change
the struct; a plain `fn` may not.

```rook
struct Point {
    x: u64
    y: u64

    fn manhattan() -> u64 {
        return .x + .y
    }

    mut fn scale(by: u64) {
        .x *= by
        .y *= by
    }
} Point

fn nudge(p: mut Point) {
    p.x += 1
}

fn demo() {
    mut a: Point = Point(x: 1, y: 2)  // every field named, in declared order
    a.scale(3)                        // `mut fn`, so `a` must be `mut`
    nudge(mut a)                      // lent so it can change: marked at the call
    b: Point = copy(a)                // copying is always written
    print_u64(b.manhattan())          // 10
}
```

- **Building a struct**: `Point(x: 1, y: 2)` names every field once, in the order
  they are declared. Function arguments are positional; only struct fields are
  named.
- **Passing a struct lends it**: nothing is copied. A plain parameter is lent
  read-only. A `mut Point` parameter may change the caller's value, and the call
  says so: `nudge(mut a)`. Numbers can be lent the same way (`n: mut u64`).
- **Copies are explicit**: `b: Point = a` is an error, because it would silently
  copy `a`. Write `copy(a)`. Numbers are always copied, so `copy` only takes a struct.
  `copy` is built in rather than a method, so a struct's methods are exactly the
  ones written inside it. Built-ins that produce a value are all written this way,
  as calls with reserved names (`copy(a)`, and later `consume(x)`, `freeze(x)` and
  `narrow(x, Interface)`); operators and
  statement words (`as`, `wrap`, `discard`, `spawn`) are keywords.
- **What can be stored**: a new value (a struct literal, a call's result, a
  `copy(...)`, or an `if` choosing between new values) can be stored in a variable,
  a field, or returned. A lent value can't, without `copy(...)`. A function may
  return its own local struct; it is moved out.
- **Mutability belongs to the variable**, not the fields: a `mut` variable can have
  any field changed (`a.x = 5`), and fields are never marked `mut`.
- **Structs have no built-in `==`.** Write a method such as `equals` where one is
  needed, so everything a struct can do is written in it.
- A struct can't contain itself by value, since it would have no fixed size.

### 3.7 Enums and `match`

An enum's value is one of its variants. A variant may hold fields, named like a
struct's. Enums are lent, copied with `copy(...)` and given methods exactly as
structs are.

```rook
enum State {
    Idle
    Busy(job: u64, since: u64)
    Failed(code: u32)

    fn job() -> u64 {
        return match . {              // `.` alone: the method's own value
            Idle => 0
            Busy(job: job, since: _) => job
            Failed(code: code) => code as u64
        }
    }

    mut fn finish() {
        . = Idle
    }
} State

fn classify(b: u8) -> u8 {
    return match b {
        '\n', '\r' => NEWLINE
        0x20..0x7F => PRINTABLE       // a range excludes its end
        ESC => ESCAPE                 // a constant
        else => OTHER
    }
}
```

- **Variants are written bare**: `Idle`, `Busy(job: 3, since: now)`. A variant
  name belongs to one enum in the whole program, so it is never ambiguous.
- **Bindings are explicit**: a pattern names every field, in declared order, and
  binds each to a new name or ignores it with `_`. Patterns aren't nested.
- **A `match` always covers every value.** On an enum, every variant is listed
  and there is no `else`, so adding a variant makes each match that misses it an
  error. On a number, arms are values, comma lists, ranges and constants, and
  `else =>` covers the rest. On a `bool`, `true` and `false` are listed. Arms
  can't overlap, so their order never matters, and an `else` that could never
  run is an error.
- **Arms work like `if` branches.** In a match used as a value, each arm is one
  expression, or leaves the match with `return`, `break` or `continue`. In a
  match statement, each arm is one statement or a `{ block }`.
- **A binding of a struct or enum field is lent** from the value being matched.
  While it is in use, that value can't change, so the binding can never see
  another variant's data.
- **`.` on its own** is a method's whole value, in structs and enums alike.
- There is no `==` on enums: match them, or write an `equals` method.

## 4. Actors and handlers

- `actor` declares an actor type with private fields, written `name: type`, or
  `mut name: type` for fields that change after creation.
- `on` declares a **handler**: an asynchronous message handler. Calling one
  (`uart.write(b)`) sends a message and returns immediately. It never waits for
  the handler to run and has no reply (section 5), but a send can find the
  mailbox full (see below).
- **Every handler is `lossy` or `reliable`**, written before `on` (after `pub`):
  `pub reliable on write(b: u8)`, `lossy on byte(b: u8)`. There is no default. An
  interface declares it for each of its handlers, and an actor that implements
  the interface must declare the same.
- `new(...)` runs once when the actor is created. It must set every field; fields
  without `mut` can be set only there, and only once.
- `spawn Uart(args)` creates an actor, runs its `new`, and returns a reference.
- Inside an actor, its own fields are written `.field`; `self` is a reference to
  the actor itself, for handing out (`counter.read(narrow(self, Reply<u64>))`).
- An actor processes one message at a time, so its fields never need locks.

### 4.1 When a mailbox is full

Mailboxes hold a fixed number of messages, so a flood can't use up memory, and a
full mailbox never crashes the sender or the receiver.

- A send to a **`reliable`** handler returns a `Sent`, an enum with the variants
  `Delivered` and `MailboxFull` (decision 126). The compiler makes the sender handle
  it with `match`, or throw it away on purpose with `discard`. A refused message is
  not counted as a drop. The sender knows what the message is worth.
- A send to a **`lossy`** handler returns nothing. If the mailbox is full the new
  message is dropped, the messages already queued keep their order, and Raven
  counts the drop. `lossy` is the receiver's promise that its protocol copes with
  loss (input bytes, log lines, "data ready" events). The call reads like any
  other, so the interface is where to look.
- Messages Raven itself sends, such as interrupts, go to `lossy` handlers, and one
  already queued absorbs a new one.
- A request over a `lossy` handler may be dropped, and no reply will come: the
  requester needs its own way to give up.

### 4.2 What a message carries

A message holds a handler's arguments, and each one is one of these:

- **Numbers, bools, chars and actor references** travel by value. Nothing is
  written, because copying them is free of cost and of risk.
- **`val` data** (frozen) travels freely: sender and receiver both hold it.
- **A struct or enum** travels owned, and the receiver's parameter says so with
  `iso`: `lossy on add(r: iso Reading)`. A parameter with neither `iso` nor `val`
  is an error, because a function's bare `p: Point` is a read-only lend and the
  same spelling must not mean "owned" here. The sender gives the value one of
  three ways:
  - `consume(x)` gives its own away (the field or variable is empty afterwards);
  - `copy(x)` keeps its own and sends a copy; or
  - a fresh value (a literal or a call result) needs no word.
- **Borrows** (`mut`, `read`) can never be sent.

```rook
struct Reading {
    temp: i32
    seq: u32
}

actor Recorder {
    mut total: i64

    new() {
        .total = 0
    }

    lossy on add(r: iso Reading) {              // the receiver owns it now
        .total += r.temp as i64
    }
} Recorder

actor Sensor {
    recorder: Recorder
    mut latest: Reading

    new(recorder: Recorder) {
        .recorder = recorder
        .latest = Reading(temp: 0, seq: 0)
    }

    lossy on tick(temp: i32) {
        .latest = Reading(temp: temp, seq: .latest.seq + 1)
        .recorder.add(copy(.latest))            // I keep mine: the copy is written
    }

    lossy on flush() {
        .recorder.add(consume(.latest))         // I give mine away
        .latest = Reading(temp: 0, seq: 0)      // refill before the handler ends
    }

    lossy on reset(temp: i32) {
        .recorder.add(Reading(temp: temp, seq: 0))   // a new value needs no word
    }
} Sensor
```

Copying a small record at every send is the price of the rule that a copy is
always written (decision 70). If small-record sends turn out to be common, copying
plain data by itself is the first thing to reconsider (decision 114).

### 4.3 Failure, restarts and ending

*Planned (decisions 116 to 120); none of it is built before milestone 6 or 7.*

- **Domains.** Until milestone 6 every `spawn` puts the new actor in the spawner's
  domain, and Rook has no word for domains. Raven still gives untrusted code and DMA
  drivers a domain of their own. How to ask for a separate one is decided at
  milestone 6.
- **A reference survives a restart.** It names the actor's place, not one instance.
  When a failed actor's domain restarts, references others hold reach the new
  instance, which starts from `new` with an empty mailbox. While it restarts, its
  mailbox counts as full.
- **`watch` tells you.** A holder that keeps state in a server can ask to be told:
  `watch(ref, narrow(self, Restarts))`. When the target's domain restarts, Raven
  sends `restarted()` once the new instance has run `new`. A watch ends when the
  watcher itself fails, so its `new` sets it up again.
- **An actor ends by `stop()`** in one of its own handlers, or when the actor that
  spawned it uses the authority `spawn` gave it. No other holder of a reference can
  end it, and actors are never freed automatically. A send to a stopped actor
  returns an error from a `reliable` handler (`Gone`) and is dropped from a `lossy`
  one; watchers are told.

```rook
pub interface Restarts {
    lossy on restarted()       // Raven sends this when the watched actor restarts
}

actor LineEditor: ByteSink, Restarts {
    out: Uart
    lines: LineSink
    mut buf: iso Buffer

    new(out: Uart, lines: LineSink) {
        .out = out
        .lines = lines
        .buf = Buffer.new(LINE_MAX)
        watch(out, narrow(self, Restarts))     // after every field is set
    }

    lossy on restarted() {
        discard .out.subscribe(narrow(self, ByteSink))   // rebuild what the restart lost
    }
    ...
} LineEditor

actor Worker {
    reliable on run(cmd: iso Command, reply: Reply<Output>) {
        ...
        discard reply.got(output)
        stop()                                 // finished; Raven frees this actor
    }
}
```

Forgetting to `watch` is silent: the compiler cannot know which clients keep state
in a server, so a server whose interface stores a client's reference should say so.

### 4.4 How long a handler may run

*Decisions 121 and 122; the budget itself is built (step 3e, decision 133), supervision is not.*

- A handler runs from its first line to its return and is never paused. Other actors
  on the core wait their turn.
- Each handler has a **time budget**. A handler that exceeds it has failed, like a
  panic, and its domain is restarted by its supervisor. A handler that loops forever
  is therefore a failure, not a hung core.
- **Long work is written in steps**: do a bounded amount, then send a message to
  oneself for the next. A step that must not be lost is a `reliable` handler whose
  send is checked.
- **Messages from one sender to one receiver arrive in the order sent.** Messages from
  different senders may interleave in any order. A `lossy` drop removes a message but
  never reorders the ones that remain.

```rook
reliable on checksum(data: val Buffer, from_byte: u64, sum: u32) {
    to_byte: u64 = if from_byte + 4096 < data.len() { from_byte + 4096 } else { data.len() }
    mut acc: u32 = sum
    adding: loop i: u64 in from_byte..to_byte {
        acc = update(acc, data.at(i))
    }
    if to_byte < data.len() {
        match self.checksum(data, to_byte, acc) {      // the next step: others run first
            Delivered => { }
            MailboxFull => panic("checksum step lost")
        }
    } else {
        discard .reply.done(acc)
    }
}
```

## 5. Replies: pass a reference

There is no hidden request/response machinery. To get an answer, send a reference
the receiver can reply to, usually narrowed to an interface:

```rook
pub interface Reply<T> {
    reliable on got(value: T)
}

actor Client: Reply<u64> {
    reliable on start(counter: Counter) {
        match counter.read(narrow(self, Reply<u64>)) {
            Delivered => { }
            MailboxFull => log_busy()
        }
    }

    reliable on got(value: u64) {
        log(value)
    }

    reliable on shutdown() { }   // not reachable through a Reply<u64> reference
}
```

## 6. Authority: references and interfaces

- An actor reference is the authority to send to that actor. There is no other
  way to reach an actor: no global names, no registry, no casting from integers.
- **Narrowing**: `narrow(self, Reply<u64>)` gives out a reference that can only send the
  handlers in that interface. There is no downcast back, so the compiler enforces
  it at zero runtime cost.
- **Revocation** is not a language feature: a proxy actor implementing the same
  interface, which can be told to stop forwarding, is ordinary library code.

## 7. Reference capabilities

Every reference to data carries a capability saying what you may do with it and
whether anyone else can see it. The rule they enforce: **no actor ever holds a
write reference to data another actor can see.** Capabilities are written before
the type: `iso Buffer`.

| Cap | Meaning | Sendable? | Freed by |
|---|---|---|---|
| `iso` | Owned, mutable, the only reference | Yes, by `consume` (move) | Owner, at end of scope |
| `val` | Frozen, immutable, may be shared | Yes, freely | Refcount |
| `tag` | Identity only: send to it, never read it (actor references) | Yes | Not data |
| `mut` | Temporary mutable borrow inside one actor | No | Not owned |
| `read` | Temporary read-only borrow inside one actor | No | Not owned |

This is Pony's model cut down to fit ownership without a garbage collector. Pony's
`ref`, `box` and `trn` are not included: borrows cover `ref` and `box`, and
building an `iso` then freezing it into a `val` covers `trn`.

### 7.1 Ownership in practice

*Steps 4a and 4b (decisions 135 to 140) build `consume`, `iso` parameters and fields,
the move checking, and messages that carry structs and enums. `freeze` (4c) is still to
come; sections 11.12 and 11.13 say what is accepted today.*

- `consume(x)` moves `x`; the compiler forbids using `x` afterwards.
- `freeze(x)`, where `x` is `iso T`, takes it like `consume` and returns it as
  `val T`: immutable, shareable, and reference counted from then on. Freezing
  is always written, because it changes what the value costs; `consume(x)` passed
  where a `val` is needed is an error. Freezing applies to the whole structure, so
  frozen data has no cycles and refcounting is enough.
- Consuming a field leaves it empty; it must be assigned again before the handler
  ends.
- Borrows are written the same way at the call site and in the parameter:
  `append(mut .buf, b)` passes to `fn append(buf: mut Buffer, ...)`. They can't be
  stored or sent, and end when the call returns.
- Actor and interface references are always `tag`, so the cap is left off
  (`out: Uart`). Data always states its capability.

### 7.2 What this gives up

Shared mutable objects inside one actor (graphs, doubly linked lists) are awkward,
as in Rust: use indices into an arena. An opt-in refcounted local cell can be added
later if this becomes painful.

## 8. Errors

- **Expected failures** return `Result<T, E>` and are handled with an explicit
  `match`. There is no `?` shortcut, so every error path is visible:
  ```rook
  block: val Block = match disk.read(n) {
      Ok(value: b) => b
      Err(error: e) => return Err(error: e)
  }
  ```
- **Bugs** (failed bounds check, `panic`, out-of-range register access, integer
  overflow) crash the actor. Its whole domain is restarted by its supervisor
  (see [design.md](design.md#7-supervision)).

## 9. Hardware access and `unsafe`

- Hardware is reached through **capabilities**, never raw addresses. Raven hands a
  driver an `Mmio` value covering exactly its device's registers, and an `Irq`
  value that routes that interrupt to it as a message.
- `Mmio` access is bounds checked; touching anything outside the range crashes the
  driver. Ordinary drivers therefore contain **no `unsafe` code**.
- **`unsafe` exists only inside Raven**, in files under `raven/trusted/`. Anywhere
  else, `unsafe` and `extern fn` are compile errors, so the trusted base is the
  list of files in that directory.
- Inside a trusted file, the lines the compiler can't check are wrapped in
  `unsafe { }`. There, addresses are plain `u64` values, read and written with the
  built-ins `load8` to `load64` and `store8` to `store64`. Each is exactly one
  access of that width, in program order, so they work for device registers.

  ```rook
  // raven/trusted/devices.rook
  pub fn read32(offset: u64) -> u32 {
      .check32(offset)                  // panics outside the range or misaligned
      unsafe {
          return load32(.base + offset)
      }
  }
  ```
- `unsafe` builds capability types (`Mmio`, `Irq`, `Bytes`, later page tables
  and mailboxes) that check their own rules. Everything else, including most of
  Raven, uses those types safely. A struct whose fields are private can only be
  built in its own file, which is what makes these types unforgeable.
- **Trusted code runs in more than the kernel.** `Mmio.read32` is compiled into
  each driver that uses it, so its load runs in the driver's domain, on registers
  Raven has mapped there. It is safe for two reasons: the compiler (an `Mmio`
  can't be forged, and every access is bounds checked) and the MMU (a domain can
  only reach the pages mapped for it).
- **DMA-capable devices** (SD card, USB, Wi-Fi) are the exception: with no IOMMU, a
  device that can do DMA can write anywhere. Their DMA registers are never handed
  out. Drivers ask a small trusted DMA actor in Raven, which checks that every
  address range belongs to the requesting domain.

## 10. Types, modules and naming

- **Primitive types**: `u8` to `u64`, `i8` to `i64`, `bool`.
- **Structs**, and **enums** with data, matched with `match` (exhaustively checked).
- **Generics** (`Result<T, E>`, `Option<T>`, `Reply<T>`), compiled to a separate copy
  per type.
- **Interfaces** are the only abstraction mechanism: they narrow actor references
  and constrain generics. There is no separate trait system.
- **Text**: `"..."` is a `Bytes`, an unchangeable run of bytes, UTF-8 by convention.
  There is no separate text type; decoding and formatting are library code.
  `Bytes` is declared in Rook (`raven/trusted/bytes.rook`), with `len()` and a
  bounds-checked `at(i)`. Escapes are as in character literals: `\n \t \r \0 \\
  \" \' \x41`.
- **Modules**: one file per module, explicit imports. Everything is private unless
  marked `pub`, including struct fields and methods. In an object-capability system this is about code organisation,
  not security: authority comes only from references.
- **No global mutable state**: no `static mut`, no singletons. A global would be
  authority nobody was handed.
- **Naming is enforced**: `snake_case` for functions, handlers, variables and
  fields; `CamelCase` for types; `SCREAMING_CASE` for constants. Since a capitalised
  name is always a type, the parser never has to guess.
- **Comments**: `//` for notes, `///` for documentation of the item that follows.

## 11. Fledgling's subset

Fledgling (milestone 2) compiles only what is needed to write the real compiler and
the first kernel code. No generics or interfaces at first; actors arrive in
milestone 3 (steps 3a to 3c are done), the rest after. It is being built in three steps:

| Step | Adds | Status |
|---|---|---|
| Core | functions, `extern fn`, constants, declarations, integers, `bool`, `if` (also as a value), all three named `loop` forms, `break`/`continue` by name, `return`, casts, named closers, `pub` | done |
| Data (2a) | structs, methods, lending, `copy(...)` | done |
| Data (2b) | enums, `match`, `.` | done |
| Hardware (2c) | trusted files, `unsafe { }`, `load`/`store`, text, `panic`, programs of several files | done |
| Kernel (2d) | the console and `kernel_main` rewritten in Rook, with `Mmio` | done |
| Actors (3a) | `actor`, `new`, `lossy on` handlers, `spawn`, `self`, sends; Raven's mailboxes and scheduler | done |
| Ownership (4a) | `consume`, `iso` parameters and fields, move checking; no run-time change | done |
| Ownership (4b) | handlers take `iso` and `val` structs and enums; up to 8 words are copied into the message | done |
| Reliable sends (3b) | `reliable on` handlers; a send to one returns `Sent` | done |
| Interfaces (3c) | `interface`, `actor Name: Interface`, `narrow(x, Interface)` (not generic) | done |
| The echo demo (3d) | see the roadmap | planned |

### 11.1 Rules settled while building the core

- **Integer literals take their type from context**: the declared type, the other
  operand, or the parameter they are passed to. With no context they are `u64`.
  Character literals (`'A'`, `'\n'`, `'\x41'`) are `u8` by default.
- **Converting integers**: `as` is only for numbers. `x as T` compiles only when
  every value of `x`'s type fits in `T` (widening, or from `bool`), so it can
  never lose information.
  Anything that could, such as narrowing or changing signedness, is written
  `x wrap T`: it keeps the low bits and reinterprets the sign, like Rust's `as`.
  `wrap` is an error where nothing could be lost, so each word always means one
  thing. A bare literal takes `as`'s target type directly: `7 as u8`.
- **Integer overflow, division by zero and oversized shifts panic.** This applies at
  every width, and to `MIN / -1`. Constant expressions are checked at compile time.
  Explicit wrapping operators can be added later if code needs them.
- **`shl` panics rather than lose set bits.** Shifting back must give the original
  value (arithmetically, for signed types), so `x shl 1` is as safe as `x * 2`. To
  drop bits on purpose, mask them off first: `(x and 0x0F) shl 4`.
- **Nothing is thrown away silently.** `x == 2` on its own line is an error, and so
  is a call whose result isn't used. `discard f()` ignores a result on purpose.
- **`extern fn name(...)`** declares a function written in assembly. All functions
  use the AAPCS64 calling convention, so Rook and assembly call each other directly.
  At most 8 parameters for now. **Known gap:** any file can declare any symbol and
  call it, which is ambient authority. Before actors exist, `extern fn` must become
  available to Raven only (decision 60).
- **Indentation is spaces only**: a tab is an error.
- **Helpful errors for other languages' habits**: `let`, `var`, `const`, `while`,
  `for`, `&&`, `||`, `!` and C bit operators are rejected with the Rook spelling.
- **A panic** calls the kernel's `rook_panic`, which prints
  `Raven: panic: <what> at <file>:<line>` and halts. Once actors exist, it will crash
  only the actor's domain instead.

### 11.2 Rules settled while building structs

- **Lending is only partly checked.** Fledgling has no borrow checker yet. It
  catches the same value lent as `mut` and lent again in one call
  (`add_into(mut a, a)`), and lent values can't be stored. Full checking arrives
  with reference capabilities in milestone 3. **Known gap.**
- **A plain struct parameter is a read-only lend.** Section 7 calls this a `read`
  borrow. Whether `read` is ever written out is part of the open question on
  default capabilities (section 12).
- **`copy` is a keyword**, so no function, method or variable can be called that.
  A method can't share a field's name.
- **Calling convention for structs**: a struct argument is passed as the address of
  the caller's value, since it is lent. A struct result is written to an address
  the caller passes in `x8`. A method gets its struct's address in `x0` and its
  arguments from `x1`; its symbol is `Struct__method`. This departs from AAPCS64,
  which copies composite arguments. Numbers passed by value still follow AAPCS64, so
  assembly can call Rook functions that take only numbers.

### 11.3 Rules settled while building enums

- **Variant names are unique** across every enum, and can't be used for a type,
  function or constant either.
- **An arm with several variants binds nothing**: `Idle, Failed(code: _) => ...`.
  Bindings there would be ambiguous.
- **A declaration in a statement arm needs a block** (`=> { x: u64 = 1 }`), so
  its scope is visible.
- **A range can't include its type's maximum**, because its end is excluded and
  must itself be a value of the type: `0x80..0xFF, 0xFF` covers the top of a
  `u8`. Range loops have the same limit.
- **Only enums, integers and `bool` can be matched.**
- **Layout**: an enum is a tag word (the variant's position, from 0) followed by
  the variant's fields, padded with zeros to the largest variant's size.
- **`brk #0x52`** follows the arm tests, where no value can arrive because the
  checker proved every value is matched. If it ever runs, the kernel's exception
  report shows it.
- `freeze` and `narrow` are now reserved words (decisions 78 and 79).

### 11.4 Rules settled while building hardware access

- **A program is several files compiled together**:
  `fledgling a.rook b.rook --trusted raven/trusted -o out.s`. Until Rook has
  modules and `import`, every `pub` item is visible in every file. Items, fields
  and methods without `pub` are private to their file, and top-level names must
  be unique across the program. **Known gap**: no `import` yet.
- **Trusted directories are named by the build.** Raven's is `raven/trusted/`.
  The test suite adds its own trusted directories (`tests/rook/support/trusted/`,
  `tests/rook/trusted/`, `tests/rook/errors/trusted/`) for the test output
  functions and for tests of `unsafe`.
- **`extern fn` is declared only in trusted files.** A `pub extern fn` may be
  called from anywhere: declaring it is the trusted decision. The test programs
  get `print_u64` and friends this way.
- **`unsafe` blocks don't nest**, and there is no `unsafe fn` yet. It will be
  added for the few operations whose rules the caller must guarantee (switching
  page tables, contexts).
- **`panic(...)` is a statement**, usable anywhere, and ends the function's
  paths like `return`. It reports the file and line of the call. A panic inside
  `Bytes.at` reports bytes.rook's line, not the caller's.
- **`rook_panic` takes lengths**: `(message, message_length, file, file_length,
  line)`, because `Bytes` has no terminating zero.
- **Constants can be text**: `GREETING: Bytes = "Hello"`.

### 11.5 Raven's Rook code

- **Layout**: `raven/` holds Raven's Rook code and `raven/trusted/` the files
  allowed `unsafe`; `kernel/` keeps the assembly (boot, exception vectors, the
  emergency console).
- **Device authority starts in one place.** The boot code calls `raven_start` in
  `raven/trusted/devices.rook`, the only file that can make an `Mmio` from
  nothing. It makes one covering every device register and hands it to
  `kernel_main` (`raven/main.rook`), which gives each device only its own `part`.
- **The console driver is safe Rook** (`raven/pl011.rook`): it reaches the UART
  and GPIO only through the `Mmio` parts it is given.
- **Panics and fault reports use the assembly console** (`kernel/arch/aarch64/uart.s`),
  so a report never depends on the Rook code that may have failed. It relies on
  `kernel_main` having set the UART up.
- **Test programs** are compiled with all of Raven's Rook code. Their `test_main`
  may take `peripherals: Mmio`, the same root `kernel_main` has.

### 11.6 Rules settled while building actors (step 3a)

- **What is accepted.** `actor Name { fields, new(...), [pub] lossy on name(...) { } }`,
  `spawn Name(args)`, `self`, and a send `ref.handler(args)` or `self.handler(args)`.
  Generic interfaces (`Reply<T>`), `watch` and `stop` are not accepted yet. Read-only `val` fields arrived in step 3d (11.9), the handler time budget in step 3e (11.10), and interrupts as messages in step 3f (11.11).
- **Fields** hold numbers, bools, actor references and (from step 3d) read-only `val` data (11.9), are private to the actor's own
  handlers (`pub` is an error) and are changed only if written `mut`.
- **`new`** sets every field exactly once, in any order, with `=` at the top level of its
  body (not inside an `if`, loop or `match`), and cannot read a field before it is set
  or hand out `self` before every field is. An actor with fields needs one; `new` is
  not `pub` and takes at most 7 parameters.
- **A handler** says `lossy` (or `reliable`) before `on`,
  has no result, and takes at most 4 parameters, each a number, a bool or an actor
  reference. A send to a `lossy` handler returns nothing, so there is nothing to handle
  or `discard`.
  Without `pub`, a handler can be sent to only from its own file.
- **Words.** `actor`, `on`, `spawn`, `self`, `lossy` and `reliable` are keywords. `new`
  is a word only inside an actor, so a method called `new` can still be written
  elsewhere. `interface`, `consume`, `import`, `freeze` and `narrow` stay reserved.
- **An actor reference is one word**: the address of the actor's fields. Raven's
  header and the mailbox sit just below it, so handlers reach fields exactly as methods
  reach a struct's. References are copied freely, have no `==`, and are not numbers.
- **Raven's actor code** is `raven/trusted/actors.rook`: one fixed 64 KB arena, a mailbox of
  16 messages per actor (a handler number and 4 words each), a run queue, and
  `run_until_idle()`, which takes turns one message at a time. A send to a full mailbox
  is dropped and counted (`dropped_messages()`). Actors are never freed: running out
  of arena is a panic.
- **Panics.** A panic in a handler reports its line and halts the whole system, as
  any panic does for now; restarting just the actor's domain waits for milestone 7.
- **Known gaps.** No `Main`, `Boot`, `freeze` (section 11.13).

### 11.7 Rules settled while building reliable sends (step 3b)

- **`Sent`** is an ordinary enum declared in `raven/trusted/actors.rook`:
  `Delivered` and `MailboxFull`. A send to a `reliable` handler returns it, and the
  unused-result rule applies as for any call: `match` it (both variants) or write
  `discard`. A program compiled without Raven's code that sends to a `reliable`
  handler is an error that says `Sent` is missing.
- **Counting.** `raven_send` (lossy) drops a message to a full mailbox and counts it;
  `raven_send_reliable` leaves the queue alone, does not count, and returns
  `MailboxFull`. Both use one routine to place a message.
- **`match` on a call that returns nothing** says there is nothing to match.

### 11.8 Rules settled while building interfaces (step 3c)

- **Declaring.** `interface Name { [lossy|reliable] on name(params) ... }` lists
  handlers with no bodies, no result, and no `pub` (whoever holds the interface can send
  them). An interface is `pub` or private to its file like any type. Its handlers
  take the same parameter types as an actor's (numbers, bools, actor and interface
  references, at most 4).
- **Implementing.** `actor Name: Interface` (several: `actor Name: A, B`) promises
  every handler of each interface, with the same modifier and parameter types
  (names may differ). Nothing else is needed: the actor's handler need not be `pub`.
- **Narrowing.** `narrow(x, Interface)` is the only way to get an interface
  reference. It works on an actor reference (including `self`) whose actor declares the
  interface. It costs nothing: the value is the same one word. An interface
  reference is a type for fields, parameters, locals and handler arguments; an
  actor reference is never accepted in its place, and it can't be narrowed again.
- **Sending** through an interface follows the interface's modifier (decision 113):
  a `reliable` handler returns `Sent`, a `lossy` one returns nothing. Only the
  interface's own handlers can be sent; the actor's others are out of reach.
- **Handler numbers** are chosen by name, program-wide (decision 128), so a message
  carries the same number whether it was sent through an actor or an interface.
- **Not yet.** Generic interfaces (`Reply<T>`, which wait for generics), narrowing
  an interface to a smaller one, and `==` on references.

### 11.9 Rules settled while building `val` fields (step 3d)

- **`val` data in an actor.** A field or a `new` parameter of struct or enum type is
  written `val`: `regs: val Mmio`, `new(regs: val Mmio)`. The value is copied into the
  actor once, by `.regs = regs` in `new`, and is read-only afterwards: it can't be
  assigned, a part of it can't be assigned, and a `mut fn` can't be called on it. It is
  never `mut`. A bare struct field or `new` parameter is an error that says to write
  `val`.
- **Where `val` may go.** Only on an actor's fields and `new`'s parameters. On a handler
  parameter it is an error until the ownership rules arrive (decision 114): no message
  carries a struct yet. On a number or bool it is an error, since those are already
  plain values. Anywhere else (a local, a function parameter, a struct field) it is an
  error that says so.
- **Not ownership.** This is the first use of `val`, not the freezing and reference
  counting of section 7: nothing here is shared between actors, because each actor holds
  its own copy.

### 11.10 Rules settled while building the handler time budget (step 3e)

- **Every handler has 100 ms.** One number for all handlers (decision 122a), chosen as
  generous and not yet measured against real handlers. Raven arms the core's timer when
  it starts a handler and disarms it when the handler returns; an interrupt in between
  means the handler has failed.
- **The report** is the owner's choice (decision 133) and has the shape of a panic:
  `Raven: panic: Counter.spin ran for over 100 ms at counter.rook:14`. The line is where
  the handler is declared. The core halts, as for any panic, until supervision exists
  (milestone 7). A test expects it with `// expect-panic: Counter.spin ran for over 100 ms
  at counter.rook:14`.
- **A handler that waits is also over budget.** `wait_ms` inside a handler counts. Long
  work belongs in steps (section 4.4).
- **Nothing else interrupts yet.** The timer is the only interrupt source enabled.

### 11.11 Rules settled while building interrupts as messages (step 3f)

- **`Irq`** is the right to receive one peripheral interrupt (numbered 32 to 63; the
  PL011 UART is 57). Its field is private to `raven/trusted/actors.rook`, so it can't be
  written by hand; `make_irq(peripherals, 57)` makes one, and only from the whole
  peripherals `Mmio` (`is_root`), so a driver given just its registers can't give
  itself an interrupt. It is held as `irq: val Irq`.
- **Routing** (the owner's choice, decision 134): `irq.route(narrow(self, Interrupt))`,
  usually in `new` after every field is set. `Interrupt` is an interface with one
  `lossy on fired()`. From then on Raven sends `fired` to that actor when the
  interrupt occurs; one already waiting in the mailbox absorbs a new one. Routing
  again replaces the route.
- **Handlers must cope with nothing to do.** A source that is still asserting
  fires again as soon as it is switched back on, so `fired` can arrive with
  nothing left to read. `UartListener` simply finds the FIFO empty.
- **The scheduler.** `run_until_idle` also delivers interrupts that have fired, and
  returns when nothing is queued. `run_forever` never returns and sleeps the core
  (`wfi`) when nothing is queued.
- **Raven's own code** may send a message to a reference it holds as a number, with
  two built-ins allowed only inside `unsafe` in trusted files: `actor_address(r)` (the
  reference as a `u64`) and `handler_number("fired")` (the number messages use for
  handlers of that name).

### 11.12 Rules settled while building ownership (step 4a)

- **`iso`** (owned, the only reference) is written on an actor's field or a function's
  parameter of struct or enum type: `mut latest: iso Reading`, `fn keep(r: iso Reading)`.
  A plain local is owned without a word; a bare parameter is a read-only lend (unchanged).
  A `new` parameter or a handler parameter may be `iso` (or `val`, section 11.13). On a
  local, a number, or a `mut` parameter it is an error.
- **`consume(x)`** gives away an owned variable (a local, or an `iso` parameter) or a
  `mut iso` field of the actor. It is a new value nothing else holds, so it can be
  assigned, returned, or passed to an `iso` parameter. Not a lent parameter, a `val`
  field, a part of something, or a number.
- **After `consume(x)`, `x` has no value** until it is assigned again, which needs it to
  be `mut` for a variable. Using it, lending it or consuming it again is an error that
  says where it was consumed.
- **Branches and loops.** After an `if` or `match`, `x` is consumed if it was on any path
  that continues; paths that `return`, `break`, `continue` or `panic` don't count. In a
  loop, a variable from outside that is consumed must be assigned again before the end
  of the body and before any `continue`; after a `break` it stays consumed.
- **A consumed `iso` field must be assigned again** before the handler ends and before
  any `return`. `new` gives every field its first value, so it may not consume.
- **An `iso` parameter takes ownership**: the argument is a new value, `consume(x)` or
  `copy(x)` (the error names both when `consume` would be allowed). `consume(x)` to a
  parameter that only borrows is an error.
- **Messages copy small data** (decision 135, built in 4b, section 11.13).
- **No run-time cost or change.** `consume(x)` generates no code beyond reading `x`.

### 11.13 Rules settled while building messages that carry data (step 4b)

- **A handler parameter of struct or enum type** says `iso` (owned) or `val` (read-only);
  a bare one is an error. At most 4 parameters and **8 words** in all (a struct counts as
  its size; a number, bool or reference as one); more is a compile error that says how
  many.
- **The sender** gives an `iso` parameter a new value, `consume(x)` or `copy(x)`. A `val`
  parameter takes read-only data: a `val` field, a `val` parameter, or text in quotes
  (a constant of text too). An owned value can't be passed as `val` until `freeze` exists
  (step 4c). Interfaces say `iso`/`val` too, and an implementing handler must match.
- **A refused send has still consumed the value** (decision 138): `consume(r)` into a full
  mailbox returns `MailboxFull` (or drops, for `lossy`) and `r` is gone. To keep the right
  to retry, send `copy(r)`.
- **How it works.** The compiler lays the arguments out as words in the sender's frame (a
  struct or enum copied in); `raven_send(target, handler, payload, words)` copies them into
  a 9-word mailbox slot. Before a handler runs, Raven copies the payload into a message
  buffer, so a send from inside the handler can't overwrite it; the dispatcher gives each
  number its word and each struct or enum the address of its words in the buffer (as a lent
  struct is passed). A mailbox is now about 1.1 KB (was 0.7 KB), so the 64 KB arena holds
  fewer actors.
- **An `iso` or `val` parameter is read-only in the handler** (copy it into a `mut` variable
  to change it); an `iso` one can be consumed, for example into an `iso` field.

## 12. Open questions

- **Default capabilities**: settled for `iso` and `val` on fields and parameters, and
  for plain locals (owned) by decisions 130 and 136. Still open: handler arguments
  (step 4b), `val` results and locals (step 4c).
- **Message ordering**: guaranteed order between one sender and one receiver (as in
  Pony) is the likely answer.
- **Wrapping arithmetic**: overflow panics (section 11.1); do we need explicit
  wrapping operators, and what do they look like?
- **Formatter**: a mandatory formatter (`rook fmt`) is assumed by several decisions
  (layout of braces, indentation); it arrives with the self-hosted tools.
