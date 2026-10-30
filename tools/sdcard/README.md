# Running on a real Pi Zero 2 W

**Status: untested on hardware.** QEMU is the reference until milestone 8.
Before the first boot read [docs/hardware.md](../../docs/hardware.md): it lists what
Raven assumes about the board, how sure each assumption is, and what the activity
LED means.

1. Format a micro SD card as FAT32.
2. Copy these files from the Raspberry Pi firmware repository
   (`raspberrypi/firmware`, `boot/` folder): `bootcode.bin`, `start.elf`,
   `fixup.dat`, and `bcm2710-rpi-zero-2-w.dtb`. Also copy `overlays/disable-bt.dtbo`
   into an `overlays/` folder on the card.
3. Run `make sdcard` and copy `build/sdcard/kernel8.img` and `build/sdcard/config.txt`
   to the card.
4. Connect a 3.3 V USB-serial adapter: Pi pin 8 (GPIO 14, TX) to adapter RX,
   pin 10 (GPIO 15, RX) to adapter TX, pin 6 to GND. **Not 5 V.**
5. Open a terminal at 115200 baud, 8N1 (e.g. `screen /dev/ttyUSB0 115200`) and
   power the Pi. You should see the same `Raven:` lines as in QEMU.
6. No serial output? Watch the activity LED: one flash means Raven started, two
   mean its console is up, solid on means boot complete (docs/hardware.md).
