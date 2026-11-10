// The MMU and the caches (milestone 6a). Raven runs on an identity map: every virtual address is the
// physical one, so nothing else in the kernel has to know the MMU is on. The map says what each part of
// memory may do, and anything it does not name faults (the exception report names the address):
//
//   0x00000000 - 0x1FFFFFFF  RAM (512 MB: what every supported board has), Normal, read/write, never executed,
//                            except the kernel image, mapped in 4 KB pages: code read-only + executable,
//                            constants read-only, data and bss read/write
//   0x3F000000 - 0x3FFFFFFF  the BCM2837 peripherals, Device-nGnRE (accesses neither reordered nor merged)
//   0x40000000 - 0x7FFFFFFF  the ARM local peripherals (timer and mailbox control), Device-nGnRE
//   anything else            unmapped
//
// 4 KB granule, 39-bit addresses (T0SZ = 25), so the walk starts at level 1: L1 entries are 1 GB, L2 entries
// 2 MB, L3 entries 4 KB. The kernel image must end below 4 MB (link.ld asserts it): two L3 tables cover it.
// Everything is EL1 only (no EL0 access). The page tables are built here, at boot, from the section
// boundaries link.ld names, with the MMU off; caches are switched on together with the MMU.

    .section .text

// Descriptor bits: [1:0] 11 table or page, 01 block; [4:2] memory type index into MAIR (0 Device, 1 Normal);
// [7:6] AP: 00 EL1 read/write, 10 EL1 read-only (01 and 11 add EL0 access, used by domain.s); [9:8] shareability
// (11 inner, 10 outer); [10] access flag (must be set, else the first access faults); [11] nG: not global, so the
// translation belongs to the table's address-space ID (ASID 0 here; domain.s gives each domain its own, and a
// global entry would clash with a domain's entry for the same address); [53] PXN and [54] UXN: not executable
// at EL1 and at EL0.
    .equ NX,            0x0060000000000000      // PXN | UXN
    .equ DESC_PAGE_RW,  0x0060000000000F07      // page, Normal, inner shareable, AF, nG, read/write, never executed
    .equ DESC_PAGE_RO,  0x0060000000000F87      // the same, read-only
    .equ DESC_PAGE_RX,  0x0040000000000F87      // read-only, executable at EL1 (UXN set, PXN clear)
    .equ DESC_BLOCK_RW, 0x0060000000000F05      // 2 MB block, Normal, read/write, never executed
    .equ DESC_DEVICE,   0x0060000000000E01      // block, Device-nGnRE, outer shareable, AF, nG, never executed
    .equ DESC_TABLE,    0x3                     // points to the next level's table

    .equ MAIR_VALUE,    0xFF04                  // attribute 0: 0x04 Device-nGnRE; attribute 1: 0xFF Normal, write-back
    .equ TCR_VALUE,     0x803519                // T0SZ 25 (bits 5:0 = 0x19), walks inner/outer write-back (bits 11:8),
                                                // inner shareable (13:12), 4 KB granule (15:14 = 0), EPD1 (bit 23): no TTBR1
    .equ SCTLR_VALUE,   0x30d81805              // RES1 bits 0x30d00800, M (bit 0), C (bit 2), I (bit 12), WXN (bit 19):
                                                // writable memory is never executable. A (alignment) and SA stay off, as before

    .equ KERNEL_START,  0x80000                 // where the image is loaded (link.ld)
    .equ DEVICE_FIRST,  504                     // L2 index of 0x3F000000 (0x3F000000 >> 21)

// void mmu_enable(void): builds the tables and turns the MMU and the caches on. Uses x0-x9; keeps x19.
    .global mmu_enable
mmu_enable:
    ldr     x9, =page_tables            // L1 at +0, L2 at +0x1000, L3 for 0-2 MB at +0x2000, for 2-4 MB at +0x3000
    add     x1, x9, #0x1000             // x1 = L2
    add     x2, x9, #0x2000             // x2 = L3 (first of two, contiguous)

    // L1: entry 0 -> L2 (0 - 1 GB), entry 1 = a 1 GB Device block (0x40000000: the ARM local peripherals)
    orr     x3, x1, #DESC_TABLE
    str     x3, [x9]
    mov     x3, #0x40000000
    ldr     x4, =DESC_DEVICE
    orr     x3, x3, x4
    str     x3, [x9, #8]

    // L2: 2 MB blocks of RAM for the first 512 MB ...
    ldr     x4, =DESC_BLOCK_RW
    mov     x5, #0                      // x5 = index
1:  lsl     x6, x5, #21                 // the block's address
    orr     x6, x6, x4
    str     x6, [x1, x5, lsl #3]
    add     x5, x5, #1
    cmp     x5, #256
    b.lo    1b
    // ... Device blocks for 0x3F000000 - 0x3FFFFFFF (indices 504 - 511) ...
    ldr     x4, =DESC_DEVICE
    mov     x5, #DEVICE_FIRST
2:  lsl     x6, x5, #21
    orr     x6, x6, x4
    str     x6, [x1, x5, lsl #3]
    add     x5, x5, #1
    cmp     x5, #512
    b.lo    2b
    // ... and the first two blocks (0 - 4 MB, the kernel image) point to the L3 tables
    orr     x3, x2, #DESC_TABLE
    str     x3, [x1]
    add     x3, x2, #0x1000
    orr     x3, x3, #DESC_TABLE
    str     x3, [x1, #8]

    // L3: one entry per 4 KB page of 0 - 4 MB. Below the image and after the constants: read/write;
    // the image's code: read-only, executable; its constants: read-only; data and bss: read/write.
    ldr     x4, =__text_end
    ldr     x5, =__rodata_end
    ldr     x6, =DESC_PAGE_RW
    ldr     x7, =DESC_PAGE_RO
    ldr     x8, =DESC_PAGE_RX
    mov     x0, #0                      // x0 = page index
3:  lsl     x3, x0, #12                 // the page's address
    mov     x10, x6                     // default: read/write
    cmp     x3, #KERNEL_START
    b.lo    4f
    cmp     x3, x4
    b.hs    5f
    mov     x10, x8                     // [KERNEL_START, __text_end): code
    b       4f
5:  cmp     x3, x5
    b.hs    4f
    mov     x10, x7                     // [__text_end, __rodata_end): constants
4:  orr     x3, x3, x10
    str     x3, [x2, x0, lsl #3]
    add     x0, x0, #1
    cmp     x0, #1024
    b.lo    3b

    // Switch on. The tables were written with the data cache off, so memory has them; the walker reads
    // them through the cache, which holds nothing yet.
    ldr     x0, =MAIR_VALUE
    msr     mair_el1, x0
    ldr     x0, =TCR_VALUE
    msr     tcr_el1, x0
    msr     ttbr0_el1, x9
    dsb     ish
    isb
    tlbi    vmalle1                     // no stale translations
    dsb     ish
    ic      iallu                       // no stale instructions
    dsb     ish
    isb
    ldr     x0, =SCTLR_VALUE
    msr     sctlr_el1, x0
    isb
    ret

// u64 image_rodata_start(void): the address of the first page of the kernel's constants (read-only), for tests.
    .global image_rodata_start
image_rodata_start:
    ldr     x0, =__text_end
    ret

    .section .bss
    .balign 4096                        // a table is 4 KB and 4 KB aligned
    .global page_tables                 // domain.s keeps its address: the table Raven runs on
page_tables:
    .space  4 * 4096
