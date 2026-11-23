# kernel

Raven, the CorvOS microkernel. It also schedules actors and delivers messages; there is no separate runtime. See ../docs/design.md, section 6.

- `link.ld`: memory layout (loaded at 0x80000, the board's `KERNEL_START`)
- `board/pizero2w/board.inc`: the board's addresses (interrupt controller, local peripherals, UART, spin table, RAM), pulled in by the `.s` files with `.include "board.inc"`; `make BOARD=...` picks another directory (decision 222, ../docs/porting.md)
- `arch/aarch64/start.s`: boot entry, core parking, drop to EL1, BSS, stack
- `arch/aarch64/vectors.s`: exception vectors (currently: report and halt)
- `arch/aarch64/uart.s`: early PL011 console
- `main.s`: `kernel_main`, rewritten in Rook later in milestone 2
- `panic.s`: `rook_panic`, called by compiled Rook code on overflow and similar bugs
- `test/print.s`: output functions for Rook test programs (test images only)
