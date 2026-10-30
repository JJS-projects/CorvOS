// Small CPU helpers Rook can't express, declared in raven/trusted/assembly.rook.

    .section .text

// u64 current_el(void): the exception level we are running at (0-3).
    .global current_el
current_el:
    mrs     x0, CurrentEL
    ubfx    x0, x0, #2, #2              // CurrentEL keeps the level in bits 3:2
    ret

// u64 read_counter(void): the ARM generic timer's physical count. start.s lets
// EL1 read it (CNTHCTL_EL2). It ticks counter_frequency() times a second.
    .global read_counter
read_counter:
    mrs     x0, cntpct_el0
    ret

// u64 counter_frequency(void): ticks per second of read_counter(), as the
// firmware set it (CNTFRQ_EL0); 0 if the firmware didn't.
    .global counter_frequency
counter_frequency:
    mrs     x0, cntfrq_el0
    ret
