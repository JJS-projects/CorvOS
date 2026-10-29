// Early console: the PL011 UART ("UART0") on GPIO 14/15.
//
// This is Raven's own debug console, used before actors exist. Later the
// UART is driven by a Rook driver actor holding an Mmio capability, and this
// stays only as the kernel's panic/debug output.
//
// Calling convention: arguments in x0, all functions may clobber x0-x15,
// and preserve x19-x28 (standard AAPCS64).
//
// On real hardware the PL011 is wired to Bluetooth by default; config.txt
// must contain dtoverlay=disable-bt (see tools/sdcard/config.txt).

    .equ MMIO_BASE,     0x3F000000
    .equ GPIO_BASE,     MMIO_BASE + 0x200000
    .equ GPFSEL1,       0x04
    .equ GPPUD,         0x94
    .equ GPPUDCLK0,     0x98

    .equ UART0_BASE,    MMIO_BASE + 0x201000
    .equ UART_DR,       0x00
    .equ UART_FR,       0x18
    .equ UART_IBRD,     0x24
    .equ UART_FBRD,     0x28
    .equ UART_LCRH,     0x2C
    .equ UART_CR,       0x30
    .equ UART_IMSC,     0x38
    .equ UART_ICR,      0x44
    .equ FR_TXFF,       5               // transmit FIFO full

    .section .text

// void uart_init(void)
// 115200 baud, 8N1, FIFOs on, assuming a 48 MHz UART clock
// (init_uart_clock=48000000 in config.txt; QEMU ignores the divisor).
    .global uart_init
uart_init:
    ldr     x1, =UART0_BASE
    str     wzr, [x1, #UART_CR]         // disable while configuring

    ldr     x2, =GPIO_BASE              // GPIO 14 and 15 -> ALT0 (TXD0, RXD0)
    ldr     w3, [x2, #GPFSEL1]
    ldr     w4, =((7 << 12) | (7 << 15))
    bic     w3, w3, w4
    ldr     w4, =((4 << 12) | (4 << 15))
    orr     w3, w3, w4
    str     w3, [x2, #GPFSEL1]

    str     wzr, [x2, #GPPUD]           // no pull-up/down on 14 and 15
    mov     x5, #150
1:  subs    x5, x5, #1
    b.ne    1b
    mov     w3, #((1 << 14) | (1 << 15))
    str     w3, [x2, #GPPUDCLK0]
    mov     x5, #150
2:  subs    x5, x5, #1
    b.ne    2b
    str     wzr, [x2, #GPPUDCLK0]

    mov     w3, #0x7ff                  // clear all pending interrupts
    str     w3, [x1, #UART_ICR]
    mov     w3, #26                     // 48e6 / (16 * 115200) = 26.04
    str     w3, [x1, #UART_IBRD]
    mov     w3, #3                      // 0.04 * 64 = 3
    str     w3, [x1, #UART_FBRD]
    mov     w3, #0x70                   // 8 bits, FIFOs enabled
    str     w3, [x1, #UART_LCRH]
    str     wzr, [x1, #UART_IMSC]       // no UART interrupts yet
    mov     w3, #0x301                  // enable UART, TX, RX
    str     w3, [x1, #UART_CR]
    ret

// void uart_putc(char c)
    .global uart_putc
uart_putc:
    ldr     x1, =UART0_BASE
1:  ldr     w2, [x1, #UART_FR]
    tbnz    w2, #FR_TXFF, 1b
    str     w0, [x1, #UART_DR]
    ret

// void uart_puts(const char *s)   -- '\n' is sent as "\r\n"
    .global uart_puts
uart_puts:
    stp     x29, x30, [sp, #-16]!
    mov     x29, sp
    mov     x9, x0
1:  ldrb    w0, [x9], #1
    cbz     w0, 3f
    cmp     w0, #'\n'
    b.ne    2f
    mov     w0, #'\r'
    bl      uart_putc
    mov     w0, #'\n'
2:  bl      uart_putc
    b       1b
3:  ldp     x29, x30, [sp], #16
    ret

// void uart_put_dec(u64 value)   -- prints the unsigned decimal value, no newline
    .global uart_put_dec
uart_put_dec:
    stp     x29, x30, [sp, #-16]!
    mov     x29, sp
    sub     sp, sp, #32                 // digit buffer (20 digits max), filled backwards
    mov     x9, sp
    add     x9, x9, #32                 // x9 = one past the end of the buffer
    mov     x10, #10
1:  udiv    x11, x0, x10                // x11 = value / 10
    msub    x12, x11, x10, x0           // x12 = value % 10
    add     x12, x12, #'0'
    strb    w12, [x9, #-1]!
    mov     x0, x11
    cbnz    x0, 1b
    mov     x13, sp
    add     x13, x13, #32
2:  ldrb    w0, [x9], #1                // print the digits in order
    bl      uart_putc
    cmp     x9, x13
    b.lo    2b
    mov     sp, x29
    ldp     x29, x30, [sp], #16
    ret

// void uart_put_hex(u64 value)   -- prints "0x" + 16 hex digits + newline
    .global uart_put_hex
uart_put_hex:
    stp     x29, x30, [sp, #-16]!
    mov     x29, sp
    mov     x9, x0
    mov     w0, #'0'
    bl      uart_putc
    mov     w0, #'x'
    bl      uart_putc
    mov     x10, #60                    // shift for the top nibble
1:  lsr     x0, x9, x10
    and     x0, x0, #0xf
    cmp     x0, #10
    b.lo    2f
    add     x0, x0, #('a' - '0' - 10)
2:  add     x0, x0, #'0'
    bl      uart_putc
    subs    x10, x10, #4
    b.pl    1b
    mov     w0, #'\r'
    bl      uart_putc
    mov     w0, #'\n'
    bl      uart_putc
    ldp     x29, x30, [sp], #16
    ret
