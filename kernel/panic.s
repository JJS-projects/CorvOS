// rook_panic: where compiled Rook code goes when it hits a bug.
//
// Fledgling emits a call to this on integer overflow, division by zero and
// oversized shifts. A panic outside any handler halts the core. A panic inside a
// handler abandons that handler and Raven restarts the actor (decision 117; see
// actor_failed in actors.s and restart_actor in raven/trusted/mailbox.rook).

    .section .text

// void rook_panic(const u8 *message, u64 message_length,
//                 const u8 *file, u64 file_length, u64 line)  -- never returns
// (a panic inside a handler abandons that handler, see actor_failed in actors.s)
//
// Messages are Rook `Bytes` (address and length, no terminating zero).
    .global rook_panic_impl             // reached through the stub of that name (rtstubs.s)
rook_panic_impl:
    msr     cntp_ctl_el0, xzr           // disarm the handler budget timer: this is the report
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
    b       actor_failed                // abandons the handler that panicked, if one is running;
                                        // otherwise halts, as a panic always did

// void domain_report_panic(const u8 *message, u64 message_length, const u8 *file, u64 file_length, u64 line):
// prints what rook_panic prints, for a panic in a protection domain, and returns (Raven goes on). Unlike
// rook_panic it gives back the registers it uses.
    .global domain_report_panic
domain_report_panic:
    stp     x29, x30, [sp, #-96]!
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]
    str     x25, [sp, #64]
    mov     x19, x0
    mov     x20, x1
    mov     x21, x2
    mov     x22, x3
    mov     x23, x4
    adr     x0, msg_panic
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
    ldr     x25, [sp, #64]
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #96
    ret

// void domain_report(const Bytes *message): "Raven: panic: <message>\n", for a domain that failed.
    .global domain_report
domain_report:
    stp     x29, x30, [sp, #-64]!
    stp     x19, x20, [sp, #16]
    stp     x24, x25, [sp, #32]         // put_bytes uses these
    mov     x19, x0
    adr     x0, msg_panic
    bl      uart_puts
    ldp     x0, x1, [x19]               // a Bytes value is (address, length)
    bl      put_bytes
    adr     x0, msg_newline
    bl      uart_puts
    ldp     x24, x25, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #64
    ret

// void rook_budget_panic(const Bytes *name, u64 budget_ms, const Bytes *file, u64 line)
//   -- never returns
//
// A handler ran over its time budget (called from raven_budget_overrun, in
// raven/trusted/actors.rook). Prints the same shape as rook_panic:
//   Raven: panic: Counter.spin ran for over 100 ms at counter.rook:14
    .global rook_budget_panic
rook_budget_panic:
    mov     x19, x0                     // "Actor.handler" as a Bytes value
    mov     x20, x1                     // the budget in ms
    mov     x21, x2                     // the file name, a Bytes value
    mov     x23, x3                     // the line

    adr     x0, msg_panic
    bl      uart_puts
    ldp     x0, x1, [x19]               // a Bytes value is (address, length)
    bl      put_bytes
    adr     x0, msg_ran
    bl      uart_puts
    mov     x0, x20
    bl      uart_put_dec
    adr     x0, msg_ms_at
    bl      uart_puts
    ldp     x0, x1, [x21]
    bl      put_bytes
    mov     x0, #':'
    bl      uart_putc
    mov     x0, x23
    bl      uart_put_dec
    adr     x0, msg_newline
    bl      uart_puts
    b       actor_failed

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
msg_ran:     .asciz " ran for over "
msg_ms_at:   .asciz " ms at "
msg_newline: .asciz "\n"
