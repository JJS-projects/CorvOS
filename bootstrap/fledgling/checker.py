"""Type checker: resolves names and types, and rejects invalid programs.

Besides checking, it annotates the tree for the code generator:
  - every expression gets `.ty`
  - every local variable and parameter gets a stack `.slot`
  - names of constants get `.const_value`
  - `x += e` is rewritten to `x = x + e`

Integer literals take their type from context (the declared type of a
variable, the other operand, a parameter type). With no context they default
to U64; character literals default to U8.
"""

from .errors import CompileError
from . import syntax as S
from .rtypes import BOOL, UNIT, U8, U64, NAMED_TYPES, IntType, is_int

ARITH = {"+", "-", "*", "/", "%"}
BITWISE = {"&", "|", "^"}
SHIFTS = {"<<", ">>"}
EQUALITY = {"==", "!="}
ORDERING = {"<", "<=", ">", ">="}
LOGICAL = {"&&", "||"}


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
                self.declare_unique(item.name, item.pos)
                params = [self.resolve_type(p.type) for p in item.params]
                if len(params) > 8:
                    raise CompileError("Fledgling supports at most 8 parameters", item.pos)
                ret = self.resolve_type(item.ret) if item.ret else UNIT
                self.fns[item.name] = FnSig(item, params, ret)
        # Constants in order: each may use the ones before it.
        for item in prog.items:
            if isinstance(item, S.ConstDecl):
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
        self.loop_depth = 0
        for param, ty in zip(decl.params, self.fn.params):
            if param.name in self.scopes[0]:
                raise CompileError(f"duplicate parameter `{param.name}`", param.pos)
            param.ty = ty
            param.slot = self.new_local(param.name, ty, mutable=False)
        returns = self.block(decl.body)
        if self.fn.ret != UNIT and not returns:
            raise CompileError(
                f"`{decl.name}` must return a {self.fn.ret} on every path", decl.pos)
        decl.nslots = self.nslots

    def new_local(self, name, ty, mutable):
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
        if isinstance(s, S.Let):
            expected = self.resolve_type(s.type) if s.type else None
            ty = self.expr(s.init, expected)
            if ty == UNIT:
                raise CompileError(f"`{s.name}` would have no value: the call returns nothing", s.init.pos)
            s.slot = self.new_local(s.name, ty, s.mutable)
            return False

        if isinstance(s, S.Assign):
            local = self.lookup(s.target.ident)
            if local is None:
                if s.target.ident in self.consts:
                    raise CompileError(f"can't assign to constant `{s.target.ident}`", s.target.pos)
                raise CompileError(f"unknown variable `{s.target.ident}`", s.target.pos)
            if not local.mutable:
                raise CompileError(
                    f"can't assign to `{s.target.ident}`: declare it with `var` to make it mutable",
                    s.pos)
            if s.op != "=":
                s.value = S.Binary(s.pos, s.op[:-1], S.Name(s.target.pos, s.target.ident), s.value)
                s.op = "="
            self.expr(s.target)
            self.expr(s.value, local.ty)
            return False

        if isinstance(s, S.ExprStmt):
            if not isinstance(s.expr, S.Call):
                raise CompileError("this expression's value is unused", s.pos)
            self.expr(s.expr)
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

        if isinstance(s, S.While):
            self.expr(s.cond, BOOL)
            self.loop_depth += 1
            self.block(s.body)
            self.loop_depth -= 1
            # `while true` with no `break` never falls through.
            forever = isinstance(s.cond, S.BoolLit) and s.cond.value
            return forever and not contains_break(s.body)

        if isinstance(s, (S.Break, S.Continue)):
            if self.loop_depth == 0:
                word = "break" if isinstance(s, S.Break) else "continue"
                raise CompileError(f"`{word}` outside a loop", s.pos)
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

    def expr(self, e, expected=None):
        """Check `e`, set `e.ty`, and return it. If `expected` is given, the
        expression must have that type."""
        ty = self.infer(e, expected)
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
                ty = self.expr(e.operand, expected if is_int(expected) else None)
                if not (is_int(ty) and ty.signed):
                    raise CompileError(f"can't negate a value of type {ty}", e.pos)
                return ty
            ty = self.expr(e.operand, expected if expected in (BOOL,) or is_int(expected) else None)
            if ty != BOOL and not is_int(ty):
                raise CompileError(f"`!` needs a Bool or an integer, found {ty}", e.pos)
            return ty

        if isinstance(e, S.Binary):
            return self.binary(e, expected)

        if isinstance(e, S.Cast):
            target = self.resolve_type(e.target)
            if not is_int(target):
                raise CompileError(f"can only cast to integer types, not {target}", e.target.pos)
            src = self.expr(e.expr)
            if not is_int(src) and src != BOOL:
                raise CompileError(f"can't cast {src} to {target}", e.pos)
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

    def binary(self, e, expected):
        op = e.op
        if op in LOGICAL:
            self.expr(e.left, BOOL)
            self.expr(e.right, BOOL)
            return BOOL

        if op in SHIFTS:
            hint = expected if is_int(expected) else None
            lty = self.expr(e.left, hint)
            if not is_int(lty):
                raise CompileError(f"`{op}` needs an integer on the left, found {lty}", e.left.pos)
            rty = self.expr(e.right)
            if not is_int(rty):
                raise CompileError(f"shift amount must be an integer, found {rty}", e.right.pos)
            return lty

        # Both operands share a type. Check the side that isn't a bare literal
        # first, so the literal can take its type.
        if op in ARITH or op in BITWISE:
            hint = expected if is_int(expected) else None
        else:
            hint = None
        if untyped_literal(e.left) and not untyped_literal(e.right):
            rty = self.expr(e.right, hint)
            lty = self.expr(e.left, rty if is_int(rty) else None)
        else:
            lty = self.expr(e.left, hint)
            rty = self.expr(e.right, lty if untyped_literal(e.right) and is_int(lty) else None)
        if lty != rty:
            raise CompileError(f"`{op}` needs both sides to have the same type, found {lty} and {rty}",
                               e.pos)

        if op in ARITH or op in BITWISE:
            if not is_int(lty):
                raise CompileError(f"`{op}` needs integers, found {lty}", e.pos)
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
            if op == "&": return a & b
            if op == "|": return a | b
            if op == "^": return a ^ b
            if op in SHIFTS:
                if not 0 <= b < e.ty.bits:
                    raise CompileError(f"shift amount {b} is too large for {e.ty}", e.pos)
                return wrap(a << b, e.ty) if op == "<<" else a >> b
            if op == "==": return int(a == b)
            if op == "!=": return int(a != b)
            if op == "<": return int(a < b)
            if op == "<=": return int(a <= b)
            if op == ">": return int(a > b)
            if op == ">=": return int(a >= b)
            if op == "&&": return int(a and b)
            if op == "||": return int(a or b)
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


def contains_break(block):
    """Does `block` contain a `break` for its enclosing loop (not a nested one)?"""
    for s in block.stmts:
        if isinstance(s, S.Break):
            return True
        if isinstance(s, S.If):
            if contains_break(s.then):
                return True
            branch = s.else_
            while isinstance(branch, S.If):
                if contains_break(branch.then):
                    return True
                branch = branch.else_
            if isinstance(branch, S.Block) and contains_break(branch):
                return True
    return False
