// Small programs for tests of protection domains (domain.s, milestone 6b-2a). They run at EL0, so they can only
// use their registers, their stack and `svc`; the Rook tests in tests/rook/trusted/domain_*.rook start them
// with domain_enter. Built into the Rook test kernel only.

    .section .text

// u64 dt_program(n): the address of program n, for domain_enter.
    .global dt_program
dt_program:
    adr     x1, programs
    ldr     x0, [x1, x0, lsl #3]
    ret

    .balign 8
programs:
    .quad dt_return, dt_add, dt_two_calls, dt_write, dt_spin, dt_stack, dt_privileged, dt_jump, dt_overflow, dt_own_pool, dt_registers

// 0: returns at once (the stub the domain was given ends the handler)
dt_return:
    ret

// 1: x0 = x0 + x1 + x2, asked of Raven with `svc #7`
dt_add:
    add     x0, x0, x1
    add     x0, x0, x2
    svc     #7
    ret

// 2: `svc #1`; adds 10 to what Raven answers; `svc #2` with the sum
dt_two_calls:
    svc     #1
    add     x0, x0, #10
    svc     #2
    ret

// 3: stores x1 at the address x0 (a data abort if the domain's table does not allow it)
dt_write:
    str     x1, [x0]
    ret

// 4: never ends
dt_spin:
    b       dt_spin

// 5: uses its stack: pushes x0 and x1, pops them into x2 and x3, asks Raven for x2 + x3 with `svc #7`
dt_stack:
    stp     x0, x1, [sp, #-16]!
    ldp     x2, x3, [sp], #16
    add     x0, x2, x3
    svc     #7
    ret

// 6: reads a system register EL0 may not read
dt_privileged:
    mrs     x0, sctlr_el1
    ret

// 7: jumps to the address in x0
dt_jump:
    br      x0

// 8: pushes for ever, until the stack runs into the unmapped guard page
dt_overflow:
    stp     x0, x1, [sp, #-16]!
    b       dt_overflow

// 9: writes x1 at the address x0, reads it back, and asks Raven for what it read with `svc #7`
dt_own_pool:
    str     x1, [x0]
    ldr     x0, [x0]
    svc     #7
    ret

// 10: tells Raven x5, x20 and x29 (which Raven must not have leaked into the domain) with three `svc #7`
dt_registers:
    mov     x0, x5
    svc     #7
    mov     x0, x20
    svc     #7
    mov     x0, x29
    svc     #7
    ret
