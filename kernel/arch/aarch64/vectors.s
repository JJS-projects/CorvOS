// EL1 exception vector table.
//
// Nothing is handled yet: every exception prints a report over the UART and
// halts the core, so that faults are never silent. Real handlers (interrupts
// delivered as messages, actor faults) arrive in later milestones.

    .section .text
    .balign 2048                        // VBAR_EL1 requires 2 KiB alignment
    .global vectors
vectors:
    .irp kind, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
    .balign 0x80                        // each entry is 32 instructions
    .if \kind == 5
    b       irq_entry                   // IRQ at EL1 using SP_EL1: the only one in use;
                                        // it must not disturb x0, so no `mov` first
    .else
    mov     x0, #\kind
    b       exception_report
    .endif
    .endr

// An IRQ at EL1. The physical timer, armed while a handler runs (decision 133): if
// it has fired, the handler has run over its time budget and Raven reports it as a
// failure, and does not return. Anything else is a peripheral interrupt, below.
irq_entry:
    stp     x0, x1, [sp, #-16]!         // the interrupted code's registers: the overrun
    stp     x2, x3, [sp, #-16]!         // path never returns, the other restores them
    mrs     x1, cntp_ctl_el0
    tbz     x1, #2, 1f                  // ISTATUS (bit 2): the timer condition is met
    msr     cntp_ctl_el0, xzr           // disarm, so it can't fire again while we report
    isb
    bl      raven_budget_overrun        // Rook (raven/trusted/actors.rook)

// Not the timer: a peripheral interrupt (the UART, for now). It must not touch the
// mailboxes, because it can land in the middle of Raven changing them. It only
// notes which sources fired in the pending word of the actor arena (offset 48) and
// switches those sources off at the controller; the scheduler turns them into
// messages between turns (decision 134) and switches them back on. Uses four
// registers, saved at the entry, then returns to what was running.
1:  ldr     x0, =0x3F00B208             // IRQ pending 2: sources 32 to 63
    ldr     w1, [x0]
    cbz     w1, 2f                      // nothing we know of: report it
    ldr     x2, =0x3F00B220             // disable IRQs 2: switch off what fired
    str     w1, [x2]
    adrp    x2, actor_arena
    add     x2, x2, :lo12:actor_arena
    ldr     x3, [x2, #48]               // the pending word
    orr     x3, x3, x1                  // w1 was zero-extended by the load
    str     x3, [x2, #48]
    ldp     x2, x3, [sp], #16
    ldp     x0, x1, [sp], #16
    eret
2:  ldp     x2, x3, [sp], #16           // an interrupt nobody asked for: report it, with
    ldp     x0, x1, [sp], #16           // the registers as they were
    mov     x0, #5
    b       exception_report

// x0 = vector index (0-15). Never returns.
exception_report:
    msr     cntp_ctl_el0, xzr           // a halted core must not take the budget timer
    mov     x19, x0

    adr     x0, msg_banner
    bl      uart_puts

    adr     x0, msg_kind                // "kind:    " + name
    bl      uart_puts
    ldr     x1, =kind_names
    ldr     x0, [x1, x19, lsl #3]
    bl      uart_puts
    mov     x0, #'\n'
    bl      uart_putc

    adr     x0, msg_ec                  // exception class = ESR_EL1[31:26]
    bl      uart_puts
    mrs     x0, esr_el1
    ubfx    x0, x0, #26, #6
    bl      uart_put_hex

    adr     x0, msg_esr
    bl      uart_puts
    mrs     x0, esr_el1
    bl      uart_put_hex

    adr     x0, msg_elr
    bl      uart_puts
    mrs     x0, elr_el1
    bl      uart_put_hex

    adr     x0, msg_far
    bl      uart_puts
    mrs     x0, far_el1
    bl      uart_put_hex

    adr     x0, msg_spsr
    bl      uart_puts
    mrs     x0, spsr_el1
    bl      uart_put_hex

    adr     x0, msg_halted
    bl      uart_puts
1:  wfe
    b       1b

    .section .rodata
msg_banner: .asciz "\n*** Raven: unhandled exception ***\n"
msg_kind:   .asciz "kind:     "
msg_ec:     .asciz "EC:       "
msg_esr:    .asciz "ESR_EL1:  "
msg_elr:    .asciz "ELR_EL1:  "
msg_far:    .asciz "FAR_EL1:  "
msg_spsr:   .asciz "SPSR_EL1: "
msg_halted: .asciz "core halted\n"

k0:  .asciz "synchronous, current EL, SP0"
k1:  .asciz "IRQ, current EL, SP0"
k2:  .asciz "FIQ, current EL, SP0"
k3:  .asciz "SError, current EL, SP0"
k4:  .asciz "synchronous, current EL, SPx"
k5:  .asciz "IRQ, current EL, SPx"
k6:  .asciz "FIQ, current EL, SPx"
k7:  .asciz "SError, current EL, SPx"
k8:  .asciz "synchronous, lower EL, AArch64"
k9:  .asciz "IRQ, lower EL, AArch64"
k10: .asciz "FIQ, lower EL, AArch64"
k11: .asciz "SError, lower EL, AArch64"
k12: .asciz "synchronous, lower EL, AArch32"
k13: .asciz "IRQ, lower EL, AArch32"
k14: .asciz "FIQ, lower EL, AArch32"
k15: .asciz "SError, lower EL, AArch32"

    .balign 8
kind_names:
    .quad k0, k1, k2, k3, k4, k5, k6, k7, k8, k9, k10, k11, k12, k13, k14, k15
