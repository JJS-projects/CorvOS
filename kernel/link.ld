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

    .rodata : ALIGN(8) {
        *(.rodata .rodata.*)
    }

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

    /DISCARD/ : {
        *(.comment)
        *(.note*)
    }
}
