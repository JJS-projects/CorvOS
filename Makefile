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
ROOK_TEST_SUPPORT := tests/rook/support/trusted/print.rook tests/rook/support/shell.rook tests/rook/support/generic_library.rook
ROOK_TEST_TRUSTED := $(RAVEN_TRUSTED) --trusted tests/rook/support/trusted \
                     --trusted tests/rook/trusted

# One kernel image per Rook test program in tests/rook/ and tests/rook/trusted/.
ROOK_TESTS     := $(wildcard tests/rook/*.rook tests/rook/trusted/*.rook)
ROOK_TEST_IMGS := $(ROOK_TESTS:tests/rook/%.rook=$(BUILD)/rook-tests/%.img)

.PHONY: all run test quick difftest selfhost-check coverage disasm sdcard clean help bootstrap rook-kernel rook-compilers
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

# Test images: a Rook program (from $(2)) built into a test kernel under
# $(BUILD)/$(1). Used for the tests in tests/rook and the generated programs
# of `make difftest`.
define ROOK_IMAGE_RULES
$(BUILD)/$(1)/%.s: $(2) $(3) $(FLEDGLING_SRCS) $(RAVEN_ROOK) $(ROOK_TEST_SUPPORT) Makefile
	@mkdir -p $$(dir $$@)
	$(FLEDGLING) $(ROOK_TEST_TRUSTED) $(RAVEN_ROOK) $(ROOK_TEST_SUPPORT) $(3) $$< -o $$@

$(BUILD)/$(1)/%.o: $(BUILD)/$(1)/%.s
	$(AS) $(ASFLAGS) -o $$@ $$<

$(BUILD)/$(1)/%.elf: $(BUILD)/$(1)/%.o $(ROOKTEST_KERNEL_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $$@ $(ROOKTEST_KERNEL_OBJS) $$<
endef
$(eval $(call ROOK_IMAGE_RULES,rook-tests,tests/rook/%.rook))
$(eval $(call ROOK_IMAGE_RULES,difftest,$(BUILD)/difftest/%.rook))
# The self-hosted compiler's programs (milestone 5), each run hosted: see docs/self-hosting.md.
COMPILER_LIB := $(wildcard compiler/lib/*.rook)
$(eval $(call ROOK_IMAGE_RULES,compiler,compiler/%.rook,$(COMPILER_LIB)))
COMPILER_IMGS := $(patsubst compiler/%.rook,$(BUILD)/compiler/%.img,$(wildcard compiler/*.rook))

# Stage 2 of the self-hosted compiler (milestone 5e, tools/selfhost-stage2.py): the assembly the Rook compiler
# wrote for itself (build/stage2/NAME.s) built into an image the way the compiler programs above are.
$(BUILD)/stage2/%.o: $(BUILD)/stage2/%.s
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/stage2/%.elf: $(BUILD)/stage2/%.o $(ROOKTEST_KERNEL_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(ROOKTEST_KERNEL_OBJS) $<

# Milestone 5f (decision 170): the same programs built by the Rook compiler instead of Fledgling.
# `make bootstrap` makes the stage-2 compiler image: Fledgling builds the compiler (above), the compiler
# writes its own assembly, and that assembly is the image (tools/selfhost-stage2.py checks that this
# compiler gives the same assembly again). tools/rookc.py runs it in QEMU; results are cached in build/cache/.
ROOKC := $(PYTHON) tools/rookc.py
STAGE2_IMG := $(BUILD)/stage2/asmdump.img
$(BUILD)/stage2/asmdump.s: $(COMPILER_LIB) compiler/asmdump.rook $(RAVEN_ROOK) $(ROOK_TEST_SUPPORT) $(BUILD)/compiler/asmdump.img tools/selfhost-stage2.py
	$(PYTHON) tools/selfhost-stage2.py --assembly-only --skip-fledgling

bootstrap: $(STAGE2_IMG)

$(BUILD)/rook/raven.s: $(RAVEN_ROOK) $(STAGE2_IMG) tools/rookc.py
	@mkdir -p $(dir $@)
	$(ROOKC) $(RAVEN_TRUSTED) $(RAVEN_ROOK) -o $@

$(BUILD)/rook/raven.o: $(BUILD)/rook/raven.s
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/rook/kernel8.elf: $(SRCS:%.s=$(BUILD)/normal/%.o) $(BUILD)/rook/raven.o kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(SRCS:%.s=$(BUILD)/normal/%.o) $(BUILD)/rook/raven.o

ROOK_KERNEL := $(BUILD)/rook/kernel8.img
rook-kernel: $(ROOK_KERNEL)

$(BUILD)/rook/compiler/%.s: compiler/%.rook $(COMPILER_LIB) $(RAVEN_ROOK) $(ROOK_TEST_SUPPORT) $(STAGE2_IMG) tools/rookc.py
	@mkdir -p $(dir $@)
	$(ROOKC) $(ROOK_TEST_TRUSTED) $(RAVEN_ROOK) $(ROOK_TEST_SUPPORT) $(COMPILER_LIB) $< -o $@

$(BUILD)/rook/compiler/%.o: $(BUILD)/rook/compiler/%.s
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/rook/compiler/%.elf: $(BUILD)/rook/compiler/%.o $(ROOKTEST_KERNEL_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(ROOKTEST_KERNEL_OBJS) $<

ROOK_COMPILER_IMGS := $(patsubst compiler/%.rook,$(BUILD)/rook/compiler/%.img,$(wildcard compiler/*.rook))
rook-compilers: $(ROOK_COMPILER_IMGS)

$(BUILD)/raven/raven.s: $(RAVEN_ROOK) $(FLEDGLING_SRCS) Makefile
	@mkdir -p $(dir $@)
	$(FLEDGLING) $(RAVEN_TRUSTED) $(RAVEN_ROOK) -o $@

$(BUILD)/raven/raven.o: $(BUILD)/raven/raven.s
	$(AS) $(ASFLAGS) -o $@ $<



$(BUILD)/kernel8.elf: $(NORMAL_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(NORMAL_OBJS)

$(BUILD)/fault/kernel8.elf: $(FAULT_OBJS) kernel/link.ld
	$(LD) $(LDFLAGS) -o $@ $(FAULT_OBJS)

%.img: %.elf
	$(OBJCOPY) -O binary $< $@

run: $(KERNEL)
	tools/run-qemu $(KERNEL)

test: $(KERNEL) $(FAULT_KERNEL) $(ROOK_TEST_IMGS) $(COMPILER_IMGS) $(ROOK_KERNEL) $(BUILD)/rook/compiler/numberer.img
	$(PYTHON) tests/run_tests.py

# A faster loop: only the tests named in T (names or prefixes), default every Rook
# program and error test; skips the boot and harness tests (about 20 s of one-at-a-time
# work). `make quick T=rook/restart` while working, `make test` before a push.
T ?= rook/ error/
quick: $(KERNEL) $(FAULT_KERNEL) $(ROOK_TEST_IMGS) $(COMPILER_IMGS) $(ROOK_KERNEL) $(BUILD)/rook/compiler/numberer.img
	$(PYTHON) tests/run_tests.py $(T)

# Differential testing: random integer arithmetic, checked against a model
# written in Python. SEED picks the programs; the same seed gives the same ones.
SEED     ?= 1
PROGRAMS ?= 12
difftest: $(KERNEL)
	$(PYTHON) tools/difftest.py --seed $(SEED) --programs $(PROGRAMS)

# Every self-hosted compiler stage against Fledgling on every Rook file (slow; see tools/selfhost-check.py).
selfhost-check: $(KERNEL) $(FAULT_KERNEL) $(ROOK_TEST_IMGS) $(COMPILER_IMGS)
	$(PYTHON) tools/selfhost-check.py lexdump
	$(PYTHON) tools/selfhost-check.py parsedump
	$(PYTHON) tools/selfhost-check.py expanddump
	$(PYTHON) tools/selfhost-check.py declsdump
	$(PYTHON) tools/selfhost-check.py bodiesdump
	$(PYTHON) tools/selfhost-check.py asmdump

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
	@echo "make bootstrap     build the stage-2 compiler (the Rook compiler built from its own assembly)"
	@echo "make rook-kernel   build build/rook/kernel8.img with the Rook compiler (also: rook-compilers)"
	@echo "make test     build everything and run the full test suite"
	@echo "              (python3 tests/run_tests.py NAME... runs only some tests)"
	@echo "make quick  only some tests (T=\"rook/restart\"; default every Rook and error test)"
	@echo "make difftest random arithmetic checked against a Python model (SEED=n PROGRAMS=n)"
	@echo "make coverage  list the compiler lines no test reaches"
	@echo "make disasm   disassemble the kernel"
	@echo "make sdcard   collect files for a real Pi Zero 2 W SD card"
	@echo "make clean    remove build output"
