# Rook language

Status: agreed starting point, October 2026. Syntax details are expected to move
while Fledgling is being written; the semantics below are the important part.
Decisions are recorded in [decisions.md](decisions.md) (19 to 25).

## 1. A first look

A UART line editor: bytes come in from the UART, are echoed back, and complete
lines are handed to a shell.

```rook
// echo.rook

interface ByteSink {
    be byte(b: U8)
}

interface LineSink {
    be line(text: Buffer val)
}

// The only place authority comes from. Raven starts Main with a Boot value
// holding capabilities for the hardware; everything else is handed down from here.
actor Main {
    be start(boot: Boot val) {
        let uart   = spawn Uart(regs: boot.uart_regs, irq: boot.uart_irq)
        let shell  = spawn Shell(out: uart)
        let editor = spawn LineEditor(out: uart, lines: shell as LineSink)
        uart.subscribe(editor as ByteSink)   // editor gets only `byte`
    }
}

actor Uart {
    let regs: Mmio val   // covers exactly the UART's registers, nothing else
    let irq: Irq val     // Raven sends this actor `interrupt` when it fires
    var listener: Option<ByteSink tag> = None

    be subscribe(to: ByteSink tag) {
        listener = Some(to)
    }

    be write(b: U8) {
        while regs.read32(FR) & TXFF != 0 {}
        regs.write32(DR, b as U32)
    }

    be interrupt() {
        while regs.read32(FR) & RXFE == 0 {
            let b = regs.read32(DR) as U8
            match listener {
                Some(l) => l.byte(b),
                None    => {},
            }
        }
    }
}

actor LineEditor: ByteSink {
    let out: Uart              // actor references are always `tag`
    let lines: LineSink tag
    var buf: Buffer iso = Buffer.new(128)

    be byte(b: U8) {
        out.write(b)                              // echo it back
        if b == '\n' {
            let line: Buffer val = consume buf    // freeze: iso -> val
            buf = Buffer.new(128)                 // field must be refilled
            lines.line(line)
        } else {
            match append(mut buf, b) {
                Ok(())  => {},
                Err(e)  => out.write('!'),        // expected failure: handled
            }
        }
    }
}

fn append(buf: mut Buffer, b: U8): Result<(), Full> {
    if buf.len() == buf.capacity() {
        return Err(Full)
    }
    buf.push(b)
    Ok(())
}
```

## 2. Actors and behaviours

- `actor` declares an actor type with private fields (`let` immutable, `var` mutable).
- `be` declares a **behaviour**: an asynchronous message handler. Calling one
  (`out.write(b)`) sends a message and returns immediately; it has no return value.
- `fn` declares an ordinary synchronous function.
- `spawn T(field: value, ...)` creates an actor and returns a reference to it.
- An actor processes one message at a time, so its fields never need locks.

## 3. Replies: pass a reference

There is no hidden request/response machinery. To get an answer, send a reference
the receiver can reply to, usually narrowed to an interface:

```rook
interface Reply<T> {
    be got(value: T)
}

actor Client: Reply<U64> {
    be start(counter: Counter) {
        counter.read(self as Reply<U64>)
    }

    be got(value: U64) {
        log(value)
    }

    be shutdown() { ... }   // not reachable through a Reply<U64> reference
}
```

## 4. Authority: references and interfaces

- An actor reference is the authority to send to that actor. There is no other
  way to reach an actor: no global names, no registry, no casting from integers.
- **Narrowing**: `self as Reply<U64>` gives out a reference that can only send the
  behaviours in that interface. There is no downcast back, so the compiler enforces
  it at zero runtime cost.
- **Revocation** is not a language feature: a proxy actor implementing the same
  interface, which can be told to stop forwarding, is ordinary library code.

## 5. Reference capabilities

Every reference to data carries a capability saying what you may do with it and
whether anyone else can see it. The rule they enforce: **no actor ever holds a
write reference to data another actor can see.**

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

### 5.1 Ownership in practice

- `consume x` moves `x`; the compiler forbids using `x` afterwards.
- `let v: T val = consume x` where `x` is `T iso` **freezes** it. Freezing applies to
  the whole structure, so frozen data has no cycles and refcounting is enough.
- Consuming a field leaves it empty; it must be assigned again before the behaviour
  ends.
- Borrows are written at the call site (`append(mut buf, b)`), cannot be stored or
  sent, and end when the call returns.
- Actor types are always `tag`, so the cap can be left off (`out: Uart`).

### 5.2 What this gives up

Shared mutable objects inside one actor (graphs, doubly linked lists) are awkward,
as in Rust: use indices into an arena. An opt-in refcounted local cell can be added
later if this becomes painful.

## 6. Errors

- **Expected failures** return `Result<T, E>` and are handled with `match`.
- **Bugs** (failed bounds check, `panic`, out-of-range register access, integer
  overflow) crash the actor. Its whole domain is restarted by its supervisor
  (see [design.md](design.md#7-supervision)).

## 7. Hardware access and `unsafe`

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

## 8. Types and modules

- **Primitive types**: `U8` to `U64`, `I8` to `I64`, `Bool`.
- **Structs**, and **enums** with data, matched with `match` (exhaustively checked).
- **Generics** (`Result<T, E>`, `Option<T>`, `Reply<T>`), compiled to a separate copy
  per type.
- **Interfaces** are the only abstraction mechanism: they narrow actor references
  and constrain generics. There is no separate trait system.
- **Modules**: one file per module, explicit imports.
- **No global mutable state**: no `static mut`, no singletons. A global would be
  authority nobody was handed.

## 9. Fledgling's subset

Fledgling (milestone 2) compiles only what is needed to write the real compiler and
the first kernel code. No generics, interfaces or actors at first; those arrive
with milestone 3. It is being built in three steps:

| Step | Adds | Status |
|---|---|---|
| Core | functions, `extern fn`, `const`, `let`/`var`, integers, `Bool`, `if`/`else`, `while`, `break`/`continue`, `return`, casts | done |
| Data | structs, enums, `match` | next |
| Hardware | Raven's `unsafe`, raw pointers, volatile register access, byte strings | after that |

### 9.1 Rules settled while building the core

- **No semicolons.** A newline ends a statement, except inside `( )` or `[ ]`, or
  after a token that can't end an expression (a binary operator, `=`, `,`, `as`).
  So a long expression is split by ending the line with an operator.
- **Variables must be initialised**: `let x: U8 = 0`, never `let x: U8`.
- **Integer literals take their type from context**: the declared type, the other
  operand, or the parameter they are passed to. With no context they are `U64`.
  Character literals (`'A'`, `'\n'`, `'\x41'`) are `U8` by default.
- **`as` converts between integer types (and from `Bool`)** by truncating or
  extending, like Rust's `as`. It never panics.
- **Integer overflow, division by zero and oversized shifts panic.** This applies at
  every width, and to `MIN / -1`. Constant expressions are checked at compile time.
  Explicit wrapping operators can be added later if code needs them.
- **`!` is logical not on `Bool` and bitwise not on integers.** There is no `~`.
- **Operators follow Rust's precedence**, so `x & MASK != 0` means `(x & MASK) != 0`.
  Comparisons can't be chained (`a < b < c` is an error).
- **A statement can't throw a value away**: `x == 2` on its own line is an error.
  Only calls may stand alone.
- **`const NAME: Type = expr`** declares a typed compile-time constant, computed from
  literals and earlier constants.
- **`extern fn name(...)`** declares a function written in assembly. All functions
  use the AAPCS64 calling convention, so Rook and assembly call each other directly.
  At most 8 parameters for now.
- **A panic** calls the kernel's `rook_panic`, which prints
  `Raven: panic: <what> at <file>:<line>` and halts. Once actors exist, it will crash
  only the actor's domain instead.

## 10. Open questions

- **Default capabilities**: how much can be inferred for locals, and which caps must
  always be written (fields, parameters, behaviour arguments)?
- **Message ordering**: guaranteed order between one sender and one receiver (as in
  Pony) is the likely answer.
- **Strings and characters**: representation and literal types (`'\n'` above is a `U8`
  for now).
- **Wrapping arithmetic**: overflow panics (section 9.1); do we need explicit
  wrapping operators, and what do they look like?
- **Constructors**: field-initialiser `spawn` as above, or Pony-style `new` methods?
