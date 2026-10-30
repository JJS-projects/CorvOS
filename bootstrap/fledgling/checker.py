"""Type checker: resolves names and types, and rejects invalid programs.

Besides checking, it annotates the tree for the code generator:
  - every expression gets `.ty`
  - every local variable and parameter gets a stack `.slot`
  - names of constants get `.const_value`
  - `x += e` is rewritten to `x = x + e`

Integer literals take their type from context (the declared type of a
variable, the other operand, a parameter type). With no context they default
to u64; character literals default to u8.

Naming conventions are enforced: functions, parameters, variables and fields
are snake_case, types are CamelCase, top-level constants are SCREAMING_CASE.

Structs are lent, not copied: a struct parameter receives the caller's value by
reference, read-only unless the parameter is `mut T` and the caller writes
`mut x`. A new struct value can only come from a "fresh" expression (a struct
literal, a call, or `.copy()`), so an existing value is never copied silently.

Locals are stored in one of three ways (`Local.kind`):
  value   a number or bool, in one 8-byte slot
  region  a struct owned by this function, in consecutive slots
  ref     a pointer, in one slot, to a lent struct or `mut` parameter
"""

import copy
import re


from .errors import CompileError
from . import syntax as S
from .rtypes import (BOOL, UNIT, U8, U64, NAMED_TYPES, IntType, StructType, is_int,
                     is_struct, lossless, words)

ARITH = {"+", "-", "*", "/", "%"}
LOGIC = {"and", "or", "xor"}     # logical on bool, bitwise on integers
SHIFTS = {"shl", "shr"}
EQUALITY = {"==", "!="}
ORDERING = {"<", "<=", ">", ">="}

SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
SCREAMING = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")
CAMEL = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def require_snake(name, what, pos):
    if not SNAKE.match(name):
        raise CompileError(f"{what} names are snake_case, like `{to_snake(name)}`: `{name}`", pos)


def to_snake(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return re.sub(r"_+", "_", name).strip("_") or "name"


def check(program):
    Checker().program(program)


class Local:
    def __init__(self, name, ty, mutable, slot, kind):
        self.name, self.ty, self.mutable, self.slot, self.kind = name, ty, mutable, slot, kind


class FnSig:
    def __init__(self, decl, params, ret, owner=None):
        self.decl = decl
        self.params = params        # [(type, is_mut)]
        self.ret = ret
        self.owner = owner          # StructType for methods
        self.mutating = decl.mutating


class Checker:
    def __init__(self):
        self.fns = {}
        self.consts = {}         # name -> (type, value)
        self.structs = {}        # name -> StructType
        self.receiver = None     # (StructType, mutating) inside a method

    # ---- top level -----------------------------------------------------

    def program(self, prog):
        # Struct names first, so types can refer to each other in any order.
        for item in prog.items:
            if isinstance(item, S.StructDecl):
                if not (CAMEL.match(item.name) and any(c.islower() for c in item.name)):
                    raise CompileError(f"type names are CamelCase, like `Point`: `{item.name}`",
                                       item.pos)
                self.declare_unique(item.name, item.pos)
                self.structs[item.name] = StructType(item.name, item)
        for st in self.structs.values():
            self.struct_fields(st)
        for st in self.structs.values():
            self.struct_size(st, [])
        # Signatures next, so functions and methods can call each other in any order.
        for item in prog.items:
            if isinstance(item, S.FnDecl):
                require_snake(item.name, "function", item.pos)
                self.declare_unique(item.name, item.pos)
                self.fns[item.name] = self.signature(item, None)
        for st in self.structs.values():
            field_names = {name for name, _ in st.fields}
            for m in st.decl.methods:
                require_snake(m.name, "method", m.pos)
                if m.name == "copy":
                    raise CompileError("`copy` is built in for every struct; choose another name",
                                       m.pos)
                if m.name in field_names:
                    raise CompileError(f"`{m.name}` is already a field of `{st.name}`", m.pos)
                if m.name in st.methods:
                    raise CompileError(f"`{st.name}` already has a method `{m.name}`", m.pos)
                st.methods[m.name] = self.signature(m, st)
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
                self.function(self.fns[item.name])
        for st in self.structs.values():
            for sig in st.methods.values():
                self.function(sig)

    def declare_unique(self, name, pos):
        if name in self.fns or name in self.consts or name in self.structs:
            raise CompileError(f"`{name}` is already defined", pos)

    def resolve_type(self, ref):
        ty = NAMED_TYPES.get(ref.name) or self.structs.get(ref.name)
        if ty is None:
            raise CompileError(f"unknown type `{ref.name}`", ref.pos)
        return ty

    def struct_fields(self, st):
        seen = set()
        for f in st.decl.fields:
            require_snake(f.name, "field", f.pos)
            if f.name in seen:
                raise CompileError(f"`{st.name}` already has a field `{f.name}`", f.pos)
            seen.add(f.name)
            st.fields.append((f.name, self.resolve_type(f.type)))
        if not st.fields:
            raise CompileError(f"`{st.name}` has no fields", st.decl.pos)

    def struct_size(self, st, path):
        if st.words is not None:
            return st.words
        if st in path:
            raise CompileError(
                f"`{st.name}` contains itself, so it would have no fixed size", st.decl.pos)
        total = 0
        for _, ty in st.fields:
            total += self.struct_size(ty, path + [st]) if is_struct(ty) else 1
        st._words = total
        return total

    def signature(self, decl, owner):
        params = []
        for p in decl.params:
            require_snake(p.name, "parameter", p.pos)
            params.append((self.resolve_type(p.type), p.type.mut))
        limit = 7 if owner else 8
        if len(params) > limit:
            raise CompileError(f"Fledgling supports at most {limit} parameters here", decl.pos)
        ret = self.resolve_type(decl.ret) if decl.ret else UNIT
        return FnSig(decl, params, ret, owner)

    def function(self, sig):
        decl = sig.decl
        self.fn = sig
        self.scopes = [{}]
        self.nslots = 0
        self.loops = []          # labels of the enclosing loops, innermost last
        self.receiver = (sig.owner, sig.mutating) if sig.owner else None
        if sig.owner:
            decl.self_slot = self.alloc(1)
        if is_struct(sig.ret):
            decl.result_slot = self.alloc(1)
        for param, (ty, is_mut) in zip(decl.params, sig.params):
            if param.name in self.scopes[0]:
                raise CompileError(f"duplicate parameter `{param.name}`", param.pos)
            param.ty = ty
            kind = "ref" if is_mut or is_struct(ty) else "value"
            param.slot = self.new_local(param.name, ty, is_mut, kind, pos=param.pos)
        returns = self.block(decl.body)
        if sig.ret != UNIT and not returns:
            raise CompileError(
                f"`{decl.name}` must return a {sig.ret} on every path", decl.pos)
        decl.nslots = self.nslots
        self.receiver = None

    def alloc(self, n):
        slot = self.nslots
        self.nslots += n
        return slot

    def new_local(self, name, ty, mutable, kind="value", pos=None):
        # No shadowing (principle 4): a name means one thing wherever it's visible.
        if pos is not None:
            if self.lookup(name) is not None:
                raise CompileError(
                    f"`{name}` is already declared here; choose a different name", pos)
            if name in self.fns:
                raise CompileError(f"`{name}` is already the name of a function", pos)
        slot = self.alloc(words(ty) if kind == "region" else 1)
        self.scopes[-1][name] = Local(name, ty, mutable, slot, kind)
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
            if is_struct(ty):
                self.require_fresh(s.init, f"`{s.name}`")
            kind = "region" if is_struct(ty) else "value"
            s.slot = self.new_local(s.name, ty, s.mutable, kind, pos=s.pos)
            return False

        if isinstance(s, S.Assign):
            if isinstance(s.target, S.Name) and self.lookup(s.target.ident) is None:
                if s.target.ident in self.consts:
                    raise CompileError(f"can't assign to constant `{s.target.ident}`", s.target.pos)
                raise CompileError(f"unknown variable `{s.target.ident}`", s.target.pos)
            target_ty = self.expr(s.target)
            self.require_mutable(s.target, s.pos)
            if s.op != "=":
                if not is_int(target_ty):
                    raise CompileError(f"`{s.op}` needs an integer, found {target_ty}", s.pos)
                s.value = S.Binary(s.pos, s.op[:-1], copy.deepcopy(s.target), s.value)
                s.op = "="
            self.expr(s.value, target_ty)
            if is_struct(target_ty):
                self.require_fresh(s.value, describe_place(s.target))
            return False

        if isinstance(s, S.ExprStmt):
            if s.discard:
                if self.expr(s.expr) == UNIT:
                    raise CompileError("nothing to discard: the call returns no value", s.expr.pos)
                return False
            if not isinstance(s.expr, (S.Call, S.MethodCall)):
                raise CompileError("this expression's value is unused", s.pos)
            if self.expr(s.expr) != UNIT:
                name = s.expr.callee if isinstance(s.expr, S.Call) else s.expr.name
                raise CompileError(
                    f"the result of `{name}` is unused; "
                    f"write `discard {name}(...)` to ignore it on purpose", s.pos)
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
                if is_struct(self.fn.ret):
                    owned_local = (isinstance(s.value, S.Name)
                                   and s.value.local.kind == "region")
                    if not owned_local:
                        self.require_fresh(s.value, "the result")
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
                e.local = local
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
            self.check_args(f"`{e.callee}`", sig.params, e.args, e.pos)
            if is_struct(sig.ret):
                e.temp = self.alloc(sig.ret.words)
            return sig.ret

        if isinstance(e, S.StructLit):
            st = self.structs.get(e.name)
            if st is None:
                raise CompileError(f"unknown type `{e.name}`", e.pos)
            if self.fn is None:
                raise CompileError("constants must be numbers or bools", e.pos)
            given = [name for name, _, _ in e.fields]
            wanted = [name for name, _ in st.fields]
            for name, _, pos in e.fields:
                if name not in wanted:
                    raise CompileError(f"`{st.name}` has no field `{name}`", pos)
            if given != wanted:
                missing = [n for n in wanted if n not in given]
                if missing:
                    raise CompileError(f"`{st.name}(...)` is missing `{missing[0]}`", e.pos)
                raise CompileError(
                    f"write the fields of `{st.name}` once each, in the order they are declared: "
                    f"{', '.join(wanted)}", e.pos)
            for (name, value, _), (_, fty) in zip(e.fields, st.fields):
                self.expr(value, fty)
                if is_struct(fty):
                    self.require_fresh(value, f"field `{name}`")
            e.temp = self.alloc(st.words)
            return st

        if isinstance(e, S.FieldAccess):
            st = self.struct_of(e)
            e.struct = st
            if not any(name == e.field for name, _ in st.fields):
                hint = " (it's a method: call it with `(...)`)" if e.field in st.methods else ""
                raise CompileError(f"`{st.name}` has no field `{e.field}`{hint}", e.pos)
            return st.offset(e.field)[1]

        if isinstance(e, S.MethodCall):
            st = self.struct_of(e)
            if e.name == "copy":
                if e.args:
                    raise CompileError("`copy()` takes no arguments", e.pos)
                e.temp = self.alloc(st.words)
                return st
            sig = st.methods.get(e.name)
            if sig is None:
                raise CompileError(f"`{st.name}` has no method `{e.name}`", e.pos)
            e.sig = sig
            if sig.mutating:
                receiver = e.obj if e.obj is not None else None
                if receiver is None:
                    if not self.receiver[1]:
                        raise CompileError(
                            f"`.{e.name}()` changes the struct, so it can only be called from "
                            f"a `mut fn`", e.pos)
                else:
                    self.require_mutable(receiver, e.pos, f"call `mut fn {e.name}` on")
            # `.m()` inside a method acts on the method's own struct ("self").
            receiver = e.obj if e.obj is not None else S.FieldAccess(e.pos, None, "")
            self.check_args(f"`{st.name}.{e.name}`", sig.params, e.args, e.pos,
                            receiver=receiver, receiver_mut=sig.mutating)
            if is_struct(sig.ret):
                e.temp = self.alloc(sig.ret.words)
            return sig.ret

        if isinstance(e, S.MutArg):
            raise CompileError("`mut` only marks an argument passed to a `mut` parameter", e.pos)

        raise AssertionError(f"unknown expression {e!r}")

    # ---- structs, places and lending ----------------------------------

    def struct_of(self, e):
        """The struct type that `e.field` or `e.method()` acts on."""
        if e.obj is None:
            if self.receiver is None:
                what = e.field if isinstance(e, S.FieldAccess) else e.name
                raise CompileError(f"`.{what}` only works inside a struct's methods", e.pos)
            return self.receiver[0]
        ty = self.expr(e.obj)
        if not is_struct(ty):
            what = e.field if isinstance(e, S.FieldAccess) else e.name
            if isinstance(e, S.MethodCall) and e.name == "copy":
                raise CompileError(f"{ty} values are always copied; `.copy()` is for structs",
                                   e.pos)
            raise CompileError(f"{ty} has no field or method `{what}`", e.pos)
        return ty

    def check_args(self, callee, params, args, pos, receiver=None, receiver_mut=False):
        if len(args) != len(params):
            raise CompileError(f"{callee} takes {len(params)} argument(s), but {len(args)} given",
                               pos)
        lent = []                       # (root name, is_mut, pos) of structs lent by reference
        if receiver is not None and root_name(receiver) is not None:
            lent.append((root_name(receiver), receiver_mut, receiver.pos))
        for arg, (pty, is_mut) in zip(args, params):
            if is_mut:
                if not isinstance(arg, S.MutArg):
                    raise CompileError(
                        f"{callee} can change this argument, so write `mut` before it", arg.pos)
                self.expr(arg.place, pty)
                arg.ty = pty
                self.require_mutable(arg.place, arg.pos, "lend as `mut`")
                lent.append((root_name(arg.place), True, arg.pos))
            else:
                if isinstance(arg, S.MutArg):
                    raise CompileError(
                        f"{callee} doesn't change this argument, so it isn't marked `mut`", arg.pos)
                self.expr(arg, pty)
                if is_struct(pty) and root_name(arg) is not None:
                    lent.append((root_name(arg), False, arg.pos))
        # A value lent as `mut` can't be lent again in the same call. Numbers
        # are copied when passed, so they can't alias.
        for i, (name, is_mut, at) in enumerate(lent):
            for other, other_mut, _ in lent[:i]:
                if name == other and (is_mut or other_mut):
                    raise CompileError(
                        f"`{name}` is lent as `mut` in this call, so it can't be lent again here",
                        at)

    def require_mutable(self, place, pos, action="assign to"):
        if isinstance(place, S.Name):
            local = place.local if hasattr(place, "local") else None
            if local is None:
                raise CompileError(f"can't {action} `{place.ident}`", pos)
            if not local.mutable:
                if local.kind == "ref":
                    raise CompileError(
                        f"can't {action} `{place.ident}`: it is lent read-only; "
                        f"take it as `mut {local.ty}` to change it", pos)
                raise CompileError(
                    f"can't {action} `{place.ident}`: declare it with `mut` to make it mutable",
                    pos)
            return
        if isinstance(place, S.FieldAccess):
            if place.obj is None:
                if not self.receiver[1]:
                    raise CompileError(
                        f"can't {action} `.{place.field}`: only a `mut fn` can change its struct",
                        pos)
                return
            self.require_mutable(place.obj, pos, action)
            return
        raise CompileError(f"can only {action} a variable or a field", pos)

    def require_fresh(self, e, what):
        """A struct value can be stored only if nothing else holds it."""
        if not is_fresh(e):
            raise CompileError(
                f"{what} would be a copy of {describe_place(e)}; write `{source_text(e)}.copy()` "
                f"to copy it", e.pos)

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
            if not is_int(lty) and lty != BOOL and not is_struct(lty):
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


def is_fresh(e):
    """Is `e` a new struct value that nothing else holds?"""
    if isinstance(e, (S.StructLit, S.Call, S.MethodCall)):
        return True
    if isinstance(e, S.IfExpr):
        return is_fresh(e.then) and is_fresh(e.else_)
    return False


def root_name(e):
    """The variable a place starts from (`p` in `p.a.b`), or "self" for `.x`."""
    while isinstance(e, S.FieldAccess):
        if e.obj is None:
            return "self"
        e = e.obj
    return e.ident if isinstance(e, S.Name) else None


def source_text(e):
    if isinstance(e, S.Name):
        return e.ident
    if isinstance(e, S.FieldAccess):
        return f"{source_text(e.obj) if e.obj is not None else ''}.{e.field}"
    return "value"


def describe_place(e):
    if isinstance(e, (S.Name, S.FieldAccess)):
        return f"`{source_text(e)}`"
    return "this value"


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
