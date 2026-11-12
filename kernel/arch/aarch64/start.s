// Raven boot entry for the BCM2837 / BCM2710A1 (Pi Zero 2 W, QEMU raspi3b).
//
// The firmware jumps here with the MMU and caches off. Depending on the
// firmware it may enter at EL3 or EL2 (or EL1), so we handle all three and
// always end up at EL1, which is where Raven runs.
//
// On exit to raven_start(entry_el), Raven's first Rook code
// (raven/trusted/devices.rook), where entry_el is the EL the firmware entered at:
//   - only core 0 is running; cores 1-3 are parked
//   - EL1, using SP_EL1, all interrupts masked
//   - BSS zeroed, stack set up, exception vectors installed
//   - the MMU and the caches on, an identity map (mmu.s)

    .section .text.boot, "ax"
    .global _start
_start:
    // Only core 0 boots. The firmware normally holds the other cores in its
    // own spin loop, but park them here too in case it doesn't.
    mrs     x0, mpidr_el1
    and     x0, x0, #0xff               // Aff0 = core number
    cbz     x0, 1f
park:
    wfe
    b       park

1:  mrs     x0, CurrentEL
    lsr     x0, x0, #2                  // CurrentEL is in bits [3:2]
    mov     x19, x0                     // remember the entry EL for kernel_main
    cmp     x0, #3
    b.eq    from_el3
    cmp     x0, #2
    b.eq    from_el2
    b       at_el1

from_el3:
    mov     x0, #0x401                  // SCR_EL3: RW=1 (EL2 is AArch64), NS=1
    msr     scr_el3, x0
    mov     x0, #0x3c9                  // SPSR: EL2h, DAIF masked
    msr     spsr_el3, x0
    adr     x0, from_el2
    msr     elr_el3, x0
    eret

from_el2:
    mov     x0, #(1 << 31)              // HCR_EL2: RW=1 (EL1 is AArch64)
    msr     hcr_el2, x0
    mrs     x0, cnthctl_el2             // let EL1 use the physical timer/counter
    orr     x0, x0, #3
    msr     cnthctl_el2, x0
    msr     cntvoff_el2, xzr
    ldr     x0, =0x30d00800             // SCTLR_EL1: RES1 bits only, MMU and caches off
    msr     sctlr_el1, x0
    mov     x0, #0x3c5                  // SPSR: EL1h, DAIF masked
    msr     spsr_el2, x0
    adr     x0, at_el1
    msr     elr_el2, x0
    eret

at_el1:
    msr     daifset, #0xf               // mask everything, even if we started at EL1
    mov     x0, #(3 << 20)              // CPACR_EL1: don't trap FP/SIMD
    msr     cpacr_el1, x0
    isb

    ldr     x0, =boot_stack_top
    mov     sp, x0

    ldr     x0, =__bss_start            // zero BSS (16-byte aligned by link.ld)
    ldr     x1, =__bss_end
2:  cmp     x0, x1
    b.hs    3f
    stp     xzr, xzr, [x0], #16
    b       2b

3:  ldr     x0, =domain_tramp           // core 0's block (domain.s): where its code finds its arena, tables and pools
    msr     tpidr_el1, x0
    ldr     x0, =vectors
    msr     vbar_el1, x0
    isb

    bl      mmu_enable                  // identity map with rights per page, caches on (mmu.s); keeps x19
    bl      domains_init                // the tables of the protection domains (domain.s); keeps x19

    mov     x0, x19                     // raven_start(entry_el)
    bl      raven_start

    // Raven's Rook code returned: nothing left to do.
halt:
    wfe
    b       halt

// void cores_release(void): lets cores 1-3 go. The firmware (QEMU's raspi3b and raspi3ap do, and the Pi's own stub
// does) holds them in a loop that waits for an address in a "spin table": 0xe0 for core 1, 0xe8 for core 2, 0xf0 for
// core 3. They arrive at secondary_entry at EL2 (or EL1) with the MMU and the caches off, so the table is cleaned to
// memory, not left in this core's cache.
    .global cores_release
cores_release:
    ldr     x1, =secondary_entry
    mov     x2, #0xe0
    mov     x3, #3
1:  str     x1, [x2]
    dc      civac, x2
    add     x2, x2, #8
    subs    x3, x3, #1
    b.ne    1b
    dsb     sy
    sev
    ret

// Cores 1-3 start here, once released. Same steps as core 0 (above) down to EL1, then their own stack, the same
// vectors, the same page tables (mmu_activate in mmu.s), and into Raven's Rook code for a secondary core.
    .global secondary_entry
secondary_entry:
    mrs     x0, CurrentEL
    lsr     x0, x0, #2
    cmp     x0, #2
    b.ne    secondary_el1               // already at EL1; an EL3 entry is not handled: the core would park below
    mov     x0, #(1 << 31)              // HCR_EL2: RW=1 (EL1 is AArch64)
    msr     hcr_el2, x0
    mrs     x0, cnthctl_el2             // let EL1 use the physical timer/counter
    orr     x0, x0, #3
    msr     cnthctl_el2, x0
    msr     cntvoff_el2, xzr
    ldr     x0, =0x30d00800             // SCTLR_EL1: RES1 bits only, MMU and caches off
    msr     sctlr_el1, x0
    mov     x0, #0x3c5                  // SPSR: EL1h, DAIF masked
    msr     spsr_el2, x0
    adr     x0, secondary_el1
    msr     elr_el2, x0
    eret

secondary_el1:
    msr     daifset, #0xf
    mov     x0, #(3 << 20)              // CPACR_EL1: don't trap FP/SIMD
    msr     cpacr_el1, x0
    isb
    mrs     x0, mpidr_el1
    and     x19, x0, #3                 // this core's number (1-3)
    cmp     x19, #1
    b.lo    park                        // not a core 1-3: stay out
    cmp     x19, #3
    b.hi    park
    ldr     x1, =secondary_stacks       // 64 KB a core: an unmapped guard page (mmu.s), then the stack
    sub     x2, x19, #1
    add     x1, x1, x2, lsl #16
    add     x1, x1, #0x10000
    mov     sp, x1
    ldr     x0, =domain_tramp           // this core's block
    add     x0, x0, x19, lsl #12
    msr     tpidr_el1, x0
    ldr     x0, =vectors
    msr     vbar_el1, x0
    isb
    bl      mmu_activate
    mov     x0, x19                     // raven_secondary(core), Rook (raven/trusted/actors.rook)
    bl      raven_secondary
2:  wfe
    b       2b

    .section .bss
    .balign 16
boot_stack:
    .space  8192                        // kept free: what the panic report and Raven's interrupt code use
    .global boot_stack_limit
boot_stack_limit:                       // compiled Rook code panics if its frame would begin below this
    .space  253952
boot_stack_top:

    .balign 4096
    .global secondary_stacks            // cores 1-3: 64 KB each, the first page of which mmu.s leaves unmapped
secondary_stacks:
    .space  3 * 65536
