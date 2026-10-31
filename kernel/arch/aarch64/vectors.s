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
    mov     x0, #\kind
    .if \kind == 5
    b       irq_entry                   // IRQ at EL1 using SP_EL1: the only one in use
    .else
    b       exception_report
    .endif
    .endr

// An IRQ at EL1. The only interrupt enabled is the physical timer, armed while a
// handler runs (decision 133): if it has fired, the handler has run over its time
// budget and Raven reports it as a failure. Anything else is a report as before.
// Never returns, so no registers are saved.
irq_entry:
    mrs     x1, cntp_ctl_el0
    tbz     x1, #2, 1f                  // ISTATUS (bit 2): the timer condition is met
    msr     cntp_ctl_el0, xzr           // disarm, so it can't fire again while we report
    isb
    bl      raven_budget_overrun        // Rook (raven/trusted/actors.rook)
1:  mov     x0, #5
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
