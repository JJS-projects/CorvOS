// Raven boot entry for the BCM2837 / BCM2710A1 (Pi Zero 2 W, QEMU raspi3b).
//
// The firmware jumps here with the MMU and caches off. Depending on the
// firmware it may enter at EL3 or EL2 (or EL1), so we handle all three and
// always end up at EL1, which is where Raven runs.
//
// On exit to kernel_main(entry_el), where entry_el is the EL the firmware
// entered at:
//   - only core 0 is running; cores 1-3 are parked
//   - EL1, using SP_EL1, all interrupts masked
//   - BSS zeroed, stack set up, exception vectors installed

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

3:  ldr     x0, =vectors
    msr     vbar_el1, x0
    isb

    mov     x0, x19                     // kernel_main(entry_el)
    bl      kernel_main

    // kernel_main returned: nothing left to do.
halt:
    wfe
    b       halt

    .section .bss
    .balign 16
boot_stack:
    .space  16384
boot_stack_top:
