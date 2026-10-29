# kernel

Raven, the CorvOS microkernel. It also schedules actors and delivers messages; there is no separate runtime. See ../docs/design.md, section 6.

- `link.ld`: memory layout (loaded at 0x80000)
- `arch/aarch64/start.s`: boot entry, core parking, drop to EL1, BSS, stack
- `arch/aarch64/vectors.s`: exception vectors (currently: report and halt)
- `arch/aarch64/uart.s`: early PL011 console
- `main.s`: `kernel_main`, rewritten in Rook from milestone 2
