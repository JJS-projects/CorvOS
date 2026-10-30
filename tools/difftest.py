#!/usr/bin/env python3
"""Differential testing of Rook's integer arithmetic.

    python3 tools/difftest.py [--seed N] [--programs N] [--cases N]

Generates random Rook programs over all eight integer types, works out what
each must print (or which panic ends it) with a model written here in Python
from the rules in docs/rook.md section 11.1, compiles the programs with
Fledgling, boots them in QEMU like the other Rook tests, and compares.

The model shares no code with the compiler. A disagreement is either a
compiler bug or a mistake in this model, and the failing program is left in
build/difftest/ with its expected output in the header, so it can be copied
to tests/rook/ as a regression test once it is understood.

The same seed gives the same programs (on the same Python version).
"""
import argparse
import glob
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.dont_write_bytecode = True
import run_tests  # noqa: E402

OUT = os.path.join(ROOT, "build", "difftest")


# ---------------------------------------------------------------------------
# The model: Rook's integer rules

class Type:
    def __init__(self, bits, signed):
        self.bits, self.signed = bits, signed
        self.name = f"{'i' if signed else 'u'}{bits}"
        self.min = -(1 << (bits - 1)) if signed else 0
        self.max = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1

    def holds(self, v):
        return self.min <= v <= self.max

    def wrap(self, v):
        """Keep the low bits and reinterpret the sign (what `wrap` does)."""
        v &= (1 << self.bits) - 1
        return v - (1 << self.bits) if self.signed and v > self.max else v

    def __repr__(self):
        return self.name


TYPES = [Type(b, s) for s in (False, True) for b in (8, 16, 32, 64)]
BOOL = "bool"


class Panic(Exception):
    pass


OVERFLOW = "integer overflow"
DIV0 = "division by zero"
SHIFT = "shift amount too large"
SHL_LOST = "shl lost set bits"


def trunc_div(a, b):
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def arith(op, ty, a, b):
    """`a op b` for two values of type `ty`; raises Panic."""
    if op in ("+", "-", "*"):
        r = {"+": a + b, "-": a - b, "*": a * b}[op]
        if not ty.holds(r):
            raise Panic(OVERFLOW)
        return r
    if op in ("/", "%"):
        if b == 0:
            raise Panic(DIV0)
        q = trunc_div(a, b)
        if op == "/":
            if not ty.holds(q):
                raise Panic(OVERFLOW)       # MIN / -1
            return q
        return a - b * q                    # MIN % -1 is 0
    if op == "and":
        return a & b
    if op == "or":
        return a | b
    if op == "xor":
        return a ^ b
    if op in ("shl", "shr"):
        if not 0 <= b < ty.bits:
            raise Panic(SHIFT)
        if op == "shr":
            return a >> b                   # arithmetic on signed values
        shifted = ty.wrap(a << b)
        if shifted >> b != a:               # shifting back must give `a`
            raise Panic(SHL_LOST)
        return shifted
    raise AssertionError(op)


# Expressions are tuples:
#   ("var", name, ty, value)   ("lit", value, ty)
#   ("bin", op, left, right, ty)   ("neg", x, ty)   ("inv", x, ty)
#   ("conv", "as"|"wrap", x, ty)
#   ("cmp", op, left, right)   ("logic", "and"|"or", left, right)   ("lnot", x)

def evaluate(e, right_first=False):
    """The value of `e`, or raise Panic. `right_first` evaluates the operands of
    arithmetic right to left, to find programs whose panic depends on the order."""
    kind = e[0]
    if kind in ("var", "lit"):
        return e[3] if kind == "var" else e[1]
    if kind == "bin":
        _, op, l, r, ty = e
        if right_first:
            b = evaluate(r, right_first)
            a = evaluate(l, right_first)
        else:
            a = evaluate(l, right_first)
            b = evaluate(r, right_first)
        return arith(op, ty, a, b)
    if kind == "neg":
        v = -evaluate(e[1], right_first)
        if not e[2].holds(v):
            raise Panic(OVERFLOW)
        return v
    if kind == "inv":
        return e[2].wrap(~evaluate(e[1], right_first))
    if kind == "conv":
        v = evaluate(e[2], right_first)
        return v if e[1] == "as" else e[3].wrap(v)
    if kind == "cmp":
        if right_first:
            b = evaluate(e[3], right_first)
            a = evaluate(e[2], right_first)
        else:
            a = evaluate(e[2], right_first)
            b = evaluate(e[3], right_first)
        return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b,
                ">": a > b, ">=": a >= b}[e[1]]
    if kind == "logic":
        left = evaluate(e[2], right_first)         # short-circuit, left first
        if (e[1] == "and") != left:
            return left
        return evaluate(e[3], right_first)
    if kind == "lnot":
        return not evaluate(e[1], right_first)
    raise AssertionError(kind)


def outcome(e, right_first=False):
    try:
        return ("value", evaluate(e, right_first))
    except Panic as p:
        return ("panic", str(p))


# ---------------------------------------------------------------------------
# Generating expressions

def edge_values(ty, rng):
    edges = [0, 1, ty.max, ty.max - 1, ty.max // 2, ty.min, ty.min + 1,
             rng.randint(ty.min, ty.max), rng.randint(ty.min, ty.max)]
    if ty.signed:
        edges += [-1, -2]
    return [v for v in edges if ty.holds(v)]


class Generator:
    def __init__(self, rng):
        self.rng = rng
        self.vars = {}          # type -> list of ("var", name, ty, value)
        self.decls = []
        for ty in TYPES:
            self.vars[ty] = []
            values = edge_values(ty, rng)
            for i, v in enumerate(rng.sample(values, 4)):
                name = f"v_{ty.name}_{i}"
                self.vars[ty].append(("var", name, ty, v))
                self.decls.append(f"    {name}: {ty.name} = {literal(v, ty)}")

    def leaf(self, ty):
        return self.rng.choice(self.vars[ty])

    def literal(self, ty, small=False):
        hi = min(ty.max, ty.bits - 1 if small else 100)
        lo = 0 if small else max(ty.min, -100)
        return ("lit", self.rng.randint(lo, hi), ty)

    def operand(self, ty, depth):
        return self.int_expr(ty, depth)

    def int_expr(self, ty, depth):
        rng = self.rng
        if depth == 0 or rng.random() < 0.25:
            return self.leaf(ty)
        roll = rng.random()
        if roll < 0.55:
            op = rng.choice(["+", "-", "*", "/", "%", "and", "or", "xor", "shl", "shr"])
            left = self.int_expr(ty, depth - 1)
            if op in ("shl", "shr"):
                # A literal amount is a u64, so it is never negative.
                right = self.literal(ty, small=True) if rng.random() < 0.6 else self.int_expr(ty, depth - 1)
            elif rng.random() < 0.3:
                right = self.literal(ty)
            else:
                right = self.int_expr(ty, depth - 1)
            if rng.random() < 0.15:          # a literal on the left, typed by the right
                left = self.literal(ty) if op not in ("shl", "shr") and right[0] != "lit" else left
            return ("bin", op, left, right, ty)
        if roll < 0.65 and ty.signed:
            return ("neg", self.int_expr(ty, depth - 1), ty)
        if roll < 0.75:
            return ("inv", self.int_expr(ty, depth - 1), ty)
        # A conversion from another type.
        source = rng.choice([t for t in TYPES if t != ty])
        inner = self.int_expr(source, depth - 1)
        kind = "as" if ty.min <= source.min and source.max <= ty.max else "wrap"
        return ("conv", kind, inner, ty)

    def bool_expr(self, depth):
        rng = self.rng
        if depth == 0 or rng.random() < 0.5:
            ty = rng.choice(TYPES)
            if rng.random() < 0.5:          # straight on variables: edge values meet
                left, right = self.leaf(ty), self.leaf(ty)
            else:
                left = self.int_expr(ty, 2)
                right = self.literal(ty) if rng.random() < 0.3 else self.int_expr(ty, 1)
            return ("cmp", rng.choice(["==", "!=", "<", "<=", ">", ">="]), left, right)
        if rng.random() < 0.2:
            return ("lnot", self.bool_expr(depth - 1))
        return ("logic", rng.choice(["and", "or"]), self.bool_expr(depth - 1),
                self.bool_expr(depth - 1))


def literal(v, ty):
    """Source text of the value `v` of type `ty` (the minimum needs two steps:
    its magnitude doesn't fit)."""
    if v == ty.min and ty.signed:
        return f"-{ty.max} - 1"
    return str(v)


def render(e):
    kind = e[0]
    if kind == "var":
        return e[1]
    if kind == "lit":
        return f"({e[1]})" if e[1] < 0 else str(e[1])
    if kind == "bin":
        return f"({render(e[2])} {e[1]} {render(e[3])})"
    if kind == "neg":
        return f"(-{render(e[1])})"
    if kind == "inv":
        return f"(not {render(e[1])})"
    if kind == "conv":
        return f"({render(e[2])} {e[1]} {e[3].name})"
    if kind == "cmp":
        return f"({render(e[2])} {e[1]} {render(e[3])})"
    if kind == "logic":
        return f"({render(e[2])} {e[1]} {render(e[3])})"
    if kind == "lnot":
        return f"(not {render(e[1])})"
    raise AssertionError(kind)


def static_type(e):
    kind = e[0]
    if kind in ("cmp", "logic", "lnot"):
        return BOOL
    return {"var": lambda: e[2], "lit": lambda: e[2], "bin": lambda: e[4],
            "neg": lambda: e[2], "inv": lambda: e[2], "conv": lambda: e[3]}[kind]()


def print_statement(e):
    ty = static_type(e)
    if ty == BOOL:
        return f"print_bool({render(e)})"
    if ty.signed:
        return f"print_i64({render(e)})" if ty.bits == 64 else f"print_i64({render(e)} as i64)"
    return f"print_u64({render(e)})" if ty.bits == 64 else f"print_u64({render(e)} as u64)"


def shown(value):
    return str(value).lower() if isinstance(value, bool) else str(value)


# ---------------------------------------------------------------------------
# Cases: a few lines of Rook, what they print, and the panic they end in

class Case:
    def __init__(self, lines, prints, panic=None, panic_line=0):
        self.lines, self.prints, self.panic, self.panic_line = lines, prints, panic, panic_line


def expression_case(g):
    e = g.bool_expr(2) if g.rng.random() < 0.35 else g.int_expr(g.rng.choice(TYPES), 3)
    return finish(e, [print_statement(e)])


def compound_case(g, index):
    """`mut c: T = leaf`, then `c op= expr`, then print c."""
    rng = g.rng
    ty = rng.choice(TYPES)
    start = g.leaf(ty)
    op = rng.choice(["+", "-", "*", "/", "%"])
    operand = g.int_expr(ty, 2)
    name = f"c_{index}"
    # `c op= operand` evaluates the operand, then does `c op operand`.
    model = ("bin", op, start, operand, ty)
    lines = [f"mut {name}: {ty.name} = {render(start)}", f"{name} {op}= {render(operand)}"]
    prints = print_statement(("var", name, ty, None))
    first = outcome(model)
    if first[0] == "panic":
        if outcome(model, right_first=True) != first:
            return None
        return Case(lines, [], first[1], panic_line=1)
    return Case(lines + [prints], [shown(first[1])])


def finish(e, statements):
    first = outcome(e)
    if outcome(e, right_first=True) != first:
        return None             # which panic comes first isn't specified
    if first[0] == "panic":
        return Case(statements, [], first[1], panic_line=0)
    return Case(statements, [shown(first[1])])


# ---------------------------------------------------------------------------
# The panic sweep: one small program for every way an operation can panic, on
# every type it applies to. A bug that fails to panic only shows up in a program
# that ends in that very panic, so random programs would find it by luck.

# (name, operator, the panic it must end in, only for signed types)
PANIC_KINDS = [
    ("add", "+", OVERFLOW, False), ("sub", "-", OVERFLOW, False),
    ("mul", "*", OVERFLOW, False), ("div0", "/", DIV0, False),
    ("divmin", "/", OVERFLOW, True), ("rem0", "%", DIV0, False),
    ("shlbig", "shl", SHIFT, False), ("shllost", "shl", SHL_LOST, False),
    ("shrbig", "shr", SHIFT, False), ("neg", "neg", OVERFLOW, True),
]


def panic_sweep():
    return [(f"{name}_{ty.name}", op, why, ty) for (name, op, why, signed) in PANIC_KINDS
            for ty in TYPES if ty.signed or not signed]


def forced_panic(rng, op, why, ty):
    """An expression that panics with `why`, on one or two new variables of type
    `ty`, with operands found by trying edge values against the model.
    Returns (expression, declarations of its variables)."""
    values = edge_values(ty, rng) + [ty.bits, ty.bits + 1, ty.bits - 1, 2, 3]
    values = [v for v in dict.fromkeys(values) if ty.holds(v)]
    pairs = [(a, b) for a in values for b in values]
    rng.shuffle(pairs)
    for a, b in pairs:
        left = ("var", "p_a", ty, a)
        right = ("var", "p_b", ty, b)
        e = ("neg", left, ty) if op == "neg" else ("bin", op, left, right, ty)
        if outcome(e) == ("panic", why):
            decls = [f"    p_a: {ty.name} = {literal(a, ty)}"]
            if op != "neg":
                decls.append(f"    p_b: {ty.name} = {literal(b, ty)}")
            return e, decls
    raise AssertionError(f"no operands make {op} on {ty} panic with {why}")


# ---------------------------------------------------------------------------
# Programs

def make_program(seed, name, cases_wanted, out_dir, forced=None):
    """One program, `name`.rook. `forced` is an entry of panic_sweep(): the program
    ends in that panic. Otherwise it ends in a random one, if any came up."""
    rng = random.Random(f"{seed}/{name}")
    g = Generator(rng)
    plain, panics = [], []
    index = 0
    while len(plain) < cases_wanted or not panics:
        index += 1
        if index > cases_wanted * 40:
            break
        case = compound_case(g, index) if rng.random() < 0.2 else expression_case(g)
        if case is None:
            continue
        (panics if case.panic else plain).append(case)
    last = rng.choice(panics) if panics else None
    if forced:
        _, op, why, ty = forced
        e, decls = forced_panic(rng, op, why, ty)
        g.decls += decls
        last = Case([print_statement(e)], [], why)

    body = list(g.decls)
    expected = []
    for case in plain[:cases_wanted]:
        body += ["    " + line for line in case.lines]
        expected += case.prints
    panic_at = None
    if last:
        base = len(body)
        body += ["    " + line for line in last.lines]
        panic_at = base + last.panic_line

    header = [f"// Generated by tools/difftest.py (seed {seed}, {name}); do not edit."]
    header += [f"// expect: {line}" for line in expected]
    if last:
        header.append(f"// expect-panic: {last.panic} at {name}.rook:LINE")
    header.append("")
    fn = ["fn test_main() {"] + body + ["}"]
    if last:
        # +1 for the `fn` line, +1 because lines count from 1.
        line_no = len(header) + 1 + panic_at + 1
        header = [h.replace("LINE", str(line_no)) for h in header]
    path = os.path.join(out_dir, name + ".rook")
    with open(path, "w") as f:
        f.write("\n".join(header + fn) + "\n")
    return path, len(plain[:cases_wanted]) + (1 if last else 0), last.panic if last else None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--programs", type=int, default=12, help="random programs")
    ap.add_argument("--cases", type=int, default=30, help="cases per random program")
    ap.add_argument("--no-sweep", action="store_true", help="skip the panic sweep")
    args = ap.parse_args(argv)

    os.makedirs(OUT, exist_ok=True)
    for old in glob.glob(os.path.join(OUT, "*")):
        os.remove(old)
    paths, cases, panics, sweep = [], 0, {}, set()
    for number in range(args.programs):
        path, n, panic = make_program(args.seed, f"r{number}", args.cases, OUT)
        paths.append(path)
        cases += n
        if panic:
            panics[panic] = panics.get(panic, 0) + 1
    if not args.no_sweep:
        for entry in panic_sweep():
            path, n, _ = make_program(args.seed, "p_" + entry[0], 4, OUT, forced=entry)
            paths.append(path)
            sweep.add(path)
            cases += n
    print(f"seed {args.seed}: {args.programs} random programs, {len(sweep)} panic-sweep programs, "
          f"{cases} cases; random programs end in: "
          + (", ".join(f"{k} x{v}" for k, v in sorted(panics.items())) or "no panic"))

    images = [os.path.relpath(p, ROOT)[:-len(".rook")] + ".img" for p in paths]
    build = subprocess.run(["make", "-s", "-k", f"-j{os.cpu_count() or 1}", *images], cwd=ROOT,
                           capture_output=True, text=True)
    tests = []
    for path, image in zip(paths, images):
        test = run_tests.RookTest.from_file(path)
        test.name = "difftest/" + os.path.basename(path)[:-len(".rook")]
        test.image = image
        if path in sweep:
            test.machines = ["raspi3b"]         # the sweep is wide; one machine is enough
        test.description = f"{len(test.expect)} results" + (f", then: {test.expect_panic.split(' at ')[0]}"
                                                           if test.expect_panic else "")
        tests.append(test)
    if build.returncode != 0:
        print(build.stderr[-3000:])
        print("Some programs did not compile; their tests below fail with a missing image.")
    status = run_tests.run_all(tests)
    if status:
        print("\nFailing programs are in build/difftest/ (expected output in each header).")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
