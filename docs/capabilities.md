# Why an untrusted actor cannot reach the UART

This is the written argument for milestone 4, the first test of the project's hypothesis:
**code can do only what it was handed a reference to do.** It is meant to be read with the
tests open. Every route that could let an untrusted actor write to the UART is listed with
what closes it and the test that fails if it opens. At the end, plainly, is what the argument
does not cover.

## The demo

`tests/rook/capability_demo.rook`. The owner side (here the test; in the kernel, `kernel_main`)
holds the console (`Pl011`, built from the peripherals `Mmio` that only Raven hands out) and a
`UartWriter` actor that writes to it. An
**untrusted** `Shell` (`tests/rook/support/shell.rook`, an ordinary application module that
imports only `ByteSink`) is handed one thing: `narrow(writer, ByteSink)`, a reference that can
only send the handler `byte`. It says hello through it and the line appears on the UART.

`tests/rook/capability_revoke.rook` puts a `Valve` (`raven/valve.rook`, an ordinary actor, not
a language feature) between them. The shell holds only a `ByteSink` narrowed from the valve; the
owner also holds the `Revoke` side. After the owner revokes, the shell speaks again and
nothing more reaches the UART.

## What "untrusted" means here

A file outside a trusted directory (`raven/trusted/`, `tests/rook/support/trusted/`,
`tests/rook/trusted/`). Such a file cannot use `unsafe` or declare `extern fn`, and it can
import only the modules that say `module public` (`tests/public-surface.txt` lists every name
they offer). Raven's own untrusted files (`pl011.rook`, `valve.rook`) are held to the same rules.

## The routes, and what closes each

| # | Route an untrusted actor might try | What closes it | Test |
|---|---|---|---|
| 1 | Send through the reference it holds, but to more than `byte` | A reference narrowed to an interface can send only that interface's handlers | `error/cap_other_handler` |
| 2 | Turn its `ByteSink` back into the writer | No downcast: a `ByteSink` is not a `UartWriter` | `error/cap_downcast` |
| 3 | Narrow it again to a wider interface | A narrowed reference cannot be narrowed again | `error/cap_renarrow` |
| 4 | Make a reference out of a number (an address) | A reference is not a number, in either direction; no cast either way | `error/cap_forge_number`, `error/cap_cast_to_reference`, `error/cap_reference_to_number`, `error/actor_convert` |
| 5 | Call Raven to do it: `actor_address`, `handler_number` | Both are allowed only inside `unsafe` in trusted files | `error/actor_address_untrusted`, `error/trusted/actor_address_number` |
| 6 | Send to an actor by address with `raven_send`, or learn the arena's address | `mailbox` and `assembly` are internal modules; application code cannot import them | `error/closed_forge_send`, `closed_mailbox`, `closed_dispatch`, `closed_timer` |
| 7 | Open a module that is meant to be closed | A trusted module is internal unless it says `module public`, and the public names are a reviewed list | `harness/public_surface` |
| 8 | Spawn its own writer | A writer needs a `Pl011`, which needs an `Mmio` for the UART's registers | next three rows |
| 9 | Make that `Mmio` | An `Mmio` has private fields; only `devices.rook` builds one, from nothing, once, for `kernel_main` | `error/cap_make_mmio`, `error/mmio_forge`, `error/mmio_root_private` |
| 10 | Cut it from another `Mmio` it holds | `part` stays inside the range it was cut from and panics outside it | `rook/mmio_part_outside`, `rook/mmio_outside` |
| 11 | Be handed the root `Mmio` | Only `kernel_main` (and the test hook) receives it. The shell is passed one reference. | the demo itself |
| 12 | Reach the UART's interrupt and read what is typed | An `Irq` has a private field and is made only from the whole peripherals | `error/irq_forged`, `rook/irq_route_panics` |
| 13 | Read or write memory directly | `load` and `store` are allowed only inside `unsafe` in trusted files | `error/load_untrusted`, `error/unsafe_untrusted` |
| 14 | Declare or call assembly | `extern fn` is allowed only in trusted files, and the calls that matter live in internal modules | `error/extern_untrusted`, route 6 |
| 15 | Find a shared global to write through | There is no global mutable state; constants are numbers, bools and text | `error/global_mut` |
| 16 | Use a name it never imported | A file uses another file's names only if it imports them | `error/import_missing_*` |

For revocation: every copy of the shell's reference points at the valve, so one `revoke`
closes all of them, whoever the shell passed it to. The revocation message is `reliable`, so
a full mailbox is reported to the owner instead of losing it (`rook/capability_revoke` checks
the line is written once; `harness/line_count` checks that check can fail).

## The one route that needs no reference: failure reports

Any code can write `panic("text")`, and Raven reports a panic on the UART:
`Raven: panic: text at file:line`. A handler that runs past its time budget is reported there
too, naming the handler. This is the single channel that reaches the console without a
reference. The owner decided (decision 145) to accept and document it. It is bounded: one
line per failure, the text is a constant in the actor's own source, and the actor does not
survive the report (its handler is abandoned and Raven restarts it, decision 148; a panic in
code outside any handler still halts the system). "Cannot write to the UART" is therefore true of every
write the language offers, not of every byte that can appear on the wire.

## Watching an actor gives no authority

`watch(ref, narrow(self, Restarts))` needs a reference to the actor, which the caller must
already hold, and lets Raven send the *watcher* a `restarted()` message. It reads nothing from
the watched actor and sends it nothing, so it adds no route to any actor or device. A
watcher can learn that an actor it can reach was restarted, which any holder of the
reference could learn by other means (a send returning `MailboxFull` while it restarts). The
test for this is `rook/watch_basic`; the compile-time limits are the `error/watch_*` tests.

## What this argument does not cover

- **Memory protection.** There is no MMU yet (milestone 6). Untrusted code is kept away from
  the UART by the language and the compiler, not by the hardware. The argument rests on the
  trusted base being right: Fledgling (a bootstrap compiler with a test suite and 99% line
  coverage, not a proof), Raven's `unsafe` Rook code, and the assembly. A compiler bug, or
  untrusted code compiled by something else, would not be covered.
- **The test build's `print` functions.** `print_u64` and friends write to the UART directly
  and exist only in test images (`kernel/test/print.s`). A test program can import them, so in
  the test build they are ambient. They do not exist in the real kernel. The demo's shell does
  not import them.
- **Denial of service.** A runaway or panicking actor is restarted, not halted (decisions 133,
  148), but it can crash again at once, so one that crash-loops keeps Raven busy and fills the
  console with reports until a supervisor stops it (milestone 7, in progress). Flooding a
  mailbox is bounded but not prevented.
- **Covert and timing channels.** Not claimed.
- **Hardware untested.** All of this ran on QEMU. Nothing here was tried on a Pi Zero 2 W.

## If you distrust it

Read `tests/public-surface.txt` (everything application code can name in Raven), then the
table above with the tests open. A reviewer who finds a seventeenth route should add it as a
failing test first.
