// rook_panic: where compiled Rook code goes when it hits a bug.
//
// Fledgling emits a call to this on integer overflow, division by zero and
// oversized shifts. For now a panic halts the core. Once actors exist, it
// will crash only the faulting actor's domain, and its supervisor restarts it
// (docs/design.md, section 7).

    .section .text

// void rook_panic(const u8 *message, u64 message_length,
//                 const u8 *file, u64 file_length, u64 line)  -- never returns
//
// Messages are Rook `Bytes` (address and length, no terminating zero).
    .global rook_panic
rook_panic:
    mov     x19, x0                     // message
    mov     x20, x1                     // message length
    mov     x21, x2                     // file
    mov     x22, x3                     // file length
    mov     x23, x4                     // line

    adr     x0, msg_panic               // "Raven: panic: <message> at <file>:<line>"
    bl      uart_puts
    mov     x0, x19
    mov     x1, x20
    bl      put_bytes
    adr     x0, msg_at
    bl      uart_puts
    mov     x0, x21
    mov     x1, x22
    bl      put_bytes
    mov     x0, #':'
    bl      uart_putc
    mov     x0, x23
    bl      uart_put_dec
    adr     x0, msg_newline
    bl      uart_puts

1:  wfe
    b       1b

// put_bytes(const u8 *bytes, u64 length): write `length` bytes to the UART.
// Uses x24 and x25, which rook_panic never needs back.
put_bytes:
    stp     x29, x30, [sp, #-16]!
    mov     x24, x0
    add     x25, x0, x1                 // one past the last byte
2:  cmp     x24, x25
    b.hs    3f
    ldrb    w0, [x24], #1
    bl      uart_putc
    b       2b
3:  ldp     x29, x30, [sp], #16
    ret

    .section .rodata
msg_panic:   .asciz "Raven: panic: "
msg_at:      .asciz " at "
msg_newline: .asciz "\n"
