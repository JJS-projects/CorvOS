"""Type checker: resolves names and types, and rejects invalid programs.

Besides checking, it annotates the tree for the code generator:
  - every expression gets `.ty`
  - every local variable and parameter gets a stack `.slot`
  - names of constants get `.const_value`
  - `x += e` is rewritten to `x = x + e`

Integer literals take their type from context (the declared type of a
variable, the other operand, a parameter type). With no context they default
to u64; character literals default to u8.

Naming conventions are enforced: functions, parameters and variables are
snake_case, top-level constants are SCREAMING_CASE.
"""

import re


from .errors import CompileError
from . import syntax as S
from .rtypes import BOOL, UNIT, U8, U64, NAMED_TYPES, IntType, is_int, lossless

ARITH = {"+", "-", "*", "/", "%"}
LOGIC = {"and", "or", "xor"}     # logical on bool, bitwise on integers
SHIFTS = {"shl", "shr"}
EQUALITY = {"==", "!="}
ORDERING = {"<", "<=", ">", ">="}

SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
SCREAMING = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


def require_snake(name, what, pos):
    if not SNAKE.match(name):
        raise CompileError(f"{what} names are snake_case, like `{to_snake(name)}`: `{name}`", pos)


def to_snake(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return re.sub(r"_+", "_", name).strip("_") or "name"


def check(program):
    Checker().program(program)


class Local:
    def __init__(self, ty, mutable, slot):
        self.ty, self.mutable, self.slot = ty, mutable, slot


class FnSig:
    def __init__(self, decl, params, ret):
        self.decl, self.params, self.ret = decl, params, ret


class Checker:
    def __init__(self):
        self.fns = {}
        self.consts = {}         # name -> (type, value)

    # ---- top level -----------------------------------------------------

    def program(self, prog):
        # Signatures first, so functions can call each other in any order.
        for item in prog.items:
            if isinstance(item, S.FnDecl):
                require_snake(item.name, "function", item.pos)
                for p in item.params:
                    require_snake(p.name, "parameter", p.pos)
                self.declare_unique(item.name, item.pos)
                params = [self.resolve_type(p.type) for p in item.params]
                if len(params) > 8:
                    raise CompileError("Fledgling supports at most 8 parameters", item.pos)
                ret = self.resolve_type(item.ret) if item.ret else UNIT
                self.fns[item.name] = FnSig(item, params, ret)
        # Constants in order: each may use the ones before it.
        for item in prog.items:
            if isinstance(item, S.ConstDecl):
                if not SCREAMING.match(item.name):
                    raise CompileError(
                        f"top-level constants are SCREAMING_CASE, like `{to_snake(item.name).upper()}`: "
                        f"`{item.name}`", item.pos)
                self.declare_unique(item.name, item.pos)
                ty = self.resolve_type(item.type)
                self.scopes, self.fn = [], None
                self.expr(item.value, ty)
                self.consts[item.name] = (ty, self.const_eval(item.value))
        for item in prog.items:
            if isinstance(item, S.FnDecl) and item.body is not None:
                self.function(item)

    def declare_unique(self, name, pos):
        if name in self.fns or name in self.consts:
            raise CompileError(f"`{name}` is already defined", pos)

    def resolve_type(self, ref):
        ty = NAMED_TYPES.get(ref.name)
        if ty is None:
            raise CompileError(f"unknown type `{ref.name}`", ref.pos)
        return ty

    def function(self, decl):
        self.fn = self.fns[decl.name]
        self.scopes = [{}]
        self.nslots = 0
        self.loops = []          # labels of the enclosing loops, innermost last
        for param, ty in zip(decl.params, self.fn.params):
            if param.name in self.scopes[0]:
                raise CompileError(f"duplicate parameter `{param.name}`", param.pos)
            param.ty = ty
            param.slot = self.new_local(param.name, ty, mutable=False, pos=param.pos)
        returns = self.block(decl.body)
        if self.fn.ret != UNIT and not returns:
            raise CompileError(
                f"`{decl.name}` must return a {self.fn.ret} on every path", decl.pos)
        decl.nslots = self.nslots

    def new_local(self, name, ty, mutable, pos=None):
        # No shadowing (principle 3): a name means one thing wherever it's visible.
        if pos is not None:
            if self.lookup(name) is not None:
                raise CompileError(
                    f"`{name}` is already declared here; choose a different name", pos)
            if name in self.fns:
                raise CompileError(f"`{name}` is already the name of a function", pos)
        slot = self.nslots
        self.nslots += 1
        self.scopes[-1][name] = Local(ty, mutable, slot)
        return slot

    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    # ---- statements ----------------------------------------------------
    # Each returns True if it always returns (control never falls through).

    def block(self, block):
        self.scopes.append({})
        returns = False
        for stmt in block.stmts:
            if self.stmt(stmt):
                returns = True
        self.scopes.pop()
        return returns

    def stmt(self, s):
        if isinstance(s, S.Decl):
            require_snake(s.name, "variable", s.pos)
            ty = self.resolve_type(s.type)
            self.expr(s.init, ty)
            s.slot = self.new_local(s.name, ty, s.mutable, pos=s.pos)
            return False

        if isinstance(s, S.Assign):
            local = self.lookup(s.target.ident)
            if local is None:
                if s.target.ident in self.consts:
                    raise CompileError(f"can't assign to constant `{s.target.ident}`", s.target.pos)
                raise CompileError(f"unknown variable `{s.target.ident}`", s.target.pos)
            if not local.mutable:
                raise CompileError(
                    f"can't assign to `{s.target.ident}`: declare it with `mut` to make it mutable",
                    s.pos)
            if s.op != "=":
                s.value = S.Binary(s.pos, s.op[:-1], S.Name(s.target.pos, s.target.ident), s.value)
                s.op = "="
            self.expr(s.target)
            self.expr(s.value, local.ty)
            return False

        if isinstance(s, S.ExprStmt):
            if s.discard:
                if self.expr(s.expr) == UNIT:
                    raise CompileError("nothing to discard: the call returns no value", s.expr.pos)
                return False
            if not isinstance(s.expr, S.Call):
                raise CompileError("this expression's value is unused", s.pos)
            if self.expr(s.expr) != UNIT:
                raise CompileError(
                    f"the result of `{s.expr.callee}` is unused; "
                    f"write `discard {s.expr.callee}(...)` to ignore it on purpose", s.pos)
            return False

        if isinstance(s, S.If):
            self.expr(s.cond, BOOL)
            then_returns = self.block(s.then)
            if s.else_ is None:
                return False
            if isinstance(s.else_, S.If):
                else_returns = self.stmt(s.else_)
            else:
                else_returns = self.block(s.else_)
            return then_returns and else_returns

        if isinstance(s, S.Loop):
            require_snake(s.label, "loop label", s.pos)
            if s.cond is not None:
                self.expr(s.cond, BOOL)
            self.loops.append(s.label)
            self.block(s.body)
            self.loops.pop()
            # `loop { }` (or `loop true`) that nothing breaks out of never falls through.
            forever = s.cond is None or (isinstance(s.cond, S.BoolLit) and s.cond.value)
            return forever and not breaks_to(s.body, s.label)

        if isinstance(s, S.ForRange):
            require_snake(s.label, "loop label", s.pos)
            require_snake(s.var, "loop variable", s.pos)
            ty = self.resolve_type(s.type)
            if not is_int(ty):
                raise CompileError(f"a range needs integers, found {ty}", s.start.pos)
            self.expr(s.start, ty)
            self.expr(s.end, ty)
            s.ty = ty
            self.scopes.append({})
            s.end_slot = self.new_local(" end", ty, mutable=False)
            s.slot = self.new_local(s.var, ty, mutable=False, pos=s.pos)
            self.loops.append(s.label)
            self.block(s.body)
            self.loops.pop()
            self.scopes.pop()
            return False

        if isinstance(s, (S.Break, S.Continue)):
            if s.label not in self.loops:
                word = "break" if isinstance(s, S.Break) else "continue"
                if not self.loops:
                    raise CompileError(f"`{word}` outside a loop", s.pos)
                raise CompileError(f"`{word} {s.label}`: no enclosing loop is named `{s.label}`",
                                   s.pos)
            return False

        if isinstance(s, S.Return):
            if s.value is None:
                if self.fn.ret != UNIT:
                    raise CompileError(f"missing return value of type {self.fn.ret}", s.pos)
            else:
                if self.fn.ret == UNIT:
                    raise CompileError(
                        f"`{self.fn.decl.name}` has no return type, so `return` takes no value",
                        s.value.pos)
                self.expr(s.value, self.fn.ret)
            return True

        raise AssertionError(f"unknown statement {s!r}")

    # ---- expressions ---------------------------------------------------

    def expr(self, e, expected=None, hint=None):
        """Check `e`, set `e.ty`, and return it. If `expected` is given, the
        expression must have that type. A `hint` only guides the types of
        literals inside `e`; a mismatch is reported by the caller."""
        ty = self.infer(e, expected if expected is not None else hint)
        e.ty = ty
        if expected is not None and ty != expected:
            raise CompileError(f"expected {expected}, found {ty}", e.pos)
        return ty

    def infer(self, e, expected):
        if isinstance(e, S.IntLit):
            ty = expected if is_int(expected) else (U8 if e.is_char else U64)
            if not ty.min <= e.value <= ty.max:
                raise CompileError(
                    f"literal {e.value} is out of range for {ty} ({ty.min} to {ty.max})", e.pos)
            return ty

        if isinstance(e, S.BoolLit):
            return BOOL

        if isinstance(e, S.Name):
            local = self.lookup(e.ident) if self.scopes else None
            if local is not None:
                e.slot = local.slot
                return local.ty
            if e.ident in self.consts:
                ty, value = self.consts[e.ident]
                e.const_value = value
                return ty
            if e.ident in self.fns:
                raise CompileError(f"`{e.ident}` is a function; call it with `{e.ident}(...)`", e.pos)
            raise CompileError(f"unknown name `{e.ident}`", e.pos)

        if isinstance(e, S.Unary):
            if e.op == "-":
                ty = self.expr(e.operand, hint=expected if is_int(expected) else None)
                if not (is_int(ty) and ty.signed):
                    raise CompileError(f"can't negate a value of type {ty}", e.pos)
                return ty
            ty = self.expr(e.operand, hint=expected if expected == BOOL or is_int(expected) else None)
            if ty != BOOL and not is_int(ty):
                raise CompileError(f"`not` needs a bool or an integer, found {ty}", e.pos)
            return ty

        if isinstance(e, S.IfExpr):
            self.expr(e.cond, BOOL)
            hint = expected
            return self.same_type_pair(e.then, e.else_, hint, "both branches of `if`")

        if isinstance(e, S.Binary):
            return self.binary(e, expected)

        if isinstance(e, S.Cast):
            target = self.resolve_type(e.target)
            if not is_int(target):
                raise CompileError(f"can only convert to integer types, not {target}", e.target.pos)
            # A bare literal takes the target type directly: `5 as u8` is a u8 literal.
            hint = target if e.kind == "as" and untyped_literal(e.expr) else None
            src = self.expr(e.expr, hint=hint)
            if not is_int(src) and src != BOOL:
                raise CompileError(f"can't convert {src} to {target}", e.pos)
            if e.kind == "as" and not lossless(src, target):
                raise CompileError(
                    f"`as` can't convert {src} to {target}: some values would be lost; "
                    f"use `wrap {target}` to keep the low bits on purpose", e.pos)
            if e.kind == "wrap" and lossless(src, target):
                raise CompileError(
                    f"`wrap` can't lose anything converting {src} to {target}; use `as`", e.pos)
            return target

        if isinstance(e, S.Call):
            sig = self.fns.get(e.callee)
            if sig is None:
                if self.lookup(e.callee) if self.scopes else None:
                    raise CompileError(f"`{e.callee}` is a variable, not a function", e.pos)
                raise CompileError(f"unknown function `{e.callee}`", e.pos)
            if self.fn is None:
                raise CompileError("constants can't call functions", e.pos)
            if len(e.args) != len(sig.params):
                raise CompileError(
                    f"`{e.callee}` takes {len(sig.params)} argument(s), but {len(e.args)} given",
                    e.pos)
            for arg, pty in zip(e.args, sig.params):
                self.expr(arg, pty)
            return sig.ret

        raise AssertionError(f"unknown expression {e!r}")

    def same_type_pair(self, a, b, hint, what):
        """Check two expressions that must share a type. The one that isn't a
        bare literal is checked first, so the literal can take its type."""
        if untyped_literal(a) and not untyped_literal(b):
            bty = self.expr(b, hint=hint)
            aty = self.expr(a, hint=bty if is_int(bty) else hint)
        else:
            aty = self.expr(a, hint=hint)
            bty = self.expr(b, hint=aty)
        if aty != bty:
            raise CompileError(f"{what} must have the same type, found {aty} and {bty}", b.pos)
        return aty

    def binary(self, e, expected):
        op = e.op
        if op in SHIFTS:
            hint = expected if is_int(expected) else None
            lty = self.expr(e.left, hint=hint)
            if not is_int(lty):
                raise CompileError(f"`{op}` needs an integer on the left, found {lty}", e.left.pos)
            rty = self.expr(e.right)
            if not is_int(rty):
                raise CompileError(f"shift amount must be an integer, found {rty}", e.right.pos)
            return lty

        # Both operands share a type. Check the side that isn't a bare literal
        # first, so the literal can take its type.
        if op in ARITH:
            hint = expected if is_int(expected) else None
        elif op in LOGIC:
            hint = expected if expected == BOOL or is_int(expected) else None
        else:
            hint = None
        if untyped_literal(e.left) and not untyped_literal(e.right):
            rty = self.expr(e.right, hint=hint)
            lty = self.expr(e.left, hint=rty if is_int(rty) else hint)
        else:
            lty = self.expr(e.left, hint=hint)
            rty = self.expr(e.right, hint=lty)
        if lty != rty:
            raise CompileError(f"`{op}` needs both sides to have the same type, found {lty} and {rty}",
                               e.pos)

        if op in ARITH:
            if not is_int(lty):
                raise CompileError(f"`{op}` needs integers, found {lty}", e.pos)
            return lty
        if op in LOGIC:
            if lty != BOOL and not is_int(lty):
                raise CompileError(f"`{op}` needs bools or integers, found {lty}", e.pos)
            return lty
        if op in EQUALITY:
            if not is_int(lty) and lty != BOOL:
                raise CompileError(f"can't compare values of type {lty}", e.pos)
            return BOOL
        if op in ORDERING:
            if not is_int(lty):
                raise CompileError(f"`{op}` needs integers, found {lty}", e.pos)
            return BOOL
        raise AssertionError(f"unknown operator {op}")

    # ---- constants -----------------------------------------------------

    def const_eval(self, e):
        """Evaluate a checked constant expression, with the same overflow
        rules as running code (but reported at compile time)."""
        if isinstance(e, S.IntLit):
            return e.value
        if isinstance(e, S.BoolLit):
            return int(e.value)
        if isinstance(e, S.Name):
            return e.const_value
        if isinstance(e, S.Cast):
            return wrap(self.const_eval(e.expr), NAMED_TYPES[e.target.name])
        if isinstance(e, S.Unary):
            v = self.const_eval(e.operand)
            if e.op == "-":
                return self.fit(-v, e)
            if e.ty == BOOL:
                return 1 - v
            return wrap(~v, e.ty)
        if isinstance(e, S.IfExpr):
            return self.const_eval(e.then) if self.const_eval(e.cond) else self.const_eval(e.else_)
        if isinstance(e, S.Binary):
            a, b = self.const_eval(e.left), self.const_eval(e.right)
            op = e.op
            if op in ("/", "%") and b == 0:
                raise CompileError("division by zero in constant", e.pos)
            if op == "+": return self.fit(a + b, e)
            if op == "-": return self.fit(a - b, e)
            if op == "*": return self.fit(a * b, e)
            if op == "/": return self.fit(trunc_div(a, b), e)
            if op == "%": return a - b * trunc_div(a, b)
            if op == "and": return a & b
            if op == "or": return a | b
            if op == "xor": return a ^ b
            if op in SHIFTS:
                if not 0 <= b < e.ty.bits:
                    raise CompileError(f"shift amount {b} is too large for {e.ty}", e.pos)
                if op == "shr":
                    return a >> b
                result = wrap(a << b, e.ty)
                if result >> b != a:
                    raise CompileError(f"`shl` would lose set bits of {e.ty}", e.pos)
                return result
            if op == "==": return int(a == b)
            if op == "!=": return int(a != b)
            if op == "<": return int(a < b)
            if op == "<=": return int(a <= b)
            if op == ">": return int(a > b)
            if op == ">=": return int(a >= b)
        raise CompileError("constants must be computed from literals and other constants", e.pos)

    def fit(self, v, e):
        if not e.ty.min <= v <= e.ty.max:
            raise CompileError(f"constant overflows {e.ty}", e.pos)
        return v


def wrap(v, ty):
    """Truncate `v` to `ty`'s width, as `as` does."""
    v &= (1 << ty.bits) - 1
    if ty.signed and v >= 1 << (ty.bits - 1):
        v -= 1 << ty.bits
    return v


def trunc_div(a, b):
    """Integer division rounding toward zero, as AArch64 sdiv/udiv do."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def untyped_literal(e):
    return isinstance(e, S.IntLit)


def breaks_to(block, label):
    """Does anything inside `block`, at any depth, `break label`?"""
    for s in block.stmts:
        if isinstance(s, S.Break) and s.label == label:
            return True
        if isinstance(s, (S.Loop, S.ForRange)) and breaks_to(s.body, label):
            return True
        if isinstance(s, S.If):
            branch = s
            while isinstance(branch, S.If):
                if breaks_to(branch.then, label):
                    return True
                branch = branch.else_
            if isinstance(branch, S.Block) and breaks_to(branch, label):
                return True
    return False
