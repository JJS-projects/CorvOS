// Small CPU helpers Rook can't express, declared in raven/trusted/assembly.rook.

    .section .text

// u64 current_el(void): the exception level we are running at (0-3).
    .global current_el
current_el:
    mrs     x0, CurrentEL
    ubfx    x0, x0, #2, #2              // CurrentEL keeps the level in bits 3:2
    ret

// u64 mmu_state(void): SCTLR_EL1 bits M (MMU, bit 0), C (data cache, bit 2) and I (instruction cache, bit 12):
// 0x1005 when mmu.s has switched everything on.
    .global mmu_state
mmu_state:
    mrs     x0, sctlr_el1
    mov     x1, #0x1005                 // (not a bitmask immediate, so through a register)
    and     x0, x0, x1
    ret

// u64 read_counter(void): the ARM generic timer's physical count. start.s lets
// EL1 read it (CNTHCTL_EL2). It ticks counter_frequency() times a second.
    .global read_counter
read_counter:
    mrs     x0, cntpct_el0
    ret

// u64 counter_frequency(void): ticks per second of read_counter(), as the
// firmware set it (CNTFRQ_EL0); 0 if the firmware didn't.
    .global counter_frequency
counter_frequency:
    mrs     x0, cntfrq_el0
    ret

// u64 core_id(void): the number of the core this runs on (0-3).
    .global core_id
core_id:
    mrs     x0, mpidr_el1
    and     x0, x0, #3
    ret

// void core_online_mark(void): this core says it is up, and where its actor arena is (never 0).
    .global core_online_mark
core_online_mark:
    mrs     x1, mpidr_el1
    and     x1, x1, #3
    mrs     x2, tpidr_el1
    ldr     x2, [x2, #176]              // T_ARENA (domain.s)
    ldr     x3, =core_online
    ldr     x6, =core_state
    mrs     x4, sctlr_el1               // what this core runs with: the exception level and the MMU and cache bits
    mov     x5, #0x1005
    and     x4, x4, x5
    mrs     x5, CurrentEL
    lsl     x5, x5, #14                 // CurrentEL holds the level in bits 3:2: shifted to bits 17:16
    orr     x4, x4, x5
    str     x4, [x6, x1, lsl #3]
    dmb     ish                         // the state before the arena: the arena says "I am up"
    str     x2, [x3, x1, lsl #3]
    dmb     ish
    ret

// u64 cores_online_mask(void): bit n is set once core n has marked itself.
    .global cores_online_mask
cores_online_mask:
    ldr     x1, =core_online
    mov     x0, #0
    mov     x3, #0
1:  ldr     x2, [x1, x3, lsl #3]
    cbz     x2, 2f
    mov     x4, #1
    lsl     x4, x4, x3
    orr     x0, x0, x4
2:  add     x3, x3, #1
    cmp     x3, #4
    b.lo    1b
    ret

// u64 core_online_arena(core): the arena core `core` reported (0 if it has not marked itself).
    .global core_online_arena
core_online_arena:
    ldr     x1, =core_online
    ldr     x0, [x1, x0, lsl #3]
    ret

// u64 core_online_state(core): (exception level << 16) | SCTLR_EL1's M, C and I bits, as the core found itself when it
// marked itself online: 0x11005 for a core at EL1 with the MMU and both caches on.
    .global core_online_state
core_online_state:
    ldr     x1, =core_state
    ldr     x0, [x1, x0, lsl #3]
    ret

// u64 core_stack_guard(core): the address of the unmapped guard page below core `core`'s stack (core 1-3).
    .global core_stack_guard
core_stack_guard:
    ldr     x1, =secondary_stacks
    sub     x0, x0, #1
    add     x0, x1, x0, lsl #16
    ret

    .section .bss
    .balign 64
core_online:                            // by core: its arena once it is up, else 0
    .space  32
core_state:                             // by core: see core_online_state
    .space  32
