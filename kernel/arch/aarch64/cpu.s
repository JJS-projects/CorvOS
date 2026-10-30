// Small CPU helpers Rook can't express, declared in raven/trusted/assembly.rook.

    .section .text

// u64 current_el(void): the exception level we are running at (0-3).
    .global current_el
current_el:
    mrs     x0, CurrentEL
    ubfx    x0, x0, #2, #2              // CurrentEL keeps the level in bits 3:2
    ret
