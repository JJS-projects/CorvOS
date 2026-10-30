// Output functions for Rook test programs (linked into test images only).
//
// Every line starts with "out: " so the test harness can tell program output
// apart from kernel messages. Rook test programs get them from
// tests/rook/support/trusted/print.rook.

    .section .text

// void print_u64(u64 x)
    .global print_u64
print_u64:
    stp     x29, x30, [sp, #-32]!
    mov     x29, sp
    str     x19, [sp, #16]
    mov     x19, x0
    adr     x0, msg_out
    bl      uart_puts
    mov     x0, x19
    bl      uart_put_dec
    adr     x0, msg_newline
    bl      uart_puts
    ldr     x19, [sp, #16]
    ldp     x29, x30, [sp], #32
    ret

// void print_i64(i64 x)
    .global print_i64
print_i64:
    stp     x29, x30, [sp, #-32]!
    mov     x29, sp
    str     x19, [sp, #16]
    mov     x19, x0
    adr     x0, msg_out
    bl      uart_puts
    tbz     x19, #63, 1f                // non-negative: print as is
    mov     x0, #'-'
    bl      uart_putc
    neg     x19, x19                    // for I64 MIN this gives 2^63 unsigned: correct
1:  mov     x0, x19
    bl      uart_put_dec
    adr     x0, msg_newline
    bl      uart_puts
    ldr     x19, [sp, #16]
    ldp     x29, x30, [sp], #32
    ret

// void print_bool(bool b)
    .global print_bool
print_bool:
    stp     x29, x30, [sp, #-32]!
    mov     x29, sp
    str     x19, [sp, #16]
    and     x19, x0, #0xff
    adr     x0, msg_out
    bl      uart_puts
    adr     x0, msg_true
    cbnz    x19, 1f
    adr     x0, msg_false
1:  bl      uart_puts
    ldr     x19, [sp, #16]
    ldp     x29, x30, [sp], #32
    ret

    .section .rodata
msg_out:     .asciz "out: "
msg_newline: .asciz "\n"
msg_true:    .asciz "true\n"
msg_false:   .asciz "false\n"
