// The board's addresses, for the assembly: the Raspberry Pi Zero 2 W (BCM2837; QEMU's raspi3b and raspi3ap).
// Pulled in with `.include "board.inc"`; the Makefile finds it through -I kernel/board/$(BOARD) (decision 222,
// docs/porting.md, the board contract). The same facts for Rook are in raven/trusted/board.rook, and
// harness/board_assembly checks that the two agree and that no file under kernel/ writes one of these addresses itself.
//
// Only names here: what the code does with them (which bit, which core's register) stays where it is used.

// Where the firmware loads kernel8.img and jumps to its first byte (kernel/link.ld places the image there).
    .equ KERNEL_START,          0x80000

// The RAM every supported board has: 512 MB, mapped by the MMU from 0 (mmu.s).
    .equ RAM_BYTES,             0x20000000

// The BCM2837 peripherals: 16 MB of device registers, up to the ARM local peripherals.
    .equ PERIPHERALS_BASE,      0x3F000000
    .equ PERIPHERALS_END,       0x40000000
    .equ UART0_BASE,            PERIPHERALS_BASE + 0x201000     // the PL011
    .equ IRQ_PENDING_2,         PERIPHERALS_BASE + 0xB208       // the interrupt controller: pending, sources 32 to 63
    .equ IRQ_ENABLE_2,          PERIPHERALS_BASE + 0xB214       // enable, sources 32 to 63 (write 1s)
    .equ IRQ_DISABLE_2,         PERIPHERALS_BASE + 0xB220       // disable, sources 32 to 63 (write 1s)

// The ARM local peripherals (the per-core interrupt routing and the mailboxes between cores): 1 GB from here is
// mapped as Device memory. Each per-core register below is core 0's; core n's is 4 * n (16 * n for a mailbox) further.
    .equ LOCAL_PERIPHERALS_BASE, 0x40000000
    .equ CORE0_TIMER_IRQCNTL,   0x40000040      // which of core 0's timers interrupt it
    .equ CORE0_MBOX_IRQCNTL,    0x40000050      // which of core 0's mailboxes interrupt it
    .equ CORE0_IRQ_SOURCE,      0x40000060      // why core 0 was interrupted
    .equ CORE0_MBOX0_SET,       0x40000080      // core 0's mailbox 0, write-set: raises its interrupt
    .equ CORE0_MBOX0_CLEAR,     0x400000C0      // core 0's mailbox 0, write-clear

// The firmware's spin table: core 1 waits for an address at SPIN_TABLE, cores 2 and 3 at the next two words.
    .equ SPIN_TABLE,            0xe0
