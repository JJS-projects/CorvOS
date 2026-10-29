// Raven kernel entry (milestone 1).
//
// For now this only proves the machine boots: it brings up the console,
// reports the exception level, and idles. It is written in assembly because
// Rook does not exist yet; milestone 2 rewrites it in Rook.
//
// Test builds (see Makefile):
//   --defsym FAULT_TEST=1  adds a deliberate breakpoint, to test the vectors
//   --defsym ROOK_TEST=1   calls test_main(), a compiled Rook test program

    .section .text
// void kernel_main(u64 entry_el)
    .global kernel_main
kernel_main:
    stp     x29, x30, [sp, #-16]!
    stp     x19, x20, [sp, #-16]!
    mov     x19, x0

    bl      uart_init
    adr     x0, msg_hello
    bl      uart_puts

    adr     x0, msg_entry                // "firmware entered at EL" + digit
    bl      uart_puts
    add     x0, x19, #'0'
    bl      uart_putc
    adr     x0, msg_newline
    bl      uart_puts

    adr     x0, msg_el                  // "running at EL" + digit
    bl      uart_puts
    mrs     x0, CurrentEL
    ubfx    x0, x0, #2, #2
    add     x0, x0, #'0'
    bl      uart_putc
    adr     x0, msg_newline
    bl      uart_puts

.ifdef ROOK_TEST
    adr     x0, msg_test_start          // run a compiled Rook test program
    bl      uart_puts
    bl      test_main
    adr     x0, msg_test_done
    bl      uart_puts
.endif

.ifdef FAULT_TEST
    adr     x0, msg_fault
    bl      uart_puts
    brk     #0x42                       // must land in exception_report
.endif

    adr     x0, msg_idle
    bl      uart_puts

    ldp     x19, x20, [sp], #16
    ldp     x29, x30, [sp], #16
    ret

    .section .rodata
msg_hello:   .asciz "Raven: hello from CorvOS\n"
msg_entry:   .asciz "Raven: firmware entered at EL"
msg_el:      .asciz "Raven: running at EL"
msg_newline: .asciz "\n"
msg_fault:   .asciz "Raven: FAULT_TEST build, triggering a breakpoint\n"
msg_test_start: .asciz "Raven: running Rook test\n"
msg_test_done:  .asciz "Raven: test_main returned\n"
msg_idle:    .asciz "Raven: boot complete, idling\n"
