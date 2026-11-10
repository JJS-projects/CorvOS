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
    .org    4096                                    // the page is exactly 4 KB

    .section .text.rtgate, "ax"
    .balign 4096
    .global rt_gate
rt_gate:
    .irp n, 1,2,3,4,5,6,7,8
    svc     #\n
    ret
    .balign 16
    .endr
    .org    4096
