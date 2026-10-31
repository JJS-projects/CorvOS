# Bring-up checklist: Raven on a real Pi Zero 2 W

**Status: nothing here has run on hardware.** QEMU accepts things a real board
won't, so this lists every assumption Raven makes about the board, what backs it,
and how sure it is. Read it before the first boot, and update it after.

Statuses:

- **Verified**: I read a primary source (named) and it agrees.
- **Partly**: a primary source agrees on part of it; the rest is said.
- **Unverified**: an assumption. If it is wrong, the board will not behave.

Sources were read on 2026-10-30. "Manual" is the *BCM2835 ARM Peripherals*
datasheet; the Zero 2 W's chip (BCM2710A1) is in the BCM2837 family and uses the
same peripheral blocks at a different base address (item 5).

## What to look at first on the board

The activity LED shows how far Raven got, with or without a serial adapter
(decision 108):

| LED | Meaning |
|---|---|
| nothing, ever | The firmware never started Raven (SD card, `config.txt`, `kernel8.img` name), or Raven stopped before its first line of Rook |
| one flash | Raven's Rook code is running |
| then two flashes | The console (UART) has been set up. If the serial terminal shows nothing, suspect items 7 and 8 (wiring, baud rate, clock) |
| solid on | Boot complete: `Raven: boot complete, idling` was printed |
| flashes, then dark or part-way | Stopped after the last signal it gave |

A panic or fault does **not** change the LED yet: the emergency console is
assembly (decision 98), and a blink loop there would need its own test. Until
then a fault is only visible on the serial port.

## Checklist

| # | What Raven assumes | Evidence | Status |
|---|---|---|---|
| 1 | The firmware loads `kernel8.img` in 64-bit mode when `config.txt` has `arm_64bit=1` and `kernel=kernel8.img`, and jumps to its first byte at physical address 0x80000 (`kernel/link.ld`) | Official [config.txt page](https://www.raspberrypi.com/documentation/computers/config_txt.html) on `kernel`/`arm_64bit` (read as a search excerpt, not in full). 0x80000 is what other bare-metal projects use; I did not find it in an official document | **Partly** |
| 2 | `dtoverlay=disable-bt` gives the PL011 ("UART0") to GPIO 14 and 15 | Firmware [overlays README](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README): "Disable onboard Bluetooth ... restores UART0/ttyAMA0 over GPIOs 14 & 15" | **Verified** |
| 3 | `enable_uart=1` is harmless | The config.txt page says it requests a serial console and implies `core_freq=250` (that matters for the *mini* UART, which Raven doesn't use). Whether Raven needs it at all is not known | **Partly** |
| 4 | GPIO 14 and 15 on ALT0 are TXD0 and RXD0; their function fields are bits 12-14 and 15-17 of GPFSEL1; ALT0 is `100` | Manual, GPIO chapter: alternate function table, GPFSEL1 field table, function encodings | **Verified** |
| 5 | The peripherals sit at physical 0x3F000000 (GPIO 0x3F200000, UART0 0x3F201000) | Raspberry Pi [`bcm2837.dtsi`](https://github.com/raspberrypi/linux/blob/rpi-6.6.y/arch/arm/boot/dts/broadcom/bcm2837.dtsi): `ranges = <0x7e000000 0x3f000000 0x1000000>` maps the manual's bus addresses (0x7E...) to ARM addresses. The Zero 2 W's tree says `compatible = ... "brcm,bcm2837"` ([`bcm2710-rpi-zero-2-w.dts`](https://github.com/raspberrypi/linux/blob/rpi-6.6.y/arch/arm/boot/dts/broadcom/bcm2710-rpi-zero-2-w.dts)) | **Verified** |
| 6 | Pull-up/down is switched off for 14 and 15 with the sequence: write GPPUD, wait 150 cycles, write GPPUDCLK0, wait 150 cycles, clear GPPUDCLK0 (`raven/pl011.rook`) | Manual, GPPUDCLK registers: the six-step sequence. Raven does steps 1-4 and 6; step 5 (remove the control signal) is the same write as step 1 since the control is 0. `settle(150)` loops 150 times, and a loop iteration takes at least a cycle, so the wait is at least 150 cycles | **Verified** (QEMU ignores pulls entirely) |
| 7 | UART divisors for 115200 baud: IBRD 26, FBRD 3, which is right for a **48 MHz** UART clock | The formula `BAUDDIV = FUARTCLK / (16 x baud)` is in the manual (UART_FBRD). 48e6 / 1843200 = 26.04, and 0.04 x 64 = 2.7, which rounds to 3. **The 48 MHz clock is not verified** (item 8) | **Partly** |
| 8 | The UART clock really is 48 MHz, because `tools/sdcard/config.txt` sets `init_uart_clock=48000000` | I found no official statement of `init_uart_clock` or its default. A third-party mirror of old documentation says 48 MHz; a 2013 forum thread says the default was 3 MHz. At 3 MHz the divisors would be IBRD 1, FBRD 40. The kernel device trees don't say: the clock comes from the firmware at run time | **Unverified. The biggest risk for the serial port.** A wrong clock means a wrong baud rate and garbage or silence, with the LED still flashing |
| 9 | The transmit-FIFO-full flag is bit 5 of the flag register (`FR_TXFF = 0x20`) and `Pl011.put` waits on it | Manual, UART_FR: bit 5 "TXFF, Transmit FIFO full" | **Verified** the bit. The wait itself is **untested**: QEMU's UART sends instantly and never sets it |
| 10 | The activity LED is GPIO 29, active low (driven low it lights) | Mainline Linux [`bcm2837-rpi-zero-2-w.dts`](https://github.com/torvalds/linux/blob/master/arch/arm/boot/dts/broadcom/bcm2837-rpi-zero-2-w.dts): `&led_act { gpios = <&gpio 29 GPIO_ACTIVE_LOW>; }`. The Raspberry Pi tree's line-name list for the same board has `STATUS_LED_N` as the 30th entry (index 29), and its legend says a `_N` suffix means active low | **Verified** (two sources) |
| 11 | Pin 29's function field is bits 27-29 of GPFSEL2, and `001` makes it an output; GPSET0 is 0x1C, GPCLR0 is 0x28, GPLEV0 is 0x34 | Manual, GPIO chapter: register table and FSEL29 | **Verified**. QEMU's GPIO model does keep the pin level, so `rook/led` checks the writes land |
| 12 | The ARM generic timer ticks at `CNTFRQ_EL0`, which the firmware sets, and EL1 may read it (`start.s` sets `CNTHCTL_EL2`) | The EL1 access is architectural. That the firmware sets `CNTFRQ_EL0` on this board is **not checked here**: other bare-metal Pi 3 projects rely on it. If it reads 0, `wait_ms` falls back to 19.2 MHz, which is also unchecked | **Unverified**. If wrong, the LED flashes at the wrong speed, or `wait_ms` never returns (if the counter doesn't run) |
| 13 | The firmware enters at EL2 or EL3 and `start.s` brings Raven to EL1; cores 1-3 are held by the firmware and also parked by Raven | QEMU enters at EL2 and `boot/hello` prints it. What the real firmware does for a `kernel8.img` on this board is not checked | **Unverified** |
| 14 | With the MMU and caches off, every access Raven makes is aligned | With the MMU off, memory is treated as device memory, where unaligned accesses fault. `Mmio` already insists on 4-byte alignment; Rook values live in 8-byte slots; text is read a byte at a time. I did not check Fledgling's generated code for anything else, and I don't know if QEMU enforces this | **Unverified** |
| 15 | The board has 512 MB of RAM | Tests run on `raspi3ap` (512 MB) as well as `raspi3b`. The Zero 2 W's memory size is from its product page, which I did not re-read | **Partly** |
| 16 | The serial adapter is 3.3 V, with its RX on pin 8 (GPIO 14, TX), TX on pin 10 (GPIO 15, RX) and GND on pin 6 | `tools/sdcard/README.md` (written from the Pi header pinout; not checked against a schematic) | **Partly** |

| 17 | Receiving works by polling: `FR_RXFE` (bit 4 of the flag register) is clear when a byte waits, and reading the data register takes it; polling once per scheduler turn is faster than 115200 baud fills the 16-byte FIFO (`raven/pl011.rook`, `UartReader`) | Manual, UART_FR "RXFE, Receive FIFO empty", UART_DR. QEMU's PL011 model accepts typed bytes and the echo demo passes on both machines | **Verified** the bit. **Untested on a board**: receive overruns, framing errors (the error bits in the data register are ignored), and whether the polling loop keeps up when other actors are busy. The send-cost numbers in docs/design.md 6.2 come from QEMU's timer and are not real times |

| 18 | The handler time budget works through the ARM local interrupt controller: bit 1 of `CORE0_TIMER_IRQCNTL` (0x40000040) sends the EL1 physical timer (`CNTP_*`) to core 0 as an IRQ, which Raven takes at EL1 (`kernel/arch/aarch64/actors.s`, `vectors.s`) | The BCM2836 ARM-local peripherals document (the same block the Pi 3 and the Zero 2 W's BCM2710A1 use, per mainline Linux's `bcm2836-l1-intc`); QEMU's `bcm2836_control` model, on which both test machines pass | **Partly**. The register and bit are from the documents and QEMU agrees; **untested on a board**: that the firmware leaves the timer unmasked for EL1, and that 100 ms is 1.92 million ticks of whatever `CNTFRQ_EL0` really is (item 12). If the interrupt never arrives, a runaway handler hangs the core as it did before; if it arrives early, ordinary handlers are reported as failed |

## Reading the board's serial port with the test harness

`tests/run_tests.py` can read boot output from a serial port instead of QEMU's
(decision 110):

| `CORVOS_SERIAL` | Output comes from |
|---|---|
| unset | QEMU's own output (what `make test` uses) |
| `pty` | QEMU's serial port on a pseudo-terminal, read the way a real port is. Slower to start, and used to test the harness |
| `/dev/ttyUSB0` (or similar) | A real board. Only `boot/hello` runs: the kernel is whatever is on the SD card |

On a board, start (or reset) it after the harness says it is reading:

```
CORVOS_SERIAL=/dev/ttyUSB0 python3 tests/run_tests.py boot/hello
```

**Not built yet:** flashing a given image, and resetting the board by itself. That
is the "real hardware in CI" step in [roadmap.md](roadmap.md#later-real-hardware-in-ci),
which needs the board, a USB-serial adapter and a way to power-cycle it.

## If I could change one thing first

Item 8. Instead of assuming the UART clock, Raven should ask the firmware for it
(over the mailbox property interface; I believe the request is "get clock rate" for
the UART clock, but I have not checked that against its documentation) and work out
the divisors. That removes the largest guess on this list, but it needs a mailbox
driver, so it is a separate piece of work.
