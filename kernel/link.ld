/*
 * Raven linker script.
 *
 * The Pi firmware (and QEMU's raspi3b machine) loads a 64-bit kernel8.img
 * at physical address 0x80000 and jumps to its first byte, with the MMU off.
 */

ENTRY(_start)

SECTIONS
{
    . = 0x80000;

    .text : {
        KEEP(*(.text.boot))     /* _start must be the first instruction */
        *(.text .text.*)
    }

    /* The MMU (kernel/arch/aarch64/mmu.s) gives code, constants and data different rights, 4 KB at a time,
       so each starts on a page of its own. */
    . = ALIGN(4096);
    __text_end = .;

    .rodata : ALIGN(8) {
        *(.rodata .rodata.*)
    }

    . = ALIGN(4096);
    __rodata_end = .;

    .data : ALIGN(8) {
        *(.data .data.*)
    }

    .bss (NOLOAD) : ALIGN(16) {
        __bss_start = .;
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        __bss_end = .;
    }

    /* mmu.s maps the first 4 MB with 4 KB pages */
    ASSERT(__bss_end <= 0x400000, "the kernel image does not fit the 4 MB the MMU maps in pages")

    /DISCARD/ : {
        *(.comment)
        *(.note*)
    }
}
