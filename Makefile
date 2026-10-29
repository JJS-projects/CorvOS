# CorvOS build. Run `make help` for the list of targets.

CROSS   ?= aarch64-linux-gnu-
AS      := $(CROSS)as
LD      := $(CROSS)ld
OBJCOPY := $(CROSS)objcopy
OBJDUMP := $(CROSS)objdump
PYTHON  ?= python3

BUILD   := build
ASFLAGS := -march=armv8-a
LDFLAGS := -T kernel/link.ld --no-warn-rwx-segments

SRCS := $(wildcard kernel/arch/aarch64/*.s) kernel/main.s

# Two builds of the same sources: the normal kernel, and a test build with a
# deliberate fault so the tests can check the exception vectors.
NORMAL_OBJS := $(SRCS:%.s=$(BUILD)/normal/%.o)
FAULT_OBJS  := $(SRCS:%.s=$(BUILD)/fault/%.o)

KERNEL       := $(BUILD)/kernel8.img
FAULT_KERNEL := $(BUILD)/fault/kernel8.img

.PHONY: all run test disasm sdcard clean help

all: $(KERNEL)

$(BUILD)/normal/%.o: %.s
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/fault/%.o: %.s
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) --defsym FAULT_TEST=1 -o $@ $<

$(BUILD)/kernel8.elf: $(NORMAL_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(NORMAL_OBJS)

$(BUILD)/fault/kernel8.elf: $(FAULT_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(FAULT_OBJS)

%.img: %.elf
	$(OBJCOPY) -O binary $< $@

run: $(KERNEL)
	tools/run-qemu $(KERNEL)

test: $(KERNEL) $(FAULT_KERNEL)
	$(PYTHON) tests/run_tests.py

disasm: $(BUILD)/kernel8.elf
	$(OBJDUMP) -d $< | less

sdcard: $(KERNEL)
	@mkdir -p $(BUILD)/sdcard
	cp $(KERNEL) tools/sdcard/config.txt $(BUILD)/sdcard/
	@echo "Kernel and config.txt are in $(BUILD)/sdcard/; see tools/sdcard/README.md"

clean:
	rm -rf $(BUILD)

help:
	@echo "make          build build/kernel8.img"
	@echo "make run      boot it in QEMU (quit with Ctrl-A then X)"
	@echo "make test     build everything and run the QEMU test suite"
	@echo "make disasm   disassemble the kernel"
	@echo "make sdcard   collect files for a real Pi Zero 2 W SD card"
	@echo "make clean    remove build output"
