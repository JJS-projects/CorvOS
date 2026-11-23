# Porting CorvOS

What a new board, a new architecture or a new compiler backend must supply, and what
Raven and Rook assume of each. The plan that fills this page is
[porting-plan.md](porting-plan.md) (decision 220); this is its skeleton (phase 0), and each
section says which phase writes it.

Today there is one of each: the Raspberry Pi Zero 2 W (BCM2837) board, the AArch64
architecture, and an AArch64 backend in both compilers. Everything runs in QEMU's
`raspi3b` and `raspi3ap` machines ([hardware.md](hardware.md) for the real board).

## The layers

| Layer | Where it lives (after the plan) | Changes for |
|---|---|---|
| Board | `raven/trusted/board.rook`, `kernel/board/$(BOARD)/` | another board with the same architecture |
| Architecture | `kernel/arch/$(ARCH)/`, the externs of `raven/trusted/assembly.rook`, `raven/trusted/arch.rook` | another instruction set |
| Backend | `target_$(ARCH)` in each compiler | another instruction set |
| Harness | the architecture table in `tests/run_tests.py` and the Makefile | another QEMU binary, machine or cross toolchain |

Raven's Rook code above these layers is the same on every port.

## The board contract

*Written in phase 1* (1.1, decision 221, is done for Rook; the assembly half is 1.2 and
1.3). The facts a board supplies, and where:

- in Rook, `raven/trusted/board.rook`, public constants (numbers only; decision 221):

  | Name | What it is | Pi Zero 2 W |
  |---|---|---|
  | `PERIPHERALS_BASE`, `PERIPHERALS_SIZE` | the device registers, as the MMU-off physical range the root `Mmio` covers | `0x3F000000`, 16 MB |
  | `UART0_OFFSET`, `GPIO_OFFSET` | where the console UART (a PL011) and the GPIO block sit in the peripherals | `0x201000`, `0x200000` |
  | `DMA_BUS_ALIAS` | added to a physical RAM address to give a device's address for it | `0xC0000000` (uncached alias) |
  | `RAM_BYTES` | the RAM every supported board has (no Rook code reads it yet) | 512 MB |
  | `REGION_BASE`, `REGION_SIZE` | the batch-job region (`region.rook`), inside RAM, clear of the kernel image and the GPU's part | 256 MB at 64 MB |
  | `COUNTER_DEFAULT_HZ` | the counter's rate if the firmware leaves it unset | 19.2 MHz |

  `harness/board_numbers` fails if `board.rook` lacks one of these, or if one of its numbers is
  written as a literal in another of Raven's Rook files. `0x200000` is exempt by value: it is
  also the size of a 2 MB pool. The compiler programs and the tests still write the UART's
  offset themselves (see the plan);
- in assembly, `kernel/board/$(BOARD)/board.inc`: the interrupt-controller, mailbox and
  spin-table addresses, and the macros for the board's interrupt read and acknowledge,
  the mailbox write that interrupts another core, releasing the secondary cores, and
  setting up interrupts.

## The architecture contract

*Written in phase 2.*

### The externs

Each of the 68 `extern fn`s of `raven/trusted/assembly.rook`, by group, with its
contract and its callers. Groups: domain entry and exit; address spaces; cores; atomics
and ordering; time; board.

### What Raven assumes of an architecture

Each assumption as a requirement, then how AArch64 meets it: word size and byte order;
page and pool sizes and alignment; argument registers; no stale translation after a
domain switch; the orderings between cores (a ring slot before its tail, a domain's
state by release and acquire, a ring record before the interrupt that announces it,
atomic add). The board facts of [hardware.md](hardware.md) items 22, 23, 27–29 and 32,
restated the same way.

## The backend contract

*Written in phase 4 ([backend.md](backend.md) once the owner has agreed it).* The
register roles (accumulator, arguments, indirect result, scratch, frame, link and stack)
and the operations a code generator asks a target for, each with its Rook meaning,
including the checked arithmetic that panics on overflow.

## What a port must supply

*Completed as the phases land.* For a new architecture, today's expectation (the end of
[porting-plan.md](porting-plan.md)):

1. a target in each compiler (`target_$(ARCH)`), meeting the backend contract;
2. `kernel/arch/$(ARCH)/`, meeting the architecture contract;
3. a board, meeting the board contract;
4. a row in the harness's architecture table.

A port is checked by the same tests as AArch64; none is written yet.
