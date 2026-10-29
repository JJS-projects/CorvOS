# Roadmap

Time budget: a few hours a week, so each milestone is meant to end in something
that runs and can be shown. The order puts the first real test of the security
hypothesis (milestone 4) before self-hosting, so the idea is tested as early as possible.

## 1. Boot and UART in QEMU

- AArch64 boot code in assembly: park three cores, set up a stack, drop to EL1.
- Exception vectors.
- Write "hello" over the UART.
- Run in QEMU `raspi3b`; scripts in `tools/`.

**Done when:** `tools/run-qemu` prints over the serial console.

## 2. Bootstrap compiler

- Python compiler in `bootstrap/` for a minimal subset: functions, integers,
  structs, pointers, `unsafe`.
- Own AArch64 code generation, linked with the milestone 1 boot code.
- Rewrite the UART output in the language.

**Done when:** the UART hello is written in the language and boots in QEMU.

## 3. Actors and messages

- `actor`, `spawn`, `send`, behaviours, mailboxes.
- Single-core cooperative scheduler in `runtime/`.
- Ownership and moves in the compiler; freezing plus refcounted frozen data.

**Done when:** two actors echo UART input to each other by message passing.

## 4. Capability demo (first hypothesis test)

- Enforce unforgeable references in the compiler.
- A trusted actor holds the UART reference; an untrusted actor without it cannot
  write to the UART by any means the language allows.
- Attenuation and revocation via a proxy actor.

**Done when:** the demo runs, with a written argument for why the untrusted actor
cannot reach the UART.

## 5. Self-hosting

- Write the real compiler in the language, in `compiler/`.
- It compiles itself; retire the Python bootstrap (kept for history).

**Done when:** the compiler rebuilds itself and the result is identical.

## 6. Protection domains and multicore

- MMU set up per domain; cross-domain messages are copied.
- Scheduler runs on all four cores.

**Done when:** actors in two domains talk across the boundary on multiple cores,
and a domain cannot read another's memory.

## 7. Drivers as actors, and supervision

- Interrupts delivered as messages to driver actors in their own domains.
- Supervision trees; a domain restarts as a unit.
- Settle the DMA question (see design.md, section 9).

**Done when:** killing a driver domain mid-use restarts it and the system carries on.

## 8. Real hardware and write-up

- Boot on a real Pi Zero 2 W from an SD card.
- Write up what was learned against all four claims: security, fault tolerance,
  simplicity, concurrency safety.
