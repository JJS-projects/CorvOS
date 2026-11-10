// Protection domains (milestone 6b-2a, decision 174): running code at EL0 in an address space of its own.
//
// Domain 0 is Raven and every plain actor: EL1, the identity map of mmu.s. Domains 1 to 3 are for `separate`
// actors. A domain has its own page tables and ASID, and a private 2 MB pool of memory. This file is the part
// that cannot be written in Rook: building the tables, entering EL0 and coming back. Raven's Rook code decides
// what to run and what the domain's requests mean (raven/trusted/assembly.rook declares the calls below).
//
// What a domain's table maps (all non-global, tagged with the domain's ASID):
//   the kernel image's code     read-only, executable at EL0 and EL1 (EL1 needs it: the exception vectors
//                               run while the domain's table is still in use)
//   the image's constants       read-only at EL0, never executed
//   domain_tramp (4 KB)         EL1 only: the exception stack, the saved registers and where Raven's context is
//   the domain's pool           read/write at EL0, never executed: page 0 is NOT mapped (a guard below the
//                               stack), pages 1-15 are the stack (it starts at 64 KB and grows down), the rest
//                               is the domain's data
//   nothing else: Raven's data, the actor arena, the peripherals, and every other pool fault.
//
// An exception from EL0 arrives with SP_EL1 on domain_tramp, saves all of EL0's registers there, switches to
// Raven's table and returns to the caller of domain_enter or domain_resume with the reason in x0. So Raven
// sees a domain as a call that returns: DONE (the handler returned), CALL (it asked for something with `svc`,
// and can be continued with domain_resume), FAULT (it did something its table does not allow) or TIMEOUT
// (the time budget timer fired).

    .equ NDOMAINS,      3
    .equ POOL_BASE,     0x14000000          // physical; domain d's pool is at POOL_BASE + (d - 1) * 2 MB, above the 256 MB region
    .equ POOL_L2_FIRST, 160                 // POOL_BASE >> 21: the L2 index of domain 1's pool
    .equ STACK_TOP,     0x10000             // the stack starts 64 KB into the pool
    .equ KERNEL_START,  0x80000

    // Descriptors (see mmu.s for the bits). nG is set in all of them; AP is bits 7:6.
    .equ NX,            0x0060000000000000
    .equ D_TABLE,       0x3
    .equ D_CODE,        0x0000000000000FC7      // page, Normal, AF, nG, AP 11: read-only at EL0 and EL1, executable
    .equ D_CONST,       0x0060000000000FC7      // the same, never executed
    .equ D_TRAMP,       0x0060000000000F07      // AP 00: EL1 only, read/write, never executed
    .equ D_POOL,        0x0060000000000F47      // AP 01: read/write at EL0 and EL1, never executed

    .equ REASON_DONE,    0
    .equ REASON_FAULT,   1
    .equ REASON_TIMEOUT, 2
    .equ REASON_CALL,    3

    // domain_tramp: [0, 96) Raven's x19-x30, [96] its sp, [104] Raven's TTBR0, [112] the running domain's TTBR0,
    // then the saved EL0 registers: a frame of 288 bytes at 224, whose end (512) is where SP_EL1 sits in EL0.
    .equ T_KSP,         96
    .equ T_KTTBR,       104
    .equ T_CURTTBR,     112
    .equ T_FRAME,       224
    .equ T_TOP,         512
    .equ FRAME_SIZE,    288
    .equ F_SP,          248                 // x0-x30 are at 8 * n; then SP_EL0, ELR, SPSR, ESR and FAR
    .equ F_ELR,         256
    .equ F_SPSR,        264
    .equ F_ESR,         272
    .equ F_FAR,         280

    .section .text

// void domains_init(void): builds the tables of every domain. Once, at boot, with the BSS zeroed. Uses x0-x9.
    .global domains_init
domains_init:
    ldr     x9, =__text_end
    ldr     x8, =__rodata_end
    ldr     x7, =domain_tramp
    lsr     x7, x7, #12                 // the page number of domain_tramp
    ldr     x6, =image_l3
    mov     x0, #0                      // page index over 0 - 4 MB
1:  lsl     x1, x0, #12
    mov     x2, #0                      // default: not mapped (below the image, data and bss)
    cmp     x1, #KERNEL_START
    b.lo    4f
    cmp     x1, x9
    b.hs    2f
    ldr     x2, =D_CODE
    b       3f
2:  cmp     x1, x8
    b.hs    4f
    ldr     x2, =D_CONST
3:  orr     x2, x2, x1
4:  cmp     x0, x7
    b.ne    5f
    ldr     x2, =D_TRAMP
    orr     x2, x2, x1
5:  str     x2, [x6, x0, lsl #3]
    add     x0, x0, #1
    cmp     x0, #1024
    b.lo    1b

    // each domain: L1 -> L2 -> (image L3 for 0 - 2 MB and 2 - 4 MB, its own L3 for its pool)
    ldr     x5, =domain_l1
    mov     x4, #1                      // x4 = domain number
10: sub     x0, x4, #1
    add     x1, x5, x0, lsl #12         // L1 of this domain
    add     x2, x1, #(NDOMAINS << 12)   // L2 (the tables of the same kind are together: L1s, L2s, L3s)
    add     x3, x2, #(NDOMAINS << 12)   // the pool's L3
    orr     x9, x2, #D_TABLE
    str     x9, [x1]                    // L1[0] -> L2
    orr     x9, x6, #D_TABLE            // image L3, first half
    str     x9, [x2]
    add     x9, x6, #0x1000
    orr     x9, x9, #D_TABLE            // second half
    str     x9, [x2, #8]
    orr     x9, x3, #D_TABLE
    add     x10, x2, #(POOL_L2_FIRST * 8)
    str     x9, [x10, x0, lsl #3]       // L2[POOL_L2_FIRST + d - 1] -> the pool's L3
    mov     x9, #POOL_BASE
    add     x9, x9, x0, lsl #21         // the pool's physical address
    ldr     x11, =D_POOL
    mov     x12, #1                     // pages 1 - 511; page 0 stays unmapped
11: lsl     x13, x12, #12
    add     x13, x13, x9
    orr     x13, x13, x11
    str     x13, [x3, x12, lsl #3]
    add     x12, x12, #1
    cmp     x12, #512
    b.lo    11b
    add     x4, x4, #1
    cmp     x4, #NDOMAINS
    b.ls    10b

    ldr     x0, =page_tables            // Raven's own TTBR0 (ASID 0), kept where the exception entry can find it
    ldr     x1, =domain_tramp
    str     x0, [x1, #T_KTTBR]
    ret

// u64 domain_pool_base(d): the physical address of domain d's pool (identity mapped for Raven)
    .global domain_pool_base
domain_pool_base:
    sub     x0, x0, #1
    mov     x1, #POOL_BASE
    add     x0, x1, x0, lsl #21
    ret

// void domain_wipe(d): zeroes domain d's pool (2 MB). Raven does this before a domain starts afresh.
    .global domain_wipe
domain_wipe:
    sub     x0, x0, #1
    mov     x1, #POOL_BASE
    add     x1, x1, x0, lsl #21
    mov     x2, #0x200000
    add     x2, x1, x2
1:  stp     xzr, xzr, [x1], #16
    cmp     x1, x2
    b.lo    1b
    ret

// Saves Raven's callee-saved registers and sp where an exception from EL0 will find them: after this, a
// return to our caller is made by leave_domain. Leaves x9 = domain_tramp.
    .macro save_kernel
    ldr     x9, =domain_tramp
    stp     x19, x20, [x9, #0]
    stp     x21, x22, [x9, #16]
    stp     x23, x24, [x9, #32]
    stp     x25, x26, [x9, #48]
    stp     x27, x28, [x9, #64]
    stp     x29, x30, [x9, #80]
    mov     x10, sp
    str     x10, [x9, #T_KSP]
    .endm

// u64 domain_enter(d, entry, a0, a1, a2): starts domain d at `entry` at EL0 with x0-x2 = a0-a2, every other
// register zero, and x30 pointing at a stub that ends the handler with `svc #0`: so the entry may be a function
// that returns. Returns why the domain stopped (REASON_*).
    .global domain_enter
domain_enter:
    save_kernel
    sub     x11, x0, #1
    ldr     x12, =domain_l1
    add     x12, x12, x11, lsl #12
    orr     x12, x12, x0, lsl #48       // TTBR0: the L1 table, the domain number as the ASID
    str     x12, [x9, #T_CURTTBR]
    add     x12, x9, #T_FRAME
    mov     x13, x12
    .rept   18                          // zero the frame: 288 bytes
    stp     xzr, xzr, [x13], #16
    .endr
    stp     x2, x3, [x12, #0]           // x0, x1
    str     x4, [x12, #16]              // x2
    ldr     x13, =domain_return_stub
    str     x13, [x12, #240]            // x30
    mov     x13, #POOL_BASE
    add     x13, x13, x11, lsl #21
    add     x13, x13, #STACK_TOP
    str     x13, [x12, #F_SP]
    str     x1, [x12, #F_ELR]
    str     xzr, [x12, #F_SPSR]         // EL0t, interrupts unmasked (the budget timer must be able to fire)
    b       resume_current

// u64 domain_resume(result): continues a domain that stopped with REASON_CALL, with `result` in its x0.
    .global domain_resume
domain_resume:
    save_kernel
    str     x0, [x9, #(T_FRAME + 0)]
    b       resume_current

// Puts the running domain's table and registers back and returns to EL0. SP_EL1 ends on the top of the tramp
// page, where the next exception from EL0 will start.
resume_current:
    ldr     x9, =domain_tramp
    ldr     x10, [x9, #T_CURTTBR]
    msr     ttbr0_el1, x10
    isb
    add     x11, x9, #T_FRAME
    mov     sp, x11
    ldr     x0, [sp, #F_SP]
    msr     sp_el0, x0
    ldp     x0, x1, [sp, #F_ELR]        // ELR and SPSR
    msr     elr_el1, x0
    msr     spsr_el1, x1
    ldp     x0, x1, [sp, #0]
    ldp     x2, x3, [sp, #16]
    ldp     x4, x5, [sp, #32]
    ldp     x6, x7, [sp, #48]
    ldp     x8, x9, [sp, #64]
    ldp     x10, x11, [sp, #80]
    ldp     x12, x13, [sp, #96]
    ldp     x14, x15, [sp, #112]
    ldp     x16, x17, [sp, #128]
    ldp     x18, x19, [sp, #144]
    ldp     x20, x21, [sp, #160]
    ldp     x22, x23, [sp, #176]
    ldp     x24, x25, [sp, #192]
    ldp     x26, x27, [sp, #208]
    ldp     x28, x29, [sp, #224]
    ldr     x30, [sp, #240]
    add     sp, sp, #FRAME_SIZE
    eret

// Back to Raven: restores its registers and returns, with x0 = the reason, to whoever called domain_enter or
// domain_resume. Raven's table is already in use.
leave_domain:
    ldr     x9, =domain_tramp
    ldp     x19, x20, [x9, #0]
    ldp     x21, x22, [x9, #16]
    ldp     x23, x24, [x9, #32]
    ldp     x25, x26, [x9, #48]
    ldp     x27, x28, [x9, #64]
    ldp     x29, x30, [x9, #80]
    ldr     x10, [x9, #T_KSP]
    mov     sp, x10
    ret

// The first thing an exception from EL0 does: SP_EL1 is on domain_tramp, so save EL0's registers there, then
// use Raven's table. (Uses only registers it has saved.)
    .macro save_user
    sub     sp, sp, #FRAME_SIZE
    stp     x0, x1, [sp, #0]
    stp     x2, x3, [sp, #16]
    stp     x4, x5, [sp, #32]
    stp     x6, x7, [sp, #48]
    stp     x8, x9, [sp, #64]
    stp     x10, x11, [sp, #80]
    stp     x12, x13, [sp, #96]
    stp     x14, x15, [sp, #112]
    stp     x16, x17, [sp, #128]
    stp     x18, x19, [sp, #144]
    stp     x20, x21, [sp, #160]
    stp     x22, x23, [sp, #176]
    stp     x24, x25, [sp, #192]
    stp     x26, x27, [sp, #208]
    stp     x28, x29, [sp, #224]
    str     x30, [sp, #240]
    mrs     x0, sp_el0
    str     x0, [sp, #F_SP]
    mrs     x0, elr_el1
    mrs     x1, spsr_el1
    stp     x0, x1, [sp, #F_ELR]
    mrs     x0, esr_el1
    mrs     x1, far_el1
    stp     x0, x1, [sp, #F_ESR]
    ldr     x0, =domain_tramp
    ldr     x1, [x0, #T_KTTBR]
    msr     ttbr0_el1, x1
    isb
    .endm

// Synchronous exception from EL0 (vector 8): `svc #0` ends a handler, `svc #n` is a request, anything else is
// the domain doing what its table or its rights do not allow.
    .global domain_sync
domain_sync:
    save_user
    ldr     x1, [sp, #F_ESR]
    lsr     x2, x1, #26                 // the exception class
    cmp     x2, #0x15                   // SVC from AArch64
    b.ne    fault
    ands    x1, x1, #0xffff             // the svc number
    mov     x0, #REASON_DONE
    b.eq    leave_domain
    mov     x0, #REASON_CALL
    b       leave_domain
fault:
    mov     x0, #REASON_FAULT
    b       leave_domain

// IRQ from EL0 (vector 9): the budget timer ends the run; any other interrupt is noted for the scheduler, as
// at EL1 (vectors.s), and the domain goes on.
    .global domain_irq
domain_irq:
    save_user
    mrs     x1, cntp_ctl_el0
    tbz     x1, #2, 1f                  // ISTATUS: the timer condition is met
    msr     cntp_ctl_el0, xzr           // disarm
    isb
    mov     x0, #REASON_TIMEOUT
    b       leave_domain
1:  ldr     x0, =0x3F00B208             // IRQ pending 2: sources 32 to 63
    ldr     w1, [x0]
    cbz     w1, 2f
    ldr     x2, =0x3F00B220             // disable IRQs 2: switch off what fired
    str     w1, [x2]
    adrp    x2, actor_arena
    add     x2, x2, :lo12:actor_arena
    ldr     x3, [x2, #48]
    orr     x3, x3, x1
    str     x3, [x2, #48]
    b       resume_current
2:  mov     x0, #9                      // an interrupt nobody asked for: report it (never returns)
    b       exception_report

// What the stub at the end of a handler does: `svc #0`.
    .global domain_return_stub
domain_return_stub:
    svc     #0

// What the last stop was, for Raven. The registers are those saved at the stop.
    .global domain_tramp_address     // u64: where Raven keeps a stopped domain's registers (EL1 only)
domain_tramp_address:
    ldr     x0, =domain_tramp
    ret

    .global domain_call_number
domain_call_number:                     // u64: the svc number of a REASON_CALL
    ldr     x0, =domain_tramp
    ldr     x0, [x0, #(T_FRAME + F_ESR)]
    and     x0, x0, #0xffff
    ret

    .global domain_saved_x           // u64 domain_saved_x(n): EL0's register xn at the stop (n 0-30)
domain_saved_x:
    ldr     x1, =domain_tramp
    add     x1, x1, #T_FRAME
    ldr     x0, [x1, x0, lsl #3]
    ret

    .global domain_fault_esr         // u64: ESR_EL1 at the stop
domain_fault_esr:
    ldr     x0, =domain_tramp
    ldr     x0, [x0, #(T_FRAME + F_ESR)]
    ret

    .global domain_fault_far         // u64: FAR_EL1 at the stop (the address of a data or instruction abort)
domain_fault_far:
    ldr     x0, =domain_tramp
    ldr     x0, [x0, #(T_FRAME + F_FAR)]
    ret

    .global domain_fault_elr         // u64: where EL0 was (ELR_EL1)
domain_fault_elr:
    ldr     x0, =domain_tramp
    ldr     x0, [x0, #(T_FRAME + F_ELR)]
    ret

    .section .bss
    .balign 4096
    .global domain_tramp
domain_tramp:                           // see the layout above; EL1 only, in every domain's table
    .space  4096
domain_l1:                              // NDOMAINS L1 tables, then NDOMAINS L2 tables, then NDOMAINS pool L3 tables
    .space  3 * 3 * 4096
image_l3:                               // the image as a domain sees it: 2 tables cover 0 - 4 MB
    .space  2 * 4096
