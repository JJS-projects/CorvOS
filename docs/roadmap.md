# Roadmap

Time budget: a few hours a week, so each milestone is meant to end in something
that runs and can be shown. The order puts the first real test of the security
hypothesis (milestone 4) before self-hosting, so the idea is tested as early as possible.

## 1. Boot and UART in QEMU

- AArch64 boot code in assembly: park three cores, set up a stack, drop to EL1.
- Exception vectors.
- Write "hello" over the UART.
- Run in QEMU `raspi3b`; scripts in `tools/`.

**Done when:** `make test` boots the kernel in QEMU, sees the UART output, and
checks that a deliberate fault is reported by the exception vectors.

## 2. Fledgling, the bootstrap compiler

- Python compiler in `bootstrap/` for a minimal subset of Rook: functions, integers,
  structs, pointers, `unsafe`.
- Own AArch64 code generation, linked with the milestone 1 boot code.
- Rewrite the UART output in Rook.

**Done when:** the UART hello is written in Rook and boots in QEMU.

## 2½. Hardening, before milestone 3

Milestone 2 has only ever run in QEMU, and Fledgling will build the self-hosted
compiler at milestone 5. This step makes both more trustworthy without real
hardware, in one PR.

- **QEMU closer to the board.** Run the Rook tests on QEMU's `raspi3ap` (512 MB,
  like the Zero 2 W) as well as `raspi3b` (1 GB), and turn on QEMU's logging of
  accesses to unimplemented or misused devices (`-d unimp,guest_errors`), with
  any such report failing the test.
- **Compiler coverage.** Run the suite under a coverage tool, and add tests for
  the checker and code-generator paths no test reaches.
- **Differential testing.** Generate random arithmetic expressions over all eight
  integer types, compute the expected result (or panic) in Python, and compare
  with the compiled code running in QEMU.
- **Bring-up checklist** (`docs/hardware.md`): everything QEMU accepts that real
  hardware may not: `config.txt` (64-bit mode, PL011 on GPIO 14/15, the UART
  clock), the baud divisors, the transmit-FIFO wait QEMU can't exercise. Each is
  checked against the BCM2837 peripherals manual and known-working bare-metal
  projects.
- **A sign of life without serial.** Blink the activity LED from Rook, so a
  first boot on the board shows how far Raven got even without a serial adapter.
- **The serial test harness, ready for hardware.** Boot tests can read a serial
  port instead of QEMU's output; tested now with QEMU's serial on a
  pseudo-terminal, so the real board is a one-line swap later.

**Done when:** all of the above is merged and green in CI.

### Later: real hardware in CI

When a Pi Zero 2 W, a 3.3 V USB-serial adapter and a way to power-cycle the board
(a hub that `uhubctl` can switch, or a relay) are connected to a PC, that PC
becomes a self-hosted GitHub Actions runner. Each run powers the board off and
on, sends `kernel8.img` over USB with `rpiboot` (no SD card), and checks the
serial output with the same expectations as the QEMU tests. Because the
repository is public, the hardware job runs only for branches in this
repository, never for pull requests from forks, so nobody else can run code on
that PC.

## 3. Actors and messages

- `actor`, `spawn`, `new`, `on` handlers, mailboxes.
- Single-core cooperative scheduler in Raven (there is no separate runtime).
- Every send goes through Raven; mailboxes are lock-free queues laid out so a
  same-domain fast path can be added later.
- Ownership and moves in the compiler; freezing plus refcounted frozen data.
- Measure the cost of a kernel-mediated send.

**Done when:** two actors echo UART input to each other by message passing, and
the cost per send is measured.

## 4. Capability demo (first hypothesis test)

- Enforce unforgeable references in the compiler.
- A trusted actor holds the UART reference; an untrusted actor without it cannot
  write to the UART by any means the language allows.
- Attenuation and revocation via a proxy actor.

**Done when:** the demo runs, with a written argument for why the untrusted actor
cannot reach the UART.

## 5. Self-hosting

- Write the real compiler in Rook, in `compiler/`.
- It compiles itself; retire Fledgling (kept for history).

**Done when:** the compiler rebuilds itself and the result is identical.

## 6. Protection domains and multicore

- MMU set up per domain; cross-domain messages are copied.
- Raven's scheduler runs on all four cores.
- Decide on the same-domain fast path using the milestone 3 measurements.

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
