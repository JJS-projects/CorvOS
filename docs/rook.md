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
    on byte(b: u8)
}

pub interface LineSink {
    on line(text: val Buffer)
}

/// Raven starts Main holding the only authority in the system.
pub actor Main {
    new(boot: val Boot) {
        uart: Uart = spawn Uart(boot.uart_regs, boot.uart_irq)
        shell: Shell = spawn Shell(uart)
        editor: LineEditor = spawn LineEditor(uart, shell as LineSink)
        uart.subscribe(editor as ByteSink)    // editor can only receive `byte`
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

    pub on subscribe(to: ByteSink) {
        .listener = Some(to)
    }

    pub on write(b: u8) {
        wait_for_space: loop (.regs.read32(FR) and TXFF) != 0 { }
        .regs.write32(DR, b as u32)
    }

    /// Raven delivers the UART interrupt as this message.
    on interrupt() {
        drain: loop (.regs.read32(FR) and RXFE) == 0 {
            b: u8 = .regs.read32(DR) wrap u8
            match .listener {
                Some(l) => l.byte(b)
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

    on byte(b: u8) {
        .out.write(b)                          // echo it back
        if b == '\n' {
            line: val Buffer = consume .buf    // freeze: iso -> val
            .buf = Buffer.new(LINE_MAX)
            .lines.line(line)
        } else {
            match append(mut .buf, b) {
                Ok(()) => { }
                Err(full) => .out.write('!')
} LineEditor

fn append(buf: mut Buffer, b: u8) -> Result<(), Full> {
    if buf.len() == buf.capacity() {
        return Err(Full)
    }
    buf.push(b)
    return Ok(())
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
| Message handlers | `on write(b: u8) { }` | `new(...)` initialises an actor; `spawn Uart(...)` creates one |
| Own fields | `.regs`, `.listener` | In actors and struct methods; `self` is the actor's own reference |
| Errors | `Result` handled with explicit `match` | No `?` shortcut |
| `match` arms | `Ok(b) => b` on separate lines | No commas |
| Naming | `snake_case` values and functions, `CamelCase` types, `SCREAMING_CASE` constants | Enforced by the compiler |
| Comments | `// note`, `/// documentation` | No block comments |
| Visibility | private by default, `pub` to export | |

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
on interrupt() {
    drain: loop (.regs.read32(FR) and RXFE) == 0 {
        b: u8 = .regs.read32(DR) wrap u8
        match .listener {
            Some(l) => l.byte(b)
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
  ones written inside it.
- **What can be stored**: a new value (a struct literal, a call's result, a
  `copy(...)`, or an `if` choosing between new values) can be stored in a variable,
  a field, or returned. A lent value can't, without `copy(...)`. A function may
  return its own local struct; it is moved out.
- **Mutability belongs to the variable**, not the fields: a `mut` variable can have
  any field changed (`a.x = 5`), and fields are never marked `mut`.
- **`==` and `!=`** compare every field.
- A struct can't contain itself by value, since it would have no fixed size.

## 4. Actors and handlers

- `actor` declares an actor type with private fields, written `name: type`, or
  `mut name: type` for fields that change after creation.
- `on` declares a **handler**: an asynchronous message handler. Calling one
  (`uart.write(b)`) sends a message and returns immediately; it has no result.
- `new(...)` runs once when the actor is created. It must set every field; fields
  without `mut` can be set only there, and only once.
- `spawn Uart(args)` creates an actor, runs its `new`, and returns a reference.
- Inside an actor, its own fields are written `.field`; `self` is a reference to
  the actor itself, for handing out (`counter.read(self as Reply<u64>)`).
- An actor processes one message at a time, so its fields never need locks.

## 5. Replies: pass a reference

There is no hidden request/response machinery. To get an answer, send a reference
the receiver can reply to, usually narrowed to an interface:

```rook
pub interface Reply<T> {
    on got(value: T)
}

actor Client: Reply<u64> {
    on start(counter: Counter) {
        counter.read(self as Reply<u64>)
    }

    on got(value: u64) {
        log(value)
    }

    on shutdown() { }         // not reachable through a Reply<u64> reference
}
```

## 6. Authority: references and interfaces

- An actor reference is the authority to send to that actor. There is no other
  way to reach an actor: no global names, no registry, no casting from integers.
- **Narrowing**: `self as Reply<u64>` gives out a reference that can only send the
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
| `val` | Frozen, immutable, may be shared | Yes, freely | Atomic refcount |
| `tag` | Identity only: send to it, never read it (actor references) | Yes | Not data |
| `mut` | Temporary mutable borrow inside one actor | No | Not owned |
| `read` | Temporary read-only borrow inside one actor | No | Not owned |

This is Pony's model cut down to fit ownership without a garbage collector. Pony's
`ref`, `box` and `trn` are not included: borrows cover `ref` and `box`, and
building an `iso` then consuming it into a `val` covers `trn`.

### 7.1 Ownership in practice

- `consume x` moves `x`; the compiler forbids using `x` afterwards.
- `v: val T = consume x`, where `x` is `iso T`, **freezes** it. Freezing applies to
  the whole structure, so frozen data has no cycles and refcounting is enough.
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
      Ok(b) => b
      Err(e) => return Err(e)
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
- **`unsafe` exists only inside Raven.**
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
- **Modules**: one file per module, explicit imports. Everything is private unless
  marked `pub`. In an object-capability system this is about code organisation,
  not security: authority comes only from references.
- **No global mutable state**: no `static mut`, no singletons. A global would be
  authority nobody was handed.
- **Naming is enforced**: `snake_case` for functions, handlers, variables and
  fields; `CamelCase` for types; `SCREAMING_CASE` for constants. Since a capitalised
  name is always a type, the parser never has to guess.
- **Comments**: `//` for notes, `///` for documentation of the item that follows.

## 11. Fledgling's subset

Fledgling (milestone 2) compiles only what is needed to write the real compiler and
the first kernel code. No generics, interfaces or actors at first; those arrive
with milestone 3. It is being built in three steps:

| Step | Adds | Status |
|---|---|---|
| Core | functions, `extern fn`, constants, declarations, integers, `bool`, `if` (also as a value), all three named `loop` forms, `break`/`continue` by name, `return`, casts, named closers, `pub` | done |
| Data (2a) | structs, methods, lending, `copy(...)` | done |
| Data (2b) | enums, `match` | next |
| Hardware | Raven's `unsafe`, raw pointers, volatile register access, byte strings | after that |

### 11.1 Rules settled while building the core

- **Integer literals take their type from context**: the declared type, the other
  operand, or the parameter they are passed to. With no context they are `u64`.
  Character literals (`'A'`, `'\n'`, `'\x41'`) are `u8` by default.
- **Converting integers**: `x as T` compiles only when every value of `x`'s type
  fits in `T` (widening, or from `bool`), so it can never lose information.
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

## 12. Open questions

- **Default capabilities**: how much can be inferred for locals, and which caps must
  always be written (fields, parameters, handler arguments)?
- **Message ordering**: guaranteed order between one sender and one receiver (as in
  Pony) is the likely answer.
- **Strings**: representation and literal types.
- **Wrapping arithmetic**: overflow panics (section 11.1); do we need explicit
  wrapping operators, and what do they look like?
- **Formatter**: a mandatory formatter (`rook fmt`) is assumed by several decisions
  (layout of braces, indentation); it arrives with the self-hosted tools.
