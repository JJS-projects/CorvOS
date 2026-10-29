// rook_panic: where compiled Rook code goes when it hits a bug.
//
// Fledgling emits a call to this on integer overflow, division by zero and
// oversized shifts. For now a panic halts the core. Once actors exist, it
// will crash only the faulting actor's domain, and its supervisor restarts it
// (docs/design.md, section 7).

    .section .text

// void rook_panic(const char *message, const char *file, u64 line)  -- never returns
    .global rook_panic
rook_panic:
    mov     x19, x0
    mov     x20, x1
    mov     x21, x2

    adr     x0, msg_panic               // "Raven: panic: <message> at <file>:<line>"
    bl      uart_puts
    mov     x0, x19
    bl      uart_puts
    adr     x0, msg_at
    bl      uart_puts
    mov     x0, x20
    bl      uart_puts
    mov     x0, #':'
    bl      uart_putc
    mov     x0, x21
    bl      uart_put_dec
    adr     x0, msg_newline
    bl      uart_puts

1:  wfe
    b       1b

    .section .rodata
msg_panic:   .asciz "Raven: panic: "
msg_at:      .asciz " at "
msg_newline: .asciz "\n"
