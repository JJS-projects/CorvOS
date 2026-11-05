#!/usr/bin/env python3
"""Build a pull request body from .github/pull_request_template.md, and optionally open the PR.

    tools/pr-body.py --summary TEXT --roadmap TEXT --review TEXT [--design TEXT] [--limits TEXT]
                     [--tests FILE] [--checks TEXT] [--bugs FILE] [--session URL] [-o OUT]
                     [--open --title TITLE --head BRANCH [--base BRANCH]]

Each text argument fills the template section of that name (use @FILE to read it from a file,
or a quoted heredoc: `--limits "$(cat <<'EOF' ... EOF)"`). Test evidence is the end of the
test output in --tests (the `slowest:` line and the `N/N passed` summary, and any FAIL block),
then --checks (extra lines such as the coverage and difftest results), then the bug-check
table from --bugs (written by tools/bug-check-batch.py). The template's own checklist is kept.
--open creates the PR with the GitHub REST API through `gh api` (GraphQL is blocked here) against main.
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, ".github", "pull_request_template.md")


def text_arg(value):
    return open(value[1:]).read().strip() if value and value.startswith("@") else (value or "")


def test_evidence(path):
    """The part of a test log worth pasting: failures, the slowest line, the summary."""
    lines = open(path).read().splitlines()
    keep = [l for l in lines if l.startswith(("FAIL", "      reason", "        |", "slowest:"))
            or re.match(r"\d+/\d+ passed", l)]
    return "\n".join(keep)


def fill(template, args):
    out = template
    out = re.sub(r"<!-- One or two sentences.*?-->", lambda m: args.summary, out, flags=re.S)
    out = re.sub(r"(\*\*Roadmap:\*\*) milestone N, <!-- which part -->", lambda m: f"{m.group(1)} {args.roadmap}", out)
    out = re.sub(r"<!-- Where to look first.*?-->", lambda m: args.review, out, flags=re.S)
    evidence = [test_evidence(args.tests)] if args.tests else []
    if args.checks:
        evidence.append(args.checks)
    out = re.sub(r"```\n<!-- paste `make test` output -->\n```",
                 lambda m: "```\n" + "\n".join(evidence) + "\n```" + (
                     "\n\nDeliberate-bug checks:\n\n" + open(args.bugs).read().strip() if args.bugs else ""),
                 out)
    out = re.sub(r"<!-- Decisions made or changed.*?-->", lambda m: args.design or "No decision in this PR.", out, flags=re.S)
    out = re.sub(r"<!-- What is deliberately not done.*?-->", lambda m: args.limits, out, flags=re.S)
    if "<!--" in out:
        sys.exit("a template section was left unfilled; the template may have changed")
    footer = "\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    if args.session:
        footer += "\n\n" + args.session
    return out.rstrip() + footer + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    for name in ("summary", "roadmap", "review", "design", "limits", "checks"):
        ap.add_argument("--" + name, type=text_arg, default="")
    ap.add_argument("--tests")
    ap.add_argument("--bugs")
    ap.add_argument("--session")
    ap.add_argument("-o", "--out", default="build/pr-body.md")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--title")
    ap.add_argument("--head")
    ap.add_argument("--base", default="main", help="the branch to merge into (a stacked PR names the one below)")
    args = ap.parse_args()
    for needed in ("summary", "roadmap", "review", "limits"):
        if not getattr(args, needed):
            sys.exit(f"--{needed} is required")
    body = fill(open(TEMPLATE).read(), args)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "w").write(body)
    print(f"wrote {args.out} ({len(body)} bytes)")
    if args.open:
        if not (args.title and args.head):
            sys.exit("--open needs --title and --head")
        run = subprocess.run(["gh", "api", "repos/JJS-projects/CorvOS/pulls", "-X", "POST",
                              "-f", f"title={args.title}", "-f", f"head={args.head}", "-f", f"base={args.base}",
                              "-F", f"body=@{args.out}", "--jq", ".html_url"],
                             capture_output=True, text=True)
        print(run.stdout.strip() or run.stderr.strip())
        sys.exit(run.returncode)


if __name__ == "__main__":
    main()
