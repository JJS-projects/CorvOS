# The portability plan

Prepares CorvOS so that a later port, to RISC-V first, is mostly additive. No RISC-V
code is written under this plan. Decision 220 records it: the scope and the thin emit
layer (rather than a full intermediate representation) are the owner's choices; the
rest is derived and open to review. The contract it produces is
[porting.md](porting.md).

## Status

| Phase | What | State |
|---|---|---|
| 0 | Groundwork: decision 220, this plan, `tools/image-identity.py`, the skeleton of [porting.md](porting.md) | in review |
| 1 | A board layer | not started |
| 2 | The architecture contract | not started |
| 3 | A harness that knows about architectures | not started |
| 4 | The thin emit layer, in both compilers | not started; **4.1 goes to the owner before anything is built** |

## The rule every PR keeps: nothing observable changes

Every step is a refactor, and each PR proves it three ways, with all three pasted into
the PR:

1. **`make test` passes.**
2. **Every built image is byte-identical to `main`'s:** `build/kernel8.img`, every test
   image under `build/rook-tests/`, and the compiler images under `build/compiler/`
   (`tools/image-identity.py`, which also compares `build/fault/kernel8.img`, Raven and
   the numberer built by the Rook compiler, and the stage-2 compiler).
3. **The compilers' output is unchanged:** the hosted `asmdump` cases, and the
   `selfhost/stage2` test.

In phase 4, `make selfhost-check` also runs once per PR (about 47 minutes, in the
background).

The comparison compares like with like: a build was checked to be deterministic before it
was relied on (built twice, at two paths, compared; see decision 220). A step that would
have to change a binary says why, adds a test, and gets the owner's agreement.

## Where things stand (at the start, after #97)

| What | AArch64 or board details inside it |
|---|---|
| `kernel/arch/aarch64/*.s`, `kernel/panic.s`, `kernel/test_hooks.s` (~2,200 lines) | Machine-level code mixed with BCM2837 board details. Interrupt-controller and mailbox addresses (`0x3F00B2xx`, `0x400000xx`) are in `vectors.s`, `domain.s`, `rings.s` and `actors.s`; the spin table is in `start.s` |
| `raven/trusted/assembly.rook` (68 `extern fn`s) | The boundary, with no written contract. AArch64 names: `domain_saved_x(n)`, `domain_fault_esr`, `domain_fault_far`, `domain_fault_elr`, `domain_pool_page_el1` |
| Raven's Rook code | Board numbers inline: `0x3F000000` (`devices.rook`, `mailbox.rook`), `0xC0000000` (`dma.rook`), the region base (`region.rook`), the counter's default 19.2 MHz (`time.rook`, `watchdog.rook`). AArch64 register numbers: `domain_saved_x(0..6)` for a domain call's arguments, `domain_saved_x(8)` for its struct-result address, `domain_saved_x(0) and 1` for a turn's result. `shr 21` for 2 MB pools, in four places in `mailbox.rook` |
| Fledgling's `codegen.py` (956 lines, 172 `emit` calls) and the self-hosted `codegen.rook` (1,862 lines, ~260 `emit`/`ob` calls) | AArch64 text written directly, with register names (`x0`, `x8`, `x16`, `x17`, `x29`, `x30`) and overflow checks through condition flags (`b.vs`, `umulh`, `cset`) |
| Makefile and `tests/run_tests.py` | `qemu-system-aarch64`, `MACHINES = raspi3b raspi3ap`, `CROSS = aarch64-linux-gnu-`, `SRCS = kernel/arch/aarch64/*.s` |

**Checked at phase 0** (the counts above hold: 68 externs, 2,203 lines of assembly, 4
`shr 21`). Three places the table does not name, for phases 1 and 2: `mmu.s` also holds
board addresses (the peripheral range `0x3F000000`–`0x3FFFFFFF` as L2 index 504, and the
local peripherals at `0x40000000`); `raven/main.rook` has a third copy of the counter's
19.2 MHz; `region.rook` already names `REGION_BASE` (`0x4000000`), locally.

## Phase 0: groundwork (1 PR)

- **0.1** Decision 220, and this file.
- **0.2** `tools/image-identity.py`: build `main` and the branch (or compare against a
  saved set of images), compare every image byte by byte, and list any that differ. A
  separate worktree (outside the repository, in a scratch directory) holds `main`, so the
  working tree isn't disturbed.
- **0.3** A skeleton [porting.md](porting.md), with sections for the board contract, the
  architecture contract, the backend contract, and what a port must supply.

## Phase 1: a board layer (2–3 PRs)

- **1.1** A new `raven/trusted/board.rook` holds the Pi Zero 2 W's facts:
  `PERIPHERALS_BASE` and its size, `DMA_BUS_ALIAS`, `RAM_BYTES`, `REGION_BASE`,
  `COUNTER_DEFAULT_HZ`, and the UART's base. The six files above import from it. A new
  test, `harness/board_numbers`, fails if any of those numbers appears in another Rook
  file.
- **1.2** A new `kernel/board/pizero2w/board.inc` (pulled in with `.include`) names the
  board addresses now written inline in the assembly. The Makefile gains
  `BOARD ?= pizero2w`.
- **1.3** The board-specific interrupt and mailbox code becomes board macros (not calls,
  so the hot paths stay identical):
  - the peripheral-interrupt read and acknowledge in `irq_entry` and `domain_irq`;
  - the mailbox write in `ring_push`;
  - `cores_release`;
  - `irq_init`.

## Phase 2: the architecture contract (2 PRs)

**2.1** Write the contract in [porting.md](porting.md). Give each of the 68 externs a
group, a contract, and its callers. The groups: domain entry and exit; address spaces;
cores; atomics and ordering; time; board.

Then list what Raven assumes of any architecture. State each assumption as a
requirement, followed by the AArch64 way of meeting it:
- 64-bit, little-endian;
- 4 KB pages, and 2 MB pools naturally aligned;
- at least 8 argument registers;
- a domain switch leaves no stale translation (ASIDs are not required: RISC-V may have
  none);
- these orderings: a ring slot is visible before its tail; a domain's state is written
  with a release and read with an acquire; a ring record is visible before the interrupt
  that announces it; atomic add.

Restate [hardware.md](hardware.md) items 22, 23, 27–29 and 32 the same way.

**2.2** Remove the AArch64 names from Rook code. The assembly behind each renamed
function stays the same instructions under a new label.

| Today | After |
|---|---|
| `domain_saved_x(i)` (arguments) | `domain_call_arg(i)` |
| `domain_saved_x(8)` (struct result) | `domain_call_result_address()` |
| `domain_saved_x(0) and 1` (a turn's result) | `domain_turn_result()` |
| `domain_fault_esr`, `domain_fault_far`, `domain_fault_elr` | `domain_fault_cause`, `domain_fault_address`, `domain_fault_pc` |
| `domain_pool_page_el1` | `domain_pool_page_kernel_only` |
| `shr 21` | `shr POOL_SHIFT`, in a small `raven/trusted/arch.rook` |
| "EL0", "EL1" in Rook-facing names and comments | "user mode", "kernel mode" |

## Phase 3: a harness that knows about architectures (1–2 PRs)

- **Makefile:** `ARCH ?= aarch64` and `BOARD ?= pizero2w`. `SRCS` comes from
  `kernel/arch/$(ARCH)` and `kernel/board/$(BOARD)`; `CROSS` and `QEMU` come from a table
  per architecture.
- **`tests/run_tests.py`:**
  - A table of architectures, giving each one's QEMU binary, machines and cross prefix.
    `MACHINES` comes from it; the `MACHINES=` override still works.
  - The hosted `asmdump` cases are kept per backend (`tests/hosted/asmdump/aarch64/`).
    Their generators, `tools/asmdump-cases.py` and `tools/separate-cases.py`, write there.
  - `harness/machines` checks the machines of each architecture.
- **Proof:** the same tests run, on the same machines, with the same results.

## Phase 4: the thin emit layer, in both compilers (6–8 PRs; about one milestone)

The code generators keep their design: an accumulator, stack slots, pushed temporaries.
What changes is that they say what to do, not how AArch64 spells it.

**4.1 Stop here and bring this to the owner first.** Write `docs/backend.md`. It must
define two things.

*Register roles instead of register names.* AArch64 today:

| Role | AArch64 |
|---|---|
| accumulator | `x0` |
| arguments 0–7 | `x0`–`x7` |
| indirect result | `x8` |
| two scratch registers | `x16`, `x17` |
| frame pointer, link register, stack pointer | `x29`, `x30`, `sp` |

This makes Rook's own calling convention explicit for each target. It only needs to
match the assembly at the `assembly.rook` boundary.

*About 35 operations, each one Rook meaning rather than one instruction:*

| Kind | Operations |
|---|---|
| Moving values | `load_imm`, `move`, `load(width, signed)`, `store(width)`, `address_of(symbol)`, `push_temp`, `pop_temp` |
| Arithmetic | `add_checked`, `sub_checked`, `mul_checked`, `neg_checked`, `div_checked`, `rem_checked`, `shl_checked`, `shr`, `and`, `or`, `xor`, `not`, `normalise(width, signed)`, `wrap` |
| Compare and branch | `compare_set(condition)`, `branch`, `branch_if_zero`, `branch_if_nonzero`, `branch_if(condition)`, `label` |
| Calls and frames | `call(symbol)`, `call_indirect`, `return`, `prologue(frame size)`, `epilogue`, `large_frame` |
| Data | `section`, `align`, `bytes`, `word`, the handler-table entry, `global` |
| Failure | `panic_if(kind)`, the panic stub, `trap` |

The checked operations are single operations on purpose. AArch64 checks overflow with
condition flags; RISC-V has none and needs a short sequence. The owner is shown
before/after samples of `codegen.py` for a few constructs. No Rook syntax changes.

**4.2 Fledgling** (2–3 PRs, by area: expressions and arithmetic; control flow and calls;
data and frames).
- A new `bootstrap/fledgling/target_aarch64.py` produces the text; `codegen.py` calls
  operations only.
- A harness test fails if `codegen.py` contains an instruction mnemonic or a register
  name.

**4.3 The self-hosted compiler** (2–3 PRs, the same areas).
- A new `compiler/lib/target_aarch64.rook` holds the text; `codegen.rook` calls
  operations only.
- The same harness test applies.
- **Proof:** the hosted `asmdump` comparisons pass, `selfhost/stage2` gives identical
  output, and `make selfhost-check` passes.

**4.4 A test-only "listing" target**, in both compilers. It prints each operation and its
arguments instead of AArch64. A harness test compiles every test program with it and
fails if any AArch64 text gets through, which proves the layer is complete without
writing RISC-V.

## After the plan

A RISC-V port would be four additions:
- `target_riscv64` in each compiler;
- `kernel/arch/riscv64/`, written to the contract (SBI, S-mode traps, `ecall`, Sv39,
  `sfence.vma`, LR/SC and AMO, the SBI timer, inter-core interrupts);
- a board for QEMU's RISC-V `virt` machine (PLIC, UART, memory map);
- a `riscv64` row in the harness.
