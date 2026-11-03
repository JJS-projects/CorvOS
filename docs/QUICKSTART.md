# Quickstart for agents

Read this page first. It is enough to start; open the full docs only for what the
task touches (map at the bottom). `CLAUDE.md` has the rules that never change.

## State

- Milestones 1–4 are merged: boot, Fledgling (the Python bootstrap compiler in
  `bootstrap/fledgling/`), actors and Raven (`raven/`, trusted parts in `raven/trusted/`),
  ownership, imports, the capability demo.
- Milestone 7 (supervision) is in progress: step 7a (a failed handler restarts its
  actor, decision 148) is merged. Next: 7b `watch`/`restarted`, 7c `stop`/`Gone`
  (decision 147), 7d a supervisor actor. Ownership 4c (`freeze`/pool) is deferred.
- There is **no Pi hardware** in the loop: everything is QEMU. Say in every PR what
  is untested on a board.
- The latest decision number is in [decisions-index.md](decisions-index.md).

## Commands

| Command | What it does |
|---|---|
| `make test` | Build and run every test. Quiet: failures, `slowest:` and a summary |
| `python3 tests/run_tests.py [-v] PREFIX...` | Run tests by name or prefix (`rook/restart`, `error/`); `-v` lists every test. Needs images from a `make test` |
| `make coverage` | Must exit 0 (compiler lines no test reaches, except `tools/coverage-gaps.txt`) |
| `make difftest` | Random arithmetic against a Python model |
| `python3 -m fledgling FILES --suggest-imports` | Which `import` lines files lack |
| `tools/bug-check.py FILE OLD NEW` | Break one thing, run `make test`, list failing tests, restore |
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
- `boot/` and `harness/` tests are Python in `run_tests.py`.
- A panic expectation `at file.rook:N` is an exact line. Lines in `raven/trusted/*.rook`
  move whenever those files change.
- Logs: `build/test-logs/`; generated assembly: `build/rook-tests/*.s`.
- A bug-check batch edits source files: commit real work first, never commit the edits,
  wait for it to end, then `make test` again (images go stale).

## Rules that bite (Fledgling)

- Top-level names are global across a program. A local or parameter can't share a
  function's name (use `rt_` prefixes in tests).
- `actor`, `on`, `self`, `module`, `import`, `consume`, `narrow`, `interface` are keywords.
- A file uses another file's names only if it imports them
  (`import module: Name, Name`, bare, one module per line, top of file).
- Raven internals (mailbox, assembly) are closed modules. A new `pub` name in a public
  module must be added to `tests/public-surface.txt`.
- Panic-line expectations into `raven/trusted` move when those files change.
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
    Ok(value: u64)
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
    }
}

fn unwrap(r: Reply) -> u64 {
    match r {
        Ok(value: v) => return v
        Failed => return 0
    }
}

fn test_main() {
    c: Counter = spawn Counter(10)
    adder: Add = narrow(c, Add)
    report(adder.add(1))
    discard adder.add(2)
    c.show()
    print_u64(unwrap(Ok(value: 7)))
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

## Where to read more (only what the task touches)

| Task touches | Read |
|---|---|
| Any decision's reasons | the entry in [decisions.md](decisions.md) (find it in [decisions-index.md](decisions-index.md)) |
| Language rules, a new feature | [rook.md](rook.md): §4 actors, §7 capabilities, §11 Fledgling's subset (by step) |
| Kernel/Raven structure, memory, scheduling | [design.md](design.md) |
| Security argument, what an untrusted actor can reach | [capabilities.md](capabilities.md) |
| Why a choice was made | [principles.md](principles.md) |
| Board assumptions, first boot | [hardware.md](hardware.md) |
| Next steps | [roadmap.md](roadmap.md) |
| Full workflow and test details | [workflow.md](workflow.md) |
