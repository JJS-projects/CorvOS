// Messages between cores (milestone 6d-2, decision 181). Each core's Raven keeps its actors, mailboxes and tables to
// itself (decision 111); the only memory two cores share is a ring for each ordered pair of cores: a queue with one
// writer, the sending core, and one reader, the receiving core. A record is 16 words (128 bytes); Raven's Rook code
// (mailbox.rook) says what is in one.
//
// Ring (src, dst) is at rings + (4 * src + dst) * 8192:  [0] head, written only by dst  [64] tail, written only by src
// [128 ...] 32 slots of 128 bytes. The counters only grow; a slot is counter mod 32. The writer fills a slot and then
// stores the tail with a release (stlr); the reader loads the tail with an acquire (ldar), so it sees the whole slot.
// `sev` wakes a core waiting in `wfe`. A global count of messages not yet handled (pending_*) tells a core that is
// waiting for everything to settle whether any other core is still at work.

    .equ RING_SLOTS,    32
    .equ RING_STRIDE_SHIFT, 13              // 8192 bytes a ring
    .equ T_ACTORS,      128                 // domain.s: the actor in each domain of a core (0: free)
    .equ POOL_BASE,     0x14000000          // domain.s
    .equ SCRATCH,       1024                // two ring records' worth of scratch in each core's block: 1024 and 1152

    .section .text

// u64 ring_push(dst, record): copies the 128 bytes at `record` into the ring from this core to `dst`. 1 if it
// was queued, 0 if the ring is full (nothing changed).
    .global ring_push
ring_push:
    mrs     x2, mpidr_el1
    and     x2, x2, #3                  // src: this core
    lsl     x2, x2, #2
    add     x2, x2, x0                  // 4 * src + dst
    ldr     x3, =rings
    add     x3, x3, x2, lsl #RING_STRIDE_SHIFT
    add     x4, x3, #64
    ldr     x5, [x4]                    // tail: ours
    ldar    x6, [x3]                    // head: the reader's, with acquire
    sub     x7, x5, x6
    cmp     x7, #RING_SLOTS
    b.hs    1f                          // full
    and     x8, x5, #(RING_SLOTS - 1)
    add     x9, x3, #128
    add     x9, x9, x8, lsl #7          // the slot
    ldp     x10, x11, [x1, #0]
    stp     x10, x11, [x9, #0]
    ldp     x10, x11, [x1, #16]
    stp     x10, x11, [x9, #16]
    ldp     x10, x11, [x1, #32]
    stp     x10, x11, [x9, #32]
    ldp     x10, x11, [x1, #48]
    stp     x10, x11, [x9, #48]
    ldp     x10, x11, [x1, #64]
    stp     x10, x11, [x9, #64]
    ldp     x10, x11, [x1, #80]
    stp     x10, x11, [x9, #80]
    ldp     x10, x11, [x1, #96]
    stp     x10, x11, [x9, #96]
    ldp     x10, x11, [x1, #112]
    stp     x10, x11, [x9, #112]
    add     x5, x5, #1
    stlr    x5, [x4]                    // the tail, with release: the slot is complete before the reader can see it
    sev
    mov     x0, #1
    ret
1:  mov     x0, #0
    ret

// u64 ring_pop(src, record): takes the oldest record of the ring from `src` to this core, into the 128 bytes at
// `record`. 1 if there was one, 0 if the ring is empty.
    .global ring_pop
ring_pop:
    mrs     x2, mpidr_el1
    and     x2, x2, #3                  // dst: this core
    lsl     x3, x0, #2
    add     x3, x3, x2                  // 4 * src + dst
    ldr     x4, =rings
    add     x4, x4, x3, lsl #RING_STRIDE_SHIFT
    ldr     x5, [x4]                    // head: ours
    add     x6, x4, #64
    ldar    x7, [x6]                    // tail: the writer's, with acquire
    cmp     x5, x7
    b.eq    1f                          // empty
    and     x8, x5, #(RING_SLOTS - 1)
    add     x9, x4, #128
    add     x9, x9, x8, lsl #7
    ldp     x10, x11, [x9, #0]
    stp     x10, x11, [x1, #0]
    ldp     x10, x11, [x9, #16]
    stp     x10, x11, [x1, #16]
    ldp     x10, x11, [x9, #32]
    stp     x10, x11, [x1, #32]
    ldp     x10, x11, [x9, #48]
    stp     x10, x11, [x1, #48]
    ldp     x10, x11, [x9, #64]
    stp     x10, x11, [x1, #64]
    ldp     x10, x11, [x9, #80]
    stp     x10, x11, [x1, #80]
    ldp     x10, x11, [x9, #96]
    stp     x10, x11, [x1, #96]
    ldp     x10, x11, [x9, #112]
    stp     x10, x11, [x1, #112]
    add     x5, x5, #1
    stlr    x5, [x4]                    // the head, with release: the slot is ours no longer
    mov     x0, #1
    ret
1:  mov     x0, #0
    ret

// u64 rings_waiting(void): bit n is set if the ring from core n to this core holds a record.
    .global rings_waiting
rings_waiting:
    mrs     x1, mpidr_el1
    and     x1, x1, #3
    mov     x0, #0
    mov     x2, #0                      // src
1:  lsl     x3, x2, #2
    add     x3, x3, x1
    ldr     x4, =rings
    add     x4, x4, x3, lsl #RING_STRIDE_SHIFT
    ldr     x5, [x4]
    add     x6, x4, #64
    ldar    x7, [x6]
    cmp     x5, x7
    b.eq    2f
    mov     x8, #1
    lsl     x8, x8, x2
    orr     x0, x0, x8
2:  add     x2, x2, #1
    cmp     x2, #4
    b.lo    1b
    ret

// void pending_add(n) / void pending_sub(n) / u64 pending_load(void): the number of messages (in a mailbox, in a ring,
// or being handled) that no core has finished with, over all cores. A core waiting for the system to be quiet
// looks at it.
    .global pending_add
pending_add:
    ldr     x1, =pending_messages
1:  ldaxr   x2, [x1]
    add     x2, x2, x0
    stlxr   w3, x2, [x1]
    cbnz    w3, 1b
    ret

    .global pending_sub
pending_sub:
    ldr     x1, =pending_messages
1:  ldaxr   x2, [x1]
    sub     x2, x2, x0
    stlxr   w3, x2, [x1]
    cbnz    w3, 1b
    ret

    .global pending_load
pending_load:
    ldr     x1, =pending_messages
    ldar    x0, [x1]
    ret

// void core_wait_event(void): sleeps until another core executes `sev` (ring_push does), or an interrupt, unless one
// already has.
    .global core_wait_event
core_wait_event:
    wfe
    ret

// void core_pause(void): a hint to the core that it is waiting in a loop.
    .global core_pause
core_pause:
    yield
    ret

// u64 ring_scratch(i): the address of this core's scratch record i (0 or 1), in its block (the page every domain's
// table maps for EL1 only).
    .global ring_scratch
ring_scratch:
    mrs     x1, tpidr_el1
    add     x1, x1, #SCRATCH
    add     x0, x1, x0, lsl #7
    ret

// u64 domain_actor_of(core, d) / void domain_set_actor_of(core, d, actor) / u64 domain_pool_base_of(core, d): the
// same as domain_actor, domain_set_actor and domain_pool_base for another core's domains. Core 0 sets one of
// another core's up when it places an actor there, before any message tells that core about it.
    .global domain_actor_of
domain_actor_of:
    ldr     x2, =domain_tramp
    add     x2, x2, x0, lsl #12
    add     x2, x2, #(T_ACTORS - 8)
    ldr     x0, [x2, x1, lsl #3]
    ret

    .global domain_set_actor_of
domain_set_actor_of:
    ldr     x3, =domain_tramp
    add     x3, x3, x0, lsl #12
    add     x3, x3, #(T_ACTORS - 8)
    str     x2, [x3, x1, lsl #3]
    ret

    .global domain_pool_base_of
domain_pool_base_of:
    mov     x2, #3
    madd    x0, x0, x2, x1              // 3 * core + d
    sub     x0, x0, #1
    mov     x1, #POOL_BASE
    add     x0, x1, x0, lsl #21
    ret

// void pool_wipe(base): zeroes 2 MB from `base` (a pool of any core).
    .global pool_wipe
pool_wipe:
    mov     x1, #0x200000
    add     x1, x0, x1
1:  stp     xzr, xzr, [x0], #16
    cmp     x0, x1
    b.lo    1b
    ret

// u64 domain_pools_start(void), u64 domain_pools_end(void): the physical range of every core's domain pools.
    .global domain_pools_start
domain_pools_start:
    mov     x0, #POOL_BASE
    ret

    .global domain_pools_end
domain_pools_end:
    mov     x0, #POOL_BASE
    mov     x1, #(12 << 21)
    add     x0, x0, x1
    ret

// u64 spawn_next_core(void): the core for the next `separate` actor, in turn: 1, 2, 3, 0, 1, ... (only core 0 spawns).
    .global spawn_next_core
spawn_next_core:
    ldr     x1, =next_core
    ldr     x0, [x1]
    add     x2, x0, #1
    str     x2, [x1]
    add     x0, x0, #1
    and     x0, x0, #3
    ret

// u64 spawn_count(void): how many `separate` actors have been placed so far.
    .global spawn_count
spawn_count:
    ldr     x1, =next_core
    ldr     x0, [x1]
    ret

    .section .bss
    .balign 4096
rings:
    .space  16 * 8192                   // 4 x 4 rings; the four from a core to itself are never used
    .balign 64
pending_messages:
    .space  64
next_core:
    .space  8
