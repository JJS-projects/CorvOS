// Test hooks, called by kernel_main (raven/main.rook) once the console is up.
//
// Which hook runs is chosen when this file is assembled (see Makefile):
//   --defsym FAULT_TEST=1  a deliberate breakpoint, to test the vectors
//   --defsym ROOK_TEST=1   calls test_main(peripherals), a compiled Rook test
// In the normal kernel it does nothing (and quiet_build says to wait for real). Output uses the early console
// (uart.s), which kernel_main has already initialised.

    .section .text
// void run_test_hooks(const Mmio *peripherals)
    .global run_test_hooks
run_test_hooks:
    stp     x29, x30, [sp, #-16]!
    stp     x19, x20, [sp, #-16]!
    mov     x19, x0                     // the peripherals Mmio, lent to test_main

.ifdef ROOK_TEST
    adr     x0, msg_test_start          // run a compiled Rook test program
    bl      uart_puts
    mov     x0, x19                     // test_main may take `peripherals: Mmio`
    bl      test_main
    adr     x0, msg_test_done
    bl      uart_puts
.endif

.ifdef FAULT_TEST
    adr     x0, msg_fault
    bl      uart_puts
    brk     #0x42                       // must land in exception_report
.endif

    ldp     x19, x20, [sp], #16
    ldp     x29, x30, [sp], #16
    ret

// bool quiet_build(void): true in the test builds, where nobody watches the
// activity LED and waiting for it would only slow the tests down.
    .global quiet_build
quiet_build:
    mov     x0, #0
.ifdef ROOK_TEST
    mov     x0, #1
.endif
.ifdef FAULT_TEST
    mov     x0, #1
.endif
    ret

    .section .rodata
msg_fault:      .asciz "Raven: FAULT_TEST build, triggering a breakpoint\n"
msg_test_start: .asciz "Raven: running Rook test\n"
msg_test_done:  .asciz "Raven: test_main returned\n"
