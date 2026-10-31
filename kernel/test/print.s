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

    .section .text

// bool probe_irq_registers(void): with known values in x0-x3, switches on the UART's
// interrupt line (57), spins, and says whether they are still there. The test
// that calls it has typed input and asked the UART for an interrupt, so one is taken
// during the spin: the interrupt code has to give the registers back (vectors.s).
    .global probe_irq_registers
probe_irq_registers:
    mov     w0, #0x1111
    movk    w0, #0x1111, lsl #16
    mov     w1, #0x2222
    movk    w1, #0x2222, lsl #16
    mov     w2, #0x3333
    movk    w2, #0x3333, lsl #16
    mov     w3, #0x4444
    movk    w3, #0x4444, lsl #16
    mov     w4, #0xB214                 // IRQ enable 2: the store that lets line 57 through,
    movk    w4, #0x3F00, lsl #16        // done here so the interrupt comes with the values set
    mov     w5, #1
    lsl     w5, w5, #25
    str     w5, [x4]
    mov     x4, #20000
1:  subs    x4, x4, #1
    b.ne    1b
    mov     w5, #0x1111
    movk    w5, #0x1111, lsl #16
    cmp     x0, x5
    b.ne    2f
    mov     w5, #0x2222
    movk    w5, #0x2222, lsl #16
    cmp     x1, x5
    b.ne    2f
    mov     w5, #0x3333
    movk    w5, #0x3333, lsl #16
    cmp     x2, x5
    b.ne    2f
    mov     w5, #0x4444
    movk    w5, #0x4444, lsl #16
    cmp     x3, x5
    b.ne    2f
    mov     x0, #1
    b       3f
2:  mov     x0, #0
3:  ret
