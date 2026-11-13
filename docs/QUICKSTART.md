# Quickstart for agents

Read this page first. It is enough to start; open the full docs only for what the
task touches (map at the bottom). `CLAUDE.md` has the rules that never change.

## State

- Milestones 1–4 are merged: boot, Fledgling (the Python bootstrap compiler in
  `bootstrap/fledgling/`), actors and Raven (`raven/`, trusted parts in `raven/trusted/`),
  ownership, imports, the capability demo.
- Milestone 7 (supervision): restarts (7a, decision 148), `watch` (7b, 149), `stop`/`Gone`/`stopped`
  (7c, 150) and a supervisor written in Rook (7d, 151; `tests/rook/supervisor_demo.rook`,
  `docs/fault-tolerance.md`) are done. 7e (decision 182): a `separate` actor whose `new` takes a `val Mmio` has those device pages mapped into its domain (`separate_device*`). 7f (decision 183): `Irq.route` to a `separate` actor on any core, core 0 wakes for ring records through its mailbox interrupt (`separate_interrupt*`). 7g (decision 184): the UART driver as a `separate` actor, killed mid-use and restarted (`separate_uart_driver`). 7h (decision 185): `dma_window` (raven/trusted/dma.rook) checks a DMA buffer named as an offset into a driver's pool; 7j (decision 189): `DmaService` copies inside a driver's pool through QEMU's DMA controller (`trusted/dma_copy`). Milestone 7 is complete.
- Milestone 5 (self-hosting) is done ([self-hosting.md](self-hosting.md)). The compiler is written in Rook
  (`compiler/`, `compiler/lib/`: lexer, parser, generics pass, checker, code generator; `make selfhost-check`
  compares each stage with Fledgling). It compiles itself twice with identical output (5e, `selfhost/stage2`,
  decision 169), and the kernel and the compiler programs are built by it (5f: `make bootstrap`,
  `tools/rookc.py`, `make rook-kernel`, decision 170). The test programs are still built by Fledgling, which
  stays as the seed and the reference. Ownership 4c (`freeze`/pool) is deferred.
- Milestone 6 (protection domains, multicore) has started: 6a is built (the MMU and caches on, one EL1 identity map, `kernel/arch/aarch64/mmu.s`, decision 171); 6b-1 (`separate actor`, the front end, decisions 172 and 173) is built, 6b-2a (EL0 machinery: `kernel/arch/aarch64/domain.s`, decision 174; the `domain_*` tests) is built; 6b-2b (a compiled `separate` actor runs in a domain of its own, decision 175; the `separate_*` tests) is built; 6b-2c (a failing domain restarts as a unit, its memory wiped, decision 176; `separate_supervised`) is built; 6c (isolation tests: [isolation.md](isolation.md)) is built; 6d-1 (cores 1-3 come up, each with its own state, decision 178; `cores_online`) is built; 6d-2 (a `separate` actor is placed on a core by Raven, decision 180, and cores talk through rings, decision 181; `separate_cross_core_*`, `separate_placement`) is built; 6e (the two-core demo, `two_core_demo`, and the write-up [milestone6.md](milestone6.md)) is built, which finishes milestone 6.
- There is **no Pi hardware** in the loop: everything is QEMU. Say in every PR what
  is untested on a board.
- The latest decision number is in [decisions-index.md](decisions-index.md).

## Commands

| Command | What it does |
|---|---|
| `make test` | Build and run every test (~45 s). Quiet: failures, `slowest:` and a summary. Rook and error tests run in parallel (`-j N` / `JOBS=N`) |
| `make quick [T="rook/restart"]` | Only the tests named in T (names or prefixes); default every Rook and error test (~15 s). Not a substitute for `make test` before a push |
| `python3 tests/run_tests.py [-v] PREFIX...` | Run tests by name or prefix (`rook/restart`, `error/`); `-v` lists every test. Needs images from a `make test` |
| `make coverage` | Must exit 0 (compiler lines no test reaches, except `tools/coverage-gaps.txt`) |
| `python3 tools/selfhost-stage2.py` | The Rook compiler compiles itself, the result is built into an image that compiles it again; the two outputs must be identical (also a test, `selfhost/stage2`) |
| `make bootstrap` / `make rook-kernel` / `make rook-compilers` | The stage-2 compiler image; Raven's kernel and the compiler programs built by it (`tools/rookc.py`, cache in `build/cache/`) |
| `make selfhost-check` | Each self-hosted compiler stage against Fledgling on every Rook file (slow; not in `make test`) |
| `python3 -m fledgling FILES [--trusted DIR] --dump-tokens\|--dump-tree\|--dump-expanded\|--dump-declarations\|--dump-bodies [--no-bodies] [--all-bodies] [--ownership] [--actors]\|--dump-asm` | The text each self-hosted stage must print for the same input |
| `python3 -m fledgling FILES --report-copies` | Every send to a `separate` actor (it copies its message), with the size (decision 173) |
| `make difftest` | Random arithmetic against a Python model |
| `tools/bringup-stages.py` | First-boot stage images for a board (after `make test`) in `build/sdcard/stages/`, with the serial output to expect (decision 187) |
| `python3 -m fledgling FILES --suggest-imports` | Which `import` lines files lack |
| `tools/new-error-test.py NAME "desc" < prog.rook` | Make an error test: writes a failing placeholder and shows the real error; read it, then `--accept NAME` fills the `// error:` line |
| `tools/bug-check.py FILE OLD NEW` | Break one thing, run `make test`, list failing tests, restore |
| `tools/bug-check-batch.py LIST` | Many bug checks from a file (format in its docstring), in the background; writes `build/bug-check-summary.md` |
| `tools/pr-body.py --summary … --tests OUT --bugs build/bug-check-summary.md [--open --title … --head …]` | Build (and open) the PR body from the template |
| `tools/decisions-index.py` | Regenerate `docs/decisions-index.md` after adding a decision |
| `MACHINES=raspi3b make test` | One QEMU machine only (use for bug checks) |

Long commands go in the background. `make test` once per change, not per edit.

## Tests (`tests/run_tests.py`)

- `rook/NAME` = `tests/rook/NAME.rook`: compiled with Raven, booted in QEMU, prints
  exactly the `// expect:` lines (also `expect-failure:`, `expect-panic:`, `expect-fault:`,
  `expect-qemu-log:`, `expect-line-count:`, `input:`/`expect-echo:`, `machines:`).
  Work out expected values by hand and say how in the header comment.
- `error/NAME` = `tests/rook/errors/NAME.rook`: the compiler must reject it with
  `// error: LINE:COL: message`. Compile the program first, **read** the message and
  caret, then write the line. `// alone` compiles without Raven.
- `tests/rook/trusted/` and `tests/rook/errors/trusted/`: tests that may use `unsafe`.
- `hosted/PROGRAM/CASE` = `tests/hosted/PROGRAM/CASE.in` fed to `compiler/PROGRAM.rook` on the UART
  (ended by byte 4; or a file listed in `PROGRAM/live.txt`, with Fledgling's output as the expectation); its output between `Hosted: begin`/`Hosted: end` must equal `CASE.out`
  ([self-hosting.md](self-hosting.md)).
- `// retries: N` in a header lets a test of a timing race pass if one of up to N runs does (decision 188); a test whose handlers wait on ring room can be held past the 100 ms budget on a loaded machine. Do not use it to hide a failure.
- `boot/` and `harness/` tests are Python in `run_tests.py`.
- A panic expectation `at file.rook:N` is an exact line for the test's own file. For a
  panic inside Raven's files write `at mailbox.rook:*` (any line; the message stays exact).
- Logs: `build/test-logs/`; generated assembly: `build/rook-tests/*.s`.
- A bug-check batch edits source files: commit real work first (it refuses otherwise),
  never commit while it runs, wait for it to end, then `make test` again (images go stale).

## Rules that bite (Fledgling)

- Top-level names are global across a program. A local or parameter can't share a
  function's name (use `rt_` prefixes in tests).
- `actor`, `on`, `self`, `module`, `import`, `consume`, `narrow`, `interface`, `watch`, `stop` are keywords.
- A file uses another file's names only if it imports them
  (`import module: Name, Name`, bare, one module per line, top of file).
- Raven internals (mailbox, assembly) are closed modules. A new `pub` name in a public
  module must be added to `tests/public-surface.txt`.
- `unsafe` only under `raven/trusted/` (and `tests/rook/trusted/`).

## Workflow

- Never push to `main`. Branch `m<milestone>-<topic>`; PR from
  `.github/pull_request_template.md`; the owner squash-merges.
- Before a PR: `make test`, `make coverage` (exit 0), `make difftest`.
- Every behaviour change: a test, and a deliberate-bug check (table in the PR: bug →
  tests that fail). A bug no test catches means add a test and say so.
- Decisions: append to `decisions.md` (never rewrite), name the principle that settled it
  ([principles.md](principles.md)), mark ones you derived "derived; open to review",
  regenerate the index. Update the relevant design doc in the same PR.
- Language design questions: ask the owner one at a time with 2–3 legal Rook samples;
  build nothing until chosen.
- `gh` GraphQL is blocked here; use REST (`gh api repos/JJS-projects/CorvOS/pulls -X POST
  -f title=… -f head=… -f base=main -F body=@FILE`). Write bodies via a quoted heredoc.

## Rook cheat sheet

Full syntax table: [rook.md](rook.md) §2 (one screen). Beyond it:

```rook
// tests/rook/quickstart_sample.rook (compiled by the test suite, so it stays legal)
import actors: Sent, run_until_idle
import print: print_u64

interface Add {
    reliable on add(n: u64)
}

enum Reply {
    Go(value: u64)
    Failed
}

actor Counter: Add {
    mut count: u64

    new(start: u64) {
        .count = start
    }

    reliable on add(n: u64) {
        .count += n
    }

    pub lossy on show() {
        print_u64(.count)
    }
}

fn report(result: Sent) {
    match result {
        Delivered => print_u64(1)
        MailboxFull => panic("full")
        Gone => panic("full")
    }
}

fn unwrap(r: Reply) -> u64 {
    match r {
        Go(value: v) => return v
        Failed => return 0
    }
}

fn test_main() {
    c: Counter = spawn Counter(10)
    adder: Add = narrow(c, Add)
    report(adder.add(1))
    discard adder.add(2)
    c.show()
    print_u64(unwrap(Go(value: 7)))
    run_until_idle()
}
```

- Sends only queue a message; handlers run in `run_until_idle()`. The test entry is `fn test_main()`.
- Handlers: `reliable on` / `lossy on`; `new(...)` initialises; `.field` is the actor's
  own; `self` is its reference; `narrow(x, Interface)` hands out fewer handlers.
- Every loop is named: `name: loop cond { }`, `break name`. `} name` may close a block.
- `a as u64` only if lossless; `x wrap u8` keeps low bits. Unused results need `discard`.
- Text is `"..."` (`Bytes`). `panic("msg")` ends the handler (the actor restarts, 148).
- Messages carry `val`, `iso` and small structs/enums (up to 8 words copied).
- Failure and restarts: rook.md §4.3 and §11.16.
- `Option<T>`/`Result<T, E>` (`import option: Option, Result`; variants `Some`, `None`, `Ok`, `Err` are taken) and `Text` (`import text: Text`; `t: Text = Text(region: region)`, `push`, `append("..")`, `append_dec`, `equals`, `hash`).
- `TextMap<V>` (`import textmap: TextMap`): `put(text, v)`, `find(text) -> Option<V>`, `contains`, `len`, `key_at(i)`, `value_at(i)`.
- `List<T>` (`import region: List, Region, make_region`): `l: List<u64> = List(region: region)`, `push`, `get`, `set`, `pop`, `len`, `clear` (§11.21).
- A struct can declare `new(...)` like an actor and is then built only as `Name(arg: value, ...)` (§11.20).
- Generics (structs and enums only): `enum Option<T> { Some(value: T)  None }`, used as `x: Option<u64> = Some(value: 3)`; the type comes from the place the value goes to, never from the arguments (§11.19).

## Where to read more (only what the task touches)

| Task touches | Read |
|---|---|
| Any decision's reasons | the entry in [decisions.md](decisions.md) (find it in [decisions-index.md](decisions-index.md)) |
| Language rules, a new feature | [rook.md](rook.md): §4 actors, §7 capabilities, §11 Fledgling's subset (by step) |
| Kernel/Raven structure, memory, scheduling | [design.md](design.md) |
| Security argument, what an untrusted actor can reach | [capabilities.md](capabilities.md) |
| Why a choice was made | [principles.md](principles.md) |
| Board assumptions, first boot | [hardware.md](hardware.md) |
| The self-hosted compiler (milestone 5) | [self-hosting.md](self-hosting.md) |
| What a protection domain guarantees, and the test behind each claim (milestone 6) | [isolation.md](isolation.md); what milestone 6 shows: [milestone6.md](milestone6.md) |
| Failure, restarts, supervisors | [fault-tolerance.md](fault-tolerance.md) |
| Next steps | [roadmap.md](roadmap.md) |
| Full workflow and test details | [workflow.md](workflow.md) |
