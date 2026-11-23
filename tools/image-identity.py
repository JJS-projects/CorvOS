#!/usr/bin/env python3
"""Check that a refactor changed no built image: every image byte for byte the same as a base's.

    tools/image-identity.py --worktree DIR [--base REF] [-j N]   build REF (default origin/main) in a git
                                                                 worktree at DIR, build this tree, compare
    tools/image-identity.py --save DIR [-j N]                    build this tree, keep a copy of its images in DIR
    tools/image-identity.py --against DIR [-j N]                 build this tree, compare with images kept by --save
    add --no-build to compare what is already built

The images are the ones `make test` builds, plus the stage-2 compiler: build/kernel8.img, build/fault/kernel8.img,
every test image under build/rook-tests/, the compiler programs under build/compiler/, Raven built by the Rook
compiler (build/rook/kernel8.img, build/rook/compiler/numberer.img) and build/stage2/asmdump.img. The list comes
from the Makefile of each tree (`make --eval`), so a later Makefile names its own images.

Prints one line per image that differs, is missing or is new, then a summary; exits 1 if any image of the base
differs or is missing from this tree. A new image (a test this tree adds) is listed but is not a failure. Where an
image differs and the assembly it was built from is beside it (NAME.s), the first differing line is shown.

The worktree is made with `git worktree add --detach` (or moved to REF if it is already there), so the working
tree is never touched. Use a directory outside the repository, such as a scratch directory. A fresh worktree has
no build/cache/, so its first build also compiles Raven and the numberer with the Rook compiler in QEMU (about two
minutes for the whole build on 4 CPUs); later builds reuse it. Decision 220, docs/porting-plan.md (phase 0).
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The images, by the Makefile's own names; a variable a tree lacks expands to nothing.
IMAGE_LIST = ("$(KERNEL) $(FAULT_KERNEL) $(ROOK_KERNEL) $(STAGE2_IMG) $(BUILD)/rook/compiler/numberer.img "
              "$(COMPILER_IMGS) $(ROOK_TEST_IMGS)")
MANIFEST = "MANIFEST"


def image_paths(tree):
    """The images `tree`'s Makefile builds, as paths relative to `tree`."""
    out = subprocess.run(["make", "--no-print-directory", "-C", tree,
                          "--eval", f"image-identity-list: ; @echo {IMAGE_LIST}", "image-identity-list"],
                         check=True, capture_output=True, text=True).stdout.split()
    if not out:
        sys.exit(f"image-identity: {tree}'s Makefile names no images")
    return sorted(set(out))


def build(tree, jobs):
    paths = image_paths(tree)
    print(f"image-identity: building {len(paths)} images in {tree}", flush=True)
    result = subprocess.run(["make", "--no-print-directory", "-C", tree, f"-j{jobs}"] + paths,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr[-4000:])
        sys.exit(f"image-identity: the build in {tree} failed")
    return paths


def worktree(path, ref):
    """A detached worktree of `ref` at `path`, made or moved there."""
    subprocess.run(["git", "-C", ROOT, "fetch", "--quiet", "origin"], check=False)
    commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", ref + "^{commit}"],
                            check=True, capture_output=True, text=True).stdout.strip()
    if os.path.exists(os.path.join(path, ".git")):
        subprocess.run(["git", "-C", path, "checkout", "--quiet", "--detach", commit], check=True)
    else:
        if os.path.realpath(path).startswith(os.path.realpath(ROOT) + os.sep):
            sys.exit("image-identity: put the worktree outside the repository")
        subprocess.run(["git", "-C", ROOT, "worktree", "add", "--quiet", "--detach", path, commit], check=True)
    print(f"image-identity: base {ref} = {commit[:12]} in {path}", flush=True)
    return path


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def first_difference(a, b):
    """The first differing line of two assembly files, or None."""
    if not (os.path.exists(a) and os.path.exists(b)):
        return None
    la, lb = open(a).read().splitlines(), open(b).read().splitlines()
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return f"line {i + 1}: base `{x.strip()}`, this tree `{y.strip()}`"
    if len(la) != len(lb):
        return f"{len(la)} lines in the base, {len(lb)} in this tree"
    return "the assembly is the same; the difference is after it (kernel objects, linking)"


def compare(base, base_paths, here, here_paths, say=print):
    """Compare two sets of images; `base` is a directory, `base_paths` relative paths in it."""
    differ, missing, new, same = [], [], [], 0
    for rel in base_paths:
        b, h = os.path.join(base, rel), os.path.join(here, rel)
        if not os.path.exists(b):
            sys.exit(f"image-identity: the base has no {rel}")
        if rel not in here_paths or not os.path.exists(h):
            missing.append(rel)
        elif digest(b) != digest(h):
            differ.append(rel)
            say(f"DIFFERS  {rel}")
            why = first_difference(os.path.splitext(b)[0] + ".s", os.path.splitext(h)[0] + ".s")
            if why:
                say(f"         {why}")
        else:
            same += 1
    for rel in missing:
        say(f"MISSING  {rel}")
    for rel in sorted(set(here_paths) - set(base_paths)):
        new.append(rel)
        say(f"NEW      {rel}")
    say(f"image-identity: {same} identical, {len(differ)} differ, {len(missing)} missing, {len(new)} new")
    return not differ and not missing


def save(paths, into):
    os.makedirs(into, exist_ok=True)
    with open(os.path.join(into, MANIFEST), "w") as manifest:
        for rel in paths:
            dest = os.path.join(into, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, rel), dest)
            asm = os.path.splitext(rel)[0] + ".s"           # kept so that a later difference can be shown
            if os.path.exists(os.path.join(ROOT, asm)):
                shutil.copyfile(os.path.join(ROOT, asm), os.path.join(into, asm))
            manifest.write(f"{digest(dest)}  {rel}\n")
    print(f"image-identity: kept {len(paths)} images in {into}")


def main(argv):
    ap = argparse.ArgumentParser(prog="image-identity", description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--worktree", metavar="DIR", help="build --base in a git worktree at DIR and compare")
    mode.add_argument("--save", metavar="DIR", help="keep a copy of this tree's images in DIR")
    mode.add_argument("--against", metavar="DIR", help="compare with images kept by --save")
    ap.add_argument("--base", default="origin/main", help="the commit to compare with (default origin/main)")
    ap.add_argument("--no-build", action="store_true", help="compare the images already built")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    args = ap.parse_args(argv)

    if args.worktree:
        base = worktree(os.path.abspath(args.worktree), args.base)
        base_paths = image_paths(base) if args.no_build else build(base, args.jobs)
    elif args.against:
        base = os.path.abspath(args.against)
        base_paths = [line.split(None, 1)[1] for line in open(os.path.join(base, MANIFEST)).read().splitlines()]
    here_paths = image_paths(ROOT) if args.no_build else build(ROOT, args.jobs)
    if args.save:
        save(here_paths, os.path.abspath(args.save))
        return 0
    return 0 if compare(base, base_paths, ROOT, here_paths) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
