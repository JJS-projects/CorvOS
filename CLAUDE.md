# Instructions for Claude

CorvOS is a research OS (Raven kernel) and language (Rook) for the Raspberry Pi
Zero 2 W. Claude writes the code; the project owner reviews and merges.

Read first: `docs/principles.md`, `docs/workflow.md`, `docs/design.md`, `docs/rook.md`,
`docs/roadmap.md`.

## Rules

- **Never push to `main`.** Work on a branch named `m<milestone>-<topic>` and open
  a pull request using `.github/pull_request_template.md`. The owner merges.
- Keep PRs reviewable in one sitting; split large milestones.
- Run `make test` before every push and paste the output into the PR.
- Any behaviour change adds or updates a test in `tests/run_tests.py`. Check that
  the test fails when the behaviour is broken.
- Decisions go in `docs/decisions.md` (append; never rewrite old entries) and the
  relevant design doc, in the same PR as the code. Check the options against
  `docs/principles.md` first and say which principle settled it.
- Language design questions go to the owner with code samples of each option
  before anything is built.
- Say plainly in the PR what is untested, especially on real hardware.
- Don't add dependencies beyond `tools/install-deps.sh` without recording why.

## Conventions

- Assembly: GNU `as` syntax, `.s` files, AAPCS64 calling convention (args in
  x0-x7, x19-x28 preserved). Comment every non-obvious register or magic number.
- Kernel console output is prefixed `Raven: `; tests match on these lines.
- Commit messages: short imperative subject, body explaining why.
