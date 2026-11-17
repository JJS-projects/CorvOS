// The calls compiled Rook code makes into Raven (milestone 6b-2b, decision 175): `bl raven_send` and the rest.
//
// They are one page of stubs. For a plain actor (Raven's own domain) a stub just jumps to the Rook function that
// does the work. For an actor in a protection domain the same page is replaced, in the domain's page table
// (domain.s), by rt_gate, which has the same layout and asks Raven for the same thing with `svc #n`: so the
// code Fledgling generates is the same for both, and a domain never runs Raven's code.
//
// Entry i is at byte 16 * i; its svc number in the gate is i + 1. Raven's service (mailbox.rook, service_call)
// uses these numbers.

    .macro stub name, impl
    .global \name
\name:
    b       \impl
    .balign 16
    .endm

    .section .text.rtstubs, "ax"
    .balign 4096
    .global rt_stubs
rt_stubs:
    stub    raven_send, send_impl                   // 1
    stub    raven_send_reliable, send_reliable_impl // 2
    stub    raven_spawn_actor, spawn_actor_impl     // 3
    stub    raven_spawn_separate, spawn_separate_impl // 4
    stub    raven_watch, watch_impl                 // 5
    stub    raven_stop, stop_impl                   // 6
    stub    raven_stop_self, stop_self_impl         // 7
    stub    rook_panic, rook_panic_impl             // 8
    stub    raven_timer_after, timer_after_impl     // 9
    stub    raven_self, self_impl                   // 10: the running actor's whole reference, address and generation
    stub    raven_new_domain, new_domain_impl       // 11: an empty domain for `spawn ... in` (decision 201); not for a domain
    stub    raven_stop_domain, stop_domain_impl     // 12: ends the actors of a domain and gives it back (decision 201); not for a domain
    stub    raven_revoke, revoke_impl               // 13: cuts off every reference to an actor and returns a new one (decision 207); not for a domain
    .org    4096                                    // the page is exactly 4 KB

    .section .text.rtgate, "ax"
    .balign 4096
    .global rt_gate
rt_gate:
    // Entries 1 and 2 (a send, a reliable send) first try the fast path of decision 204 and ask Raven only if it did not handle the
    // message. The gate page is the same virtual address in every domain but the stubs' physical copy, so the jump is absolute.
    ldr     x16, 1f
    br      x16
1:  .quad   rt_gate_send
    ldr     x16, 2f
    br      x16
2:  .quad   rt_gate_send_reliable
    .irp n, 3,4,5,6,7,8,9,10,11,12,13
    svc     #\n
    ret
    .balign 16
    .endr
    .org    4096

    .section .text, "ax"
// A send inside a domain (decision 204): try `domain_send_local(target, handler, payload, words, pool)`, where pool is this domain's own
// pool (the stack is in it, and a pool is 2 MB aligned); if it did not handle it, `svc #1`. x0-x3 and the link register are kept
// across the call so that the svc sees the arguments.
    .global rt_gate_send
rt_gate_send:
    sub     sp, sp, #48
    stp     x0, x1, [sp]
    stp     x2, x3, [sp, #16]
    str     x30, [sp, #32]
    mov     x4, sp
    lsr     x4, x4, #21
    lsl     x4, x4, #21                 // the domain's pool: the stack pointer with the low 21 bits cleared
    bl      domain_send_local
    mov     x9, x0                      // 1: handled
    ldp     x0, x1, [sp]
    ldp     x2, x3, [sp, #16]
    ldr     x30, [sp, #32]
    add     sp, sp, #48
    cbnz    x9, 1f
    svc     #1
1:  ret

// The same for a send to a `reliable` handler: x8 is where the result goes (kept too), and it is the sixth argument.
    .global rt_gate_send_reliable
rt_gate_send_reliable:
    sub     sp, sp, #64
    stp     x0, x1, [sp]
    stp     x2, x3, [sp, #16]
    stp     x8, x30, [sp, #32]
    mov     x4, sp
    lsr     x4, x4, #21
    lsl     x4, x4, #21
    mov     x5, x8
    bl      domain_send_reliable_local
    mov     x9, x0
    ldp     x0, x1, [sp]
    ldp     x2, x3, [sp, #16]
    ldp     x8, x30, [sp, #32]
    add     sp, sp, #64
    cbnz    x9, 1f
    svc     #2
1:  ret
