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

FLEDGLING_SRCS := $(wildcard bootstrap/fledgling/*.py)
FLEDGLING      := PYTHONPATH=bootstrap PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m fledgling

SRCS := $(wildcard kernel/arch/aarch64/*.s) kernel/panic.s kernel/test_hooks.s

# Raven's Rook code: everything under raven/, compiled as one program.
# Only raven/trusted/ may use `unsafe` and `extern fn` (decision 90).
RAVEN_ROOK    := $(wildcard raven/trusted/*.rook raven/*.rook)
RAVEN_TRUSTED := --trusted raven/trusted
RAVEN_OBJ     := $(BUILD)/raven/raven.o

# Builds of the same kernel sources:
#   normal   the real kernel
#   fault    test build with a deliberate fault, to test the exception vectors
#   rooktest test build that runs a compiled Rook program's test_main()
NORMAL_OBJS := $(SRCS:%.s=$(BUILD)/normal/%.o) $(RAVEN_OBJ)
FAULT_OBJS  := $(SRCS:%.s=$(BUILD)/fault/%.o) $(RAVEN_OBJ)
ROOKTEST_KERNEL_OBJS := $(SRCS:%.s=$(BUILD)/rooktest/%.o) $(BUILD)/rooktest/kernel/test/print.o

KERNEL       := $(BUILD)/kernel8.img
FAULT_KERNEL := $(BUILD)/fault/kernel8.img

# A test image's Rook code is Raven's plus the test output functions plus the
# test program, compiled as one program. Test programs that need trust live in
# tests/rook/trusted/.
ROOK_TEST_SUPPORT := tests/rook/support/trusted/print.rook
ROOK_TEST_TRUSTED := $(RAVEN_TRUSTED) --trusted tests/rook/support/trusted \
                     --trusted tests/rook/trusted

# One kernel image per Rook test program in tests/rook/ and tests/rook/trusted/.
ROOK_TESTS     := $(wildcard tests/rook/*.rook tests/rook/trusted/*.rook)
ROOK_TEST_IMGS := $(ROOK_TESTS:tests/rook/%.rook=$(BUILD)/rook-tests/%.img)

.PHONY: all run test coverage disasm sdcard clean help
# Keep generated assembly, so it can be read when reviewing or debugging.
.SECONDARY:

all: $(KERNEL)

# Every generated file depends on this Makefile, so changing a flag or a rule
# (or switching to a branch that has) rebuilds it instead of reusing objects
# built the old way.
$(BUILD)/normal/%.o: %.s Makefile
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/fault/%.o: %.s Makefile
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) --defsym FAULT_TEST=1 -o $@ $<

$(BUILD)/rooktest/%.o: %.s Makefile
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) --defsym ROOK_TEST=1 -o $@ $<

$(BUILD)/raven/raven.s: $(RAVEN_ROOK) $(FLEDGLING_SRCS) Makefile
	@mkdir -p $(dir $@)
	$(FLEDGLING) $(RAVEN_TRUSTED) $(RAVEN_ROOK) -o $@

$(BUILD)/raven/raven.o: $(BUILD)/raven/raven.s
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/rook-tests/%.s: tests/rook/%.rook $(FLEDGLING_SRCS) $(RAVEN_ROOK) $(ROOK_TEST_SUPPORT) Makefile
	@mkdir -p $(dir $@)
	$(FLEDGLING) $(ROOK_TEST_TRUSTED) $(RAVEN_ROOK) $(ROOK_TEST_SUPPORT) $< -o $@

$(BUILD)/rook-tests/%.o: $(BUILD)/rook-tests/%.s
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/rook-tests/%.elf: $(BUILD)/rook-tests/%.o $(ROOKTEST_KERNEL_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(ROOKTEST_KERNEL_OBJS) $<

$(BUILD)/kernel8.elf: $(NORMAL_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(NORMAL_OBJS)

$(BUILD)/fault/kernel8.elf: $(FAULT_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(FAULT_OBJS)

%.img: %.elf
	$(OBJCOPY) -O binary $< $@

run: $(KERNEL)
	tools/run-qemu $(KERNEL)

test: $(KERNEL) $(FAULT_KERNEL) $(ROOK_TEST_IMGS)
	$(PYTHON) tests/run_tests.py

coverage:
	$(PYTHON) tools/coverage.py

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
	@echo "make test     build everything and run the full test suite"
	@echo "              (python3 tests/run_tests.py NAME... runs only some tests)"
	@echo "make coverage  list the compiler lines no test reaches"
	@echo "make disasm   disassemble the kernel"
	@echo "make sdcard   collect files for a real Pi Zero 2 W SD card"
	@echo "make clean    remove build output"
