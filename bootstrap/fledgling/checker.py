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

Structs and enums ("aggregates") are lent, not copied: a parameter receives
the caller's value by reference, read-only unless the parameter is `mut T` and
the caller writes `mut x`. A new value can only come from a "fresh" expression
(a literal, a call, `copy(...)`, or an `if` or `match` choosing between fresh
values), so an existing value is never copied silently.

A `match` binding of a struct or enum field is a read-only lend into the value
being matched. While one is live, the matched variable can't change: otherwise
`s = Idle` inside the arm would leave the binding reading another variant's data.

Locals are stored in one of three ways (`Local.kind`):
  value   a number or bool, in one 8-byte slot
  region  a struct or enum owned by this function, in consecutive slots
  ref     a pointer, in one slot, to a lent value or `mut` parameter
"""

import copy
import os
import re


from .errors import CompileError
from . import syntax as S
from .rtypes import (BOOL, UNIT, U8, U64, INT_TYPES, NAMED_TYPES, ActorType, EnumType, InterfaceType, IntType,
                     StructType, Variant, is_actor, is_aggregate, is_enum, is_int, is_struct,
                     lossless, words)

# What a message carries in this step: numbers, bools and actor references.
# Structs, enums and owned data wait for the ownership rules (decision 114).
MESSAGE_PARAMS = 4          # a handler takes at most 4 parameters
MESSAGE_WORDS = 8           # and a message carries at most 8 words (decision 135)
NEW_PARAMS = 7

# Built-in functions (decision 77: built-ins that produce a value are calls with
# reserved names). Loads and stores touch raw memory, so they are only allowed
# inside `unsafe { }`; each is exactly one access of that width, in program order.
LOADS = {f"load{n}": INT_TYPES[f"u{n}"] for n in (8, 16, 32, 64)}
STORES = {f"store{n}": INT_TYPES[f"u{n}"] for n in (8, 16, 32, 64)}
# Two more only Raven needs, to send a message of its own (an interrupt) to a
# reference it holds: `actor_address(reference)` is the reference as a number, and
# `handler_number("fired")` is the number messages use for the handlers of that name.
# Both are allowed only inside `unsafe { }` in trusted files, like loads and stores.
BUILTINS = set(LOADS) | set(STORES) | {"panic", "actor_address", "handler_number"}

ARITH = {"+", "-", "*", "/", "%"}
LOGIC = {"and", "or", "xor"}     # logical on bool, bitwise on integers
SHIFTS = {"shl", "shr"}
EQUALITY = {"==", "!="}
ORDERING = {"<", "<=", ">", ">="}

SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
SCREAMING = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")
CAMEL = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def require_camel(name, what, pos):
    if not (CAMEL.match(name) and any(c.islower() for c in name)):
        raise CompileError(f"{what} names are CamelCase, like `Point`: `{name}`", pos)


def require_snake(name, what, pos):
    if not SNAKE.match(name):
        raise CompileError(f"{what} names are snake_case, like `{to_snake(name)}`: `{name}`", pos)


def to_snake(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return re.sub(r"_+", "_", name).strip("_") or "name"


def check(program):
    Checker().program(program)


class Local:
    def __init__(self, name, ty, mutable, slot, kind, owned=False, val=False):
        self.name, self.ty, self.mutable, self.slot, self.kind = name, ty, mutable, slot, kind
        self.owned = owned      # an `iso` parameter: not a borrow, so it can be consumed
        self.val = val          # a `val` parameter: read-only data that can be passed on


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
        self.enums = {}          # name -> EnumType
        self.variants = {}       # variant name -> Variant (names are unique across enums)
        self.receiver = None     # (AggregateType, mutating) inside a method
        self.locked = {}         # variable -> a `match` binding lent from it
        self.const_decls = {}    # name -> ConstDecl
        self.file = None         # the file of the code being checked
        self.trusted = False     # ... and whether it is in a trusted directory
        self.unsafe_depth = 0    # inside `unsafe { }`
        self.actors = {}         # name -> ActorType
        self.interfaces = {}     # name -> InterfaceType
        self.handler_numbers = {}  # handler name -> its number in messages
        self.in_new = False      # checking an actor's `new`
        self.assigned = set()    # fields `new` has set so far
        self.moved = {}          # consumed places -> Pos of the `consume`; a place is a Local
                                 # (variable) or ".field" (an actor's `iso` field)
        self.break_states = []   # per enclosing loop: the moved states at its `break`s
        self.loop_entries = []   # per enclosing loop: (moved at entry, locals outside it)

    # ---- top level -----------------------------------------------------

    def program(self, prog):
        # Type and variant names first, so types can refer to each other in any order.
        for item in prog.items:
            if isinstance(item, (S.StructDecl, S.EnumDecl)):
                require_camel(item.name, "type", item.pos)
                self.declare_unique(item.name, item.pos)
                if isinstance(item, S.StructDecl):
                    self.structs[item.name] = StructType(item.name, item)
                else:
                    self.enums[item.name] = en = EnumType(item.name, item)
                    if not item.variants:
                        raise CompileError(f"`{item.name}` has no variants", item.pos)
                    for tag, vd in enumerate(item.variants):
                        require_camel(vd.name, "variant", vd.pos)
                        self.declare_unique(vd.name, vd.pos)
                        self.variants[vd.name] = en.variants[vd.name] = Variant(
                            vd.name, tag, en, vd.pos)
        for item in prog.items:
            if isinstance(item, S.ActorDecl):
                require_camel(item.name, "actor", item.pos)
                self.declare_unique(item.name, item.pos)
                self.actors[item.name] = item.ty = ActorType(item.name, item)
            elif isinstance(item, S.InterfaceDecl):
                require_camel(item.name, "interface", item.pos)
                self.declare_unique(item.name, item.pos)
                self.interfaces[item.name] = item.ty = InterfaceType(item.name, item)
        for st in self.structs.values():
            self.enter_file(st.decl)
            st.fields = self.field_list(st.decl.fields, f"`{st.name}`")
            st.public_fields = {f.name for f in st.decl.fields if f.public}
            if not st.fields:
                raise CompileError(f"`{st.name}` has no fields", st.decl.pos)
        for en in self.enums.values():
            self.enter_file(en.decl)
            for vd in en.decl.variants:
                en.variants[vd.name].fields = self.field_list(vd.fields or [], f"`{vd.name}`")
        for ty in self.aggregates():
            self.size(ty, [])
        for actor in self.actors.values():
            self.enter_file(actor.decl)
            self.actor_fields(actor)
        # Signatures next, so functions and methods can call each other in any order.
        for item in prog.items:
            if isinstance(item, S.FnDecl):
                self.enter_file(item)
                require_snake(item.name, "function", item.pos)
                if item.body is None and not item.trusted:
                    raise CompileError(
                        "`extern fn` can call any code, so it is only allowed in files under a "
                        "trusted directory, such as raven/trusted/", item.pos)
                self.declare_unique(item.name, item.pos)
                self.fns[item.name] = self.signature(item, None)
        for st in self.aggregates():
            self.enter_file(st.decl)
            field_names = {name for name, _ in st.fields} if is_struct(st) else set()
            for m in st.decl.methods:
                m.file, m.trusted = st.decl.file, st.decl.trusted
                require_snake(m.name, "method", m.pos)
                if m.name in field_names:
                    raise CompileError(f"`{m.name}` is already a field of `{st.name}`", m.pos)
                if m.name in st.methods:
                    raise CompileError(f"`{st.name}` already has a method `{m.name}`", m.pos)
                st.methods[m.name] = self.signature(m, st)
        for iface in self.interfaces.values():
            self.enter_file(iface.decl)
            self.interface_signatures(iface)
        for actor in self.actors.values():
            self.enter_file(actor.decl)
            self.actor_signatures(actor)
        self.number_handlers(prog)
        for actor in self.actors.values():
            self.enter_file(actor.decl)
            self.implements(actor)
        # Constants in order: each may use the ones before it.
        for item in prog.items:
            if isinstance(item, S.ConstDecl):
                if not SCREAMING.match(item.name):
                    raise CompileError(
                        f"top-level constants are SCREAMING_CASE, like `{to_snake(item.name).upper()}`: "
                        f"`{item.name}`", item.pos)
                self.enter_file(item)
                self.declare_unique(item.name, item.pos)
                ty = self.resolve_type(item.type)
                self.scopes, self.fn = [], None
                self.expr(item.value, ty)
                if is_aggregate(ty):
                    if not isinstance(item.value, S.StrLit):
                        raise CompileError("constants must be numbers, bools or text", item.value.pos)
                    value = item.value
                else:
                    value = self.const_eval(item.value)
                self.consts[item.name] = (ty, value)
                self.const_decls[item.name] = item
        for item in prog.items:
            if isinstance(item, S.FnDecl) and item.body is not None:
                self.function(self.fns[item.name])
        for st in self.aggregates():
            for sig in st.methods.values():
                self.function(sig)
        for actor in self.actors.values():
            if actor.new_sig is not None:
                self.function(actor.new_sig)
            for sig in actor.methods.values():
                self.function(sig)

    def actor_fields(self, actor):
        decl = actor.decl
        seen = set()
        for f in decl.fields:
            require_snake(f.name, "field", f.pos)
            if f.name in seen:
                raise CompileError(f"`{actor.name}` already has a field `{f.name}`", f.pos)
            seen.add(f.name)
            if f.public:
                raise CompileError(
                    "an actor's fields are private: other actors reach it with messages", f.pos)
            ty = self.resolve_type(f.type)
            if f.type.val and not is_aggregate(ty):
                raise CompileError(
                    f"`val` is for structs and enums; {ty} is already a plain value", f.type.pos)
            if f.type.iso and not is_aggregate(ty):
                raise CompileError(
                    f"`iso` is for structs and enums; {ty} is already a plain value", f.type.pos)
            if f.type.val and f.mutable:
                raise CompileError("a `val` field is read-only, so it can't be `mut`", f.pos)
            if is_aggregate(ty) and not (f.type.val or f.type.iso):
                raise CompileError(
                    f"an actor's field holds a number, a bool, a reference, owned data "
                    f"written `iso {ty}`, or read-only data written `val {ty}`, not a bare {ty}",
                    f.type.pos)
            actor.fields.append((f.name, ty))
            if f.type.val:
                actor.val_fields.add(f.name)
            if f.type.iso:
                actor.iso_fields.add(f.name)
            if f.mutable:
                actor.mut_fields.add(f.name)
        if decl.fields and decl.new is None:
            raise CompileError(
                f"`{actor.name}` has fields, so it needs a `new` that sets them", decl.pos)

    def actor_signatures(self, actor):
        decl = actor.decl
        if decl.new is not None:
            decl.new.file, decl.new.trusted = decl.file, decl.trusted
            actor.new_sig = self.actor_signature(actor, decl.new, NEW_PARAMS, "`new`")
        for h in decl.handlers:
            h.file, h.trusted = decl.file, decl.trusted
            require_snake(h.name, "handler", h.pos)
            if h.name in {name for name, _ in actor.fields}:
                raise CompileError(f"`{h.name}` is already a field of `{actor.name}`", h.pos)
            if h.name in actor.methods:
                raise CompileError(f"`{actor.name}` already has a handler `{h.name}`", h.pos)
            actor.methods[h.name] = self.actor_signature(
                actor, h, MESSAGE_PARAMS, f"handler `{h.name}`")

    def interface_signatures(self, iface):
        for h in iface.decl.handlers:
            h.file, h.trusted = iface.decl.file, iface.decl.trusted
            require_snake(h.name, "handler", h.pos)
            if h.name in iface.methods:
                raise CompileError(f"`{iface.name}` already has a handler `{h.name}`", h.pos)
            iface.methods[h.name] = self.actor_signature(
                iface, h, MESSAGE_PARAMS, f"handler `{h.name}`")

    def number_handlers(self, prog):
        """A handler's number in a message is chosen by its name, program-wide
        (decision 128): the same name is the same number in every actor and
        interface, so a send through an interface needs no translation."""
        numbers = {}
        for item in prog.items:
            if isinstance(item, (S.ActorDecl, S.InterfaceDecl)):
                for h in item.handlers:
                    h.number = numbers.setdefault(h.name, len(numbers))
        self.handler_numbers = numbers

    def implements(self, actor):
        """`actor Name: Interface` promises every handler of the interface, with the
        same modifier and parameter types."""
        for name, pos in actor.decl.implements:
            iface = self.interfaces.get(name)
            if iface is None:
                if name in self.actors or name in self.structs or name in self.enums:
                    raise CompileError(f"`{name}` is not an interface", pos)
                raise CompileError(f"unknown interface `{name}`", pos)
            self.require_visible(iface.decl, iface.name, pos)
            if iface in actor.implements:
                raise CompileError(f"`{actor.name}` already implements `{name}`", pos)
            for hname, want in iface.methods.items():
                have = actor.methods.get(hname)
                if have is None:
                    raise CompileError(
                        f"`{actor.name}` implements `{name}`, so it needs a handler `{hname}`", pos)
                if have.decl.handler != want.decl.handler:
                    raise CompileError(
                        f"`{name}` declares `{hname}` as `{want.decl.handler}`, so "
                        f"`{actor.name}` must too", have.decl.pos)
                if ([t for t, _ in have.params] != [t for t, _ in want.params]
                        or [(p.type.iso, p.type.val) for p in have.decl.params]
                        != [(p.type.iso, p.type.val) for p in want.decl.params]):
                    raise CompileError(
                        f"`{actor.name}.{hname}` must take the same parameter types as "
                        f"`{name}.{hname}`, each `iso` or `val` the same way", have.decl.pos)
            actor.implements.append(iface)

    def actor_signature(self, actor, decl, limit, what):
        params = []
        for p in decl.params:
            require_snake(p.name, "parameter", p.pos)
            if p.type.mut:
                raise CompileError(
                    "a message can't carry a borrow: a `mut` parameter is lent to a call, "
                    "not sent", p.pos)
            ty = self.resolve_type(p.type)
            if p.type.val and not is_aggregate(ty):
                raise CompileError(
                    f"`val` is for structs and enums; {ty} is already a plain value", p.type.pos)
            if p.type.iso and not is_aggregate(ty):
                raise CompileError(
                    f"`iso` is for structs and enums; {ty} is already a plain value", p.type.pos)
            if what == "`new`" and (p.type.val or p.type.iso):
                params.append((ty, False))          # data handed to the new actor
                continue
            if is_aggregate(ty) and not (p.type.iso or p.type.val):
                raise CompileError(
                    (f"`new` takes a struct or enum as owned or read-only data: write "
                     f"`iso {ty}` or `val {ty}`" if what == "`new`" else
                     f"a message carries a struct or enum owned or read-only: write "
                     f"`iso {ty}` or `val {ty}`"), p.type.pos)
            params.append((ty, False))
        if len(params) > limit:
            raise CompileError(
                f"{what} takes at most {limit} parameters for now", decl.pos)
        if what != "`new`":
            carried = sum(words(ty) for ty, _ in params)
            if carried > MESSAGE_WORDS:
                raise CompileError(
                    f"{what} would carry {carried} words, and a message carries at most "
                    f"{MESSAGE_WORDS} (larger data waits for the pool that comes with `freeze`)",
                    decl.pos)
        return FnSig(decl, params, UNIT, actor)

    def aggregates(self):
        return list(self.structs.values()) + list(self.enums.values())

    def enter_file(self, decl):
        self.file, self.trusted = decl.file, decl.trusted

    def require_visible(self, decl, name, pos):
        """Without `pub`, an item can only be used in its own file (decision 49)."""
        if decl.file != self.file and not decl.public:
            raise CompileError(
                f"`{name}` is private to {os.path.basename(decl.file)}; "
                f"mark it `pub` to use it in another file", pos)

    def declare_unique(self, name, pos):
        if name in BUILTINS:
            raise CompileError(f"`{name}` is built in; choose another name", pos)
        if name in self.variants:
            raise CompileError(
                f"`{name}` is already a variant of `{self.variants[name].enum}`", pos)
        if (name in self.fns or name in self.consts or name in self.structs
                or name in self.enums or name in self.actors or name in self.interfaces):
            raise CompileError(f"`{name}` is already defined", pos)

    def resolve_type(self, ref):
        ty = (NAMED_TYPES.get(ref.name) or self.structs.get(ref.name)
              or self.enums.get(ref.name) or self.actors.get(ref.name)
              or self.interfaces.get(ref.name))
        if is_aggregate(ty) or is_actor(ty):
            self.require_visible(ty.decl, ty.name, ref.pos)
        if ty is None:
            if ref.name in self.variants:
                raise CompileError(
                    f"`{ref.name}` is a variant of `{self.variants[ref.name].enum}`, not a type",
                    ref.pos)
            raise CompileError(f"unknown type `{ref.name}`", ref.pos)
        return ty

    def field_list(self, fields, owner):
        result, seen = [], set()
        for f in fields:
            require_snake(f.name, "field", f.pos)
            if f.name in seen:
                raise CompileError(f"{owner} already has a field `{f.name}`", f.pos)
            seen.add(f.name)
            result.append((f.name, self.resolve_type(f.type)))
        return result

    def size(self, ty, path):
        """Size in words. A struct or enum can't contain itself by value."""
        if not is_aggregate(ty):
            return 1
        if ty.words is not None:
            return ty.words
        if ty in path:
            raise CompileError(
                f"`{ty.name}` contains itself, so it would have no fixed size", ty.decl.pos)
        if is_struct(ty):
            total = sum(self.size(fty, path + [ty]) for _, fty in ty.fields)
        else:
            total = 1 + max(sum(self.size(fty, path + [ty]) for _, fty in v.fields)
                            for v in ty.variants.values())
        ty._words = total
        return total

    def signature(self, decl, owner):
        params = []
        for p in decl.params:
            require_snake(p.name, "parameter", p.pos)
            ty = self.resolve_type(p.type)
            if p.type.iso and not is_aggregate(ty):
                raise CompileError(
                    f"`iso` is for structs and enums; {ty} is already a plain value", p.type.pos)
            params.append((ty, p.type.mut))
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
        self.in_new = is_actor(sig.owner) and decl.name == "new"
        self.assigned = set()
        self.locked = {}
        self.moved, self.break_states, self.loop_entries = {}, [], []
        self.unsafe_depth = 0
        self.enter_file(decl)
        if sig.owner:
            decl.self_slot = self.alloc(1)
        if is_aggregate(sig.ret):
            decl.result_slot = self.alloc(1)
        for param, (ty, is_mut) in zip(decl.params, sig.params):
            if param.name in self.scopes[0]:
                raise CompileError(f"duplicate parameter `{param.name}`", param.pos)
            param.ty = ty
            kind = "ref" if is_mut or is_aggregate(ty) else "value"
            param.slot = self.new_local(param.name, ty, is_mut, kind, pos=param.pos,
                                        owned=param.type.iso, val=param.type.val)
        returns = self.block(decl.body)
        if not block_diverges(decl.body):
            self.require_fields_back()
        if sig.ret != UNIT and not returns:
            raise CompileError(
                f"`{decl.name}` must return a {sig.ret} on every path", decl.pos)
        decl.nslots = self.nslots
        if self.in_new:
            for name, _ in sig.owner.fields:
                if name not in self.assigned:
                    raise CompileError(f"`new` must set every field: `.{name}` is never set",
                                       decl.pos)
        self.in_new = False
        self.receiver = None

    def alloc(self, n):
        slot = self.nslots
        self.nslots += n
        return slot

    def new_local(self, name, ty, mutable, kind="value", pos=None, owned=False, val=False):
        # No shadowing (principle 4): a name means one thing wherever it's visible.
        if pos is not None:
            if self.lookup(name) is not None:
                raise CompileError(
                    f"`{name}` is already declared here; choose a different name", pos)
            if name in self.fns:
                raise CompileError(f"`{name}` is already the name of a function", pos)
            if name in BUILTINS:
                raise CompileError(f"`{name}` is built in; choose another name", pos)
        slot = self.alloc(words(ty) if kind == "region" else 1)
        self.scopes[-1][name] = Local(name, ty, mutable, slot, kind, owned, val)
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
            if is_aggregate(ty):
                self.require_fresh(s.init, f"`{s.name}`")
            kind = "region" if is_aggregate(ty) else "value"
            s.slot = self.new_local(s.name, ty, s.mutable, kind, pos=s.pos)
            return False

        if isinstance(s, S.Assign):
            if self.in_new and isinstance(s.target, S.FieldAccess) and s.target.obj is None:
                return self.assign_in_new(s)
            if isinstance(s.target, S.Name) and self.lookup(s.target.ident) is None:
                if s.target.ident in self.consts:
                    raise CompileError(f"can't assign to constant `{s.target.ident}`", s.target.pos)
                raise CompileError(f"unknown variable `{s.target.ident}`", s.target.pos)
            # Assigning to a consumed place gives it a value again, so it is not a use.
            key = self.moved_key(s.target)
            was = self.moved.pop(key, None) if key is not None else None
            target_ty = self.expr(s.target)
            if was is not None:
                self.moved[key] = was
            self.require_mutable(s.target, s.pos)
            if s.op != "=":
                if not is_int(target_ty):
                    raise CompileError(f"`{s.op}` needs an integer, found {target_ty}", s.pos)
                s.value = S.Binary(s.pos, s.op[:-1], copy.deepcopy(s.target), s.value)
                s.op = "="
            self.expr(s.value, target_ty)
            if is_aggregate(target_ty):
                self.require_fresh(s.value, describe_place(s.target))
            if key is not None:
                self.moved.pop(key, None)
            return False

        if isinstance(s, S.ExprStmt):
            if s.discard:
                if self.expr(s.expr) == UNIT:
                    raise CompileError("nothing to discard: the call returns no value", s.expr.pos)
                return False
            if not isinstance(s.expr, (S.Call, S.MethodCall)):
                raise CompileError("this expression's value is unused", s.pos)
            if isinstance(s.expr, S.Call) and s.expr.callee == "panic":
                self.expr(s.expr)
                return True                         # a panic never returns
            if self.expr(s.expr) != UNIT:
                name = s.expr.callee if isinstance(s.expr, S.Call) else s.expr.name
                raise CompileError(
                    f"the result of `{name}` is unused; "
                    f"write `discard {name}(...)` to ignore it on purpose", s.pos)
            return False

        if isinstance(s, S.If):
            self.expr(s.cond, BOOL)
            before = dict(self.moved)
            then_returns = self.block(s.then)
            ends = [self.moved] if not block_diverges(s.then) else []
            self.moved = dict(before)
            if s.else_ is None:
                ends.append(self.moved)
                self.moved = merge_moved(ends, before)
                return False
            if isinstance(s.else_, S.If):
                else_returns = self.stmt(s.else_)
            else:
                else_returns = self.block(s.else_)
            if not diverges(s.else_):
                ends.append(self.moved)
            self.moved = merge_moved(ends, before)
            return then_returns and else_returns

        if isinstance(s, S.Loop):
            require_snake(s.label, "loop label", s.pos)
            if s.cond is not None:
                self.expr(s.cond, BOOL)
            self.loops.append(s.label)
            self.enter_loop()
            self.block(s.body)
            self.leave_loop(s.body)
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
            self.enter_loop()
            self.block(s.body)
            self.leave_loop(s.body)
            self.loops.pop()
            self.scopes.pop()
            return False

        if isinstance(s, S.Match):
            self.match_patterns(s)
            always_returns = True
            before, ends = dict(self.moved), []
            for arm in s.arms:
                self.moved = dict(before)
                unlock = self.enter_arm(s, arm)
                if isinstance(arm.body, S.Block):
                    returns = self.block(arm.body)
                else:
                    returns = self.stmt(arm.body)
                self.exit_arm(unlock)
                if not diverges(arm.body):
                    ends.append(self.moved)
                always_returns = always_returns and returns
            self.moved = merge_moved(ends, before)
            # Every value is matched by some arm, so if each arm returns, so does the match.
            return always_returns

        if isinstance(s, S.Unsafe):
            if not self.trusted:
                raise CompileError(
                    "`unsafe` is only allowed in files under a trusted directory, "
                    "such as raven/trusted/", s.pos)
            if self.unsafe_depth:
                raise CompileError("this is already inside `unsafe { }`", s.pos)
            self.unsafe_depth += 1
            returns = self.block(s.body)
            self.unsafe_depth -= 1
            return returns

        if isinstance(s, (S.Break, S.Continue)):
            if s.label not in self.loops:
                word = "break" if isinstance(s, S.Break) else "continue"
                if not self.loops:
                    raise CompileError(f"`{word}` outside a loop", s.pos)
                raise CompileError(f"`{word} {s.label}`: no enclosing loop is named `{s.label}`",
                                   s.pos)
            at = self.loops.index(s.label)
            if isinstance(s, S.Break):
                self.break_states[at].append(dict(self.moved))
            else:
                self.require_loop_ready(at)
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
                if is_aggregate(self.fn.ret):
                    local = getattr(s.value, "local", None) if isinstance(s.value, S.Name) else None
                    owned_local = local is not None and (local.kind == "region" or local.owned)
                    if not owned_local:
                        self.require_fresh(s.value, "the result")
            self.require_fields_back()
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
            if (is_actor(expected) and expected.is_interface and is_actor(ty)
                    and not ty.is_interface):
                raise CompileError(
                    f"expected {expected}, found {ty}: hand out only what the interface allows "
                    f"with `narrow(..., {expected})`", e.pos)
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

        if isinstance(e, S.StrLit):
            return self.bytes_type(e.pos)

        if isinstance(e, S.Name):
            local = self.lookup(e.ident) if self.scopes else None
            if local is not None:
                self.require_not_consumed(local, e.ident, e.pos)
                e.slot = local.slot
                e.local = local
                return local.ty
            if e.ident in self.consts:
                self.require_visible(self.const_decls[e.ident], e.ident, e.pos)
                ty, value = self.consts[e.ident]
                if isinstance(value, S.StrLit):
                    e.const_str = value
                else:
                    e.const_value = value
                return ty
            if e.ident in self.variants:
                v = self.variants[e.ident]
                self.require_visible(v.enum.decl, v.enum.name, e.pos)
                if v.fields:
                    raise CompileError(
                        f"`{v.name}` has fields: write `{v.name}("
                        f"{', '.join(name + ': ...' for name, _ in v.fields)})`", e.pos)
                if self.fn is None:
                    raise CompileError("constants must be numbers or bools", e.pos)
                e.variant = v
                e.temp = self.alloc(v.enum.words)
                return v.enum
            if e.ident in self.fns:
                raise CompileError(f"`{e.ident}` is a function; call it with `{e.ident}(...)`", e.pos)
            if e.ident in self.structs or e.ident in self.enums or e.ident in self.actors:
                raise CompileError(f"`{e.ident}` is a type, not a value", e.pos)
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

        if isinstance(e, S.Call) and e.callee in BUILTINS:
            return self.builtin(e)

        if isinstance(e, S.Call):
            sig = self.fns.get(e.callee)
            if sig is None:
                if self.lookup(e.callee) if self.scopes else None:
                    raise CompileError(f"`{e.callee}` is a variable, not a function", e.pos)
                raise CompileError(f"unknown function `{e.callee}`", e.pos)
            if self.fn is None:
                raise CompileError("constants can't call functions", e.pos)
            self.require_visible(sig.decl, e.callee, e.pos)
            self.check_args(f"`{e.callee}`", sig.params, e.args, e.pos, sig=sig)
            if is_aggregate(sig.ret):
                e.temp = self.alloc(sig.ret.words)
            return sig.ret

        if isinstance(e, S.StructLit):
            v = self.variants.get(e.name)
            st = v.enum if v is not None else self.structs.get(e.name)
            if st is not None:
                self.require_visible(st.decl, st.name, e.pos)
                if v is None and st.decl.file != self.file and len(st.public_fields) < len(st.fields):
                    raise CompileError(
                        f"`{st.name}` has private fields, so it can only be built in "
                        f"{os.path.basename(st.decl.file)}", e.pos)
            if st is None:
                if e.name in self.enums:
                    first = next(iter(self.enums[e.name].variants))
                    raise CompileError(
                        f"`{e.name}` is an enum: build a value with one of its variants, "
                        f"such as `{first}`", e.pos)
                raise CompileError(f"unknown type `{e.name}`", e.pos)
            if self.fn is None:
                raise CompileError("constants must be numbers or bools", e.pos)
            fields = v.fields if v is not None else st.fields
            if v is not None and not fields:
                raise CompileError(f"`{e.name}` has no fields: write `{e.name}`", e.pos)
            self.named_fields(e.name, fields, [(n, p) for n, _, p in e.fields], e.pos)
            for (name, value, _), (_, fty) in zip(e.fields, fields):
                self.expr(value, fty)
                if is_aggregate(fty):
                    self.require_fresh(value, f"field `{name}`")
            e.variant = v
            e.temp = self.alloc(st.words)
            return st

        if isinstance(e, S.FieldAccess):
            st = self.struct_of(e)
            if is_actor(st):
                if e.obj is not None:
                    raise CompileError(
                        f"an actor's fields are private: send `{st.name}` a message instead",
                        e.pos)
                if not any(name == e.field for name, _ in st.fields):
                    raise CompileError(f"`{st.name}` has no field `{e.field}`", e.pos)
                if self.in_new and e.field not in self.assigned:
                    raise CompileError(f"`.{e.field}` is read before `new` sets it", e.pos)
                self.require_not_consumed("." + e.field, "." + e.field, e.pos)
                e.struct = st
                return st.offset(e.field)[1]
            if is_enum(st):
                raise CompileError(
                    f"`{st.name}` is an enum: read its fields with `match`", e.pos)
            e.struct = st
            if not any(name == e.field for name, _ in st.fields):
                hint = " (it's a method: call it with `(...)`)" if e.field in st.methods else ""
                raise CompileError(f"`{st.name}` has no field `{e.field}`{hint}", e.pos)
            if st.decl.file != self.file and e.field not in st.public_fields:
                raise CompileError(
                    f"`{e.field}` is private to {os.path.basename(st.decl.file)}; "
                    f"mark the field `pub` to use it in another file", e.pos)
            return st.offset(e.field)[1]

        if isinstance(e, S.MethodCall):
            st = self.struct_of(e)
            if is_actor(st):
                return self.send(e, st)
            sig = st.methods.get(e.name)
            if sig is None:
                raise CompileError(f"`{st.name}` has no method `{e.name}`", e.pos)
            if st.decl.file != self.file and not sig.decl.public:
                raise CompileError(
                    f"`{e.name}` is private to {os.path.basename(st.decl.file)}; "
                    f"mark the method `pub` to use it in another file", e.pos)
            e.sig = sig
            if sig.mutating:
                receiver = e.obj if e.obj is not None else None
                if receiver is None:
                    if not self.receiver[1]:
                        raise CompileError(
                            f"`.{e.name}()` changes its value, so it can only be called from "
                            f"a `mut fn`", e.pos)
                    self.require_unlocked("self", f"call `mut fn {e.name}` on", ".", e.pos)
                else:
                    self.require_mutable(receiver, e.pos, f"call `mut fn {e.name}` on")
            # `.m()` inside a method acts on the method's own struct ("self").
            receiver = e.obj if e.obj is not None else S.FieldAccess(e.pos, None, "")
            self.check_args(f"`{st.name}.{e.name}`", sig.params, e.args, e.pos,
                            receiver=receiver, receiver_mut=sig.mutating, sig=sig)
            if is_aggregate(sig.ret):
                e.temp = self.alloc(sig.ret.words)
            return sig.ret

        if isinstance(e, S.Consume):
            return self.consume(e)

        if isinstance(e, S.Copy):
            ty = self.expr(e.value)
            if not is_aggregate(ty):
                raise CompileError(
                    f"{ty} values are always copied; `copy(...)` is for structs and enums",
                    e.value.pos)
            e.temp = self.alloc(ty.words)
            return ty

        if isinstance(e, S.SelfValue):
            if self.receiver is None:
                raise CompileError(
                    "`.` on its own is a method's own value, so it only works inside methods",
                    e.pos)
            if is_actor(self.receiver[0]):
                raise CompileError(
                    "`.` on its own is a struct's own value: an actor refers to itself "
                    "as `self`", e.pos)
            return self.receiver[0]

        if isinstance(e, S.SelfRef):
            if self.receiver is None or not is_actor(self.receiver[0]):
                raise CompileError("`self` is the actor itself, so it only works inside an actor",
                                   e.pos)
            if self.in_new and len(self.assigned) < len(self.receiver[0].fields):
                raise CompileError(
                    "`self` can't be used in `new` until every field is set", e.pos)
            return self.receiver[0]

        if isinstance(e, S.Narrow):
            if self.fn is None:
                raise CompileError("constants can't narrow references", e.pos)
            iface = self.interfaces.get(e.interface)
            if iface is None:
                if e.interface in self.actors or e.interface in self.structs \
                        or e.interface in self.enums:
                    raise CompileError(f"`{e.interface}` is not an interface", e.interface_pos)
                raise CompileError(f"unknown interface `{e.interface}`", e.interface_pos)
            self.require_visible(iface.decl, iface.name, e.interface_pos)
            src = self.expr(e.value)
            if not is_actor(src):
                raise CompileError(f"`narrow` works on actor references, not {src}", e.value.pos)
            if src.is_interface:
                raise CompileError(
                    f"this is already a `{src}` reference: it can't be narrowed again", e.value.pos)
            if iface not in src.implements:
                raise CompileError(
                    f"`{src.name}` doesn't implement `{iface.name}`: declare it as "
                    f"`actor {src.name}: {iface.name}`", e.value.pos)
            return iface

        if isinstance(e, S.Spawn):
            if self.fn is None:
                raise CompileError("constants can't spawn actors", e.pos)
            actor = self.actors.get(e.name)
            if actor is None and e.name in self.interfaces:
                raise CompileError(
                    f"`{e.name}` is an interface: `spawn` creates actors that implement it", e.pos)
            if actor is None:
                if e.name in self.structs or e.name in self.enums:
                    raise CompileError(f"`{e.name}` is not an actor: `spawn` creates actors", e.pos)
                raise CompileError(f"unknown actor `{e.name}`", e.pos)
            self.require_visible(actor.decl, actor.name, e.pos)
            sig = actor.new_sig
            self.check_args(f"`spawn {e.name}`", sig.params if sig else [], e.args, e.pos,
                            sig=sig)
            e.actor, e.sig = actor, sig
            e.temp = self.alloc(1)
            return actor

        if isinstance(e, S.MatchExpr):
            if self.fn is None:
                raise CompileError("constants can't use `match`", e.pos)
            self.match_patterns(e)
            if not any(isinstance(arm.body, S.Expr) for arm in e.arms):
                raise CompileError("every arm leaves, so this `match` has no value", e.pos)
            # Arms that aren't bare literals first, so the literals can take their type.
            ordered = sorted(e.arms, key=lambda arm: untyped_literal(arm.body))
            ty = None
            for arm in ordered:
                unlock = self.enter_arm(e, arm)
                if isinstance(arm.body, S.Expr):
                    aty = self.expr(arm.body, hint=ty if ty is not None else expected)
                    if ty is None:
                        ty = aty
                    elif aty != ty:
                        raise CompileError(
                            f"every arm of a `match` must have the same type, found {ty} and {aty}",
                            arm.body.pos)
                else:
                    self.stmt(arm.body)             # return, break or continue
                self.exit_arm(unlock)
            return ty

        if isinstance(e, S.MutArg):
            raise CompileError("`mut` only marks an argument passed to a `mut` parameter", e.pos)

        raise AssertionError(f"unknown expression {e!r}")

    # ---- built-ins and text --------------------------------------------

    def builtin(self, e):
        name = e.callee
        if self.fn is None:
            raise CompileError("constants can't call functions", e.pos)
        for arg in e.args:
            if isinstance(arg, S.MutArg):
                raise CompileError(f"`{name}` doesn't change its arguments, so they aren't "
                                   f"marked `mut`", arg.pos)
        if name == "panic":
            if len(e.args) != 1:
                raise CompileError("`panic` takes one argument: a message", e.pos)
            self.expr(e.args[0], self.bytes_type(e.pos))
            return UNIT
        if self.unsafe_depth == 0:
            where = "inside `unsafe { }`" if self.trusted else \
                "in files under a trusted directory, inside `unsafe { }`"
            raise CompileError(
                f"`{name}` touches memory the compiler can't check, so it is only allowed {where}",
                e.pos)
        if name == "actor_address":
            if len(e.args) != 1:
                raise CompileError("`actor_address` takes one argument: a reference", e.pos)
            ty = self.expr(e.args[0])
            if not is_actor(ty):
                raise CompileError(
                    f"`actor_address` takes an actor or interface reference, not {ty}",
                    e.args[0].pos)
            return U64
        if name == "handler_number":
            if len(e.args) != 1 or not isinstance(e.args[0], S.StrLit):
                raise CompileError(
                    "`handler_number` takes one argument: a handler's name in quotes", e.pos)
            handler = e.args[0].data.decode()
            if handler not in self.handler_numbers:
                raise CompileError(f"no actor or interface has a handler `{handler}`",
                                   e.args[0].pos)
            e.number = self.handler_numbers[handler]
            e.args[0].ty = self.bytes_type(e.pos)
            return U64
        if name in LOADS:
            if len(e.args) != 1:
                raise CompileError(f"`{name}` takes one argument: an address", e.pos)
            self.expr(e.args[0], U64)
            return LOADS[name]
        if len(e.args) != 2:
            raise CompileError(f"`{name}` takes two arguments: an address and a value", e.pos)
        self.expr(e.args[0], U64)
        self.expr(e.args[1], STORES[name])
        return UNIT

    def bytes_type(self, pos):
        """The type of text in quotes: the program's `Bytes` struct."""
        st = self.structs.get("Bytes")
        if st is None:
            raise CompileError(
                "text in quotes is a `Bytes`, which this program doesn't include "
                "(it is declared in raven/trusted/bytes.rook)", pos)
        if st.fields != [("address", U64), ("length", U64)]:
            raise CompileError(
                "`Bytes` must have exactly the fields `address: u64` and `length: u64`, "
                "in that order", st.decl.pos)
        return st

    # ---- match ---------------------------------------------------------

    def match_patterns(self, m):
        """Check the patterns (not the bodies) of a match, and that every value
        of the subject's type is matched exactly once."""
        ty = self.expr(m.subject)
        m.subject_slot = self.alloc(1)
        if is_enum(ty):
            self.enum_patterns(m, ty)
        elif is_int(ty) or ty == BOOL:
            self.value_patterns(m, ty)
        else:
            if ty == UNIT:
                raise CompileError(
                    "there is nothing to match: this call returns no value", m.subject.pos)
            raise CompileError(
                f"can't match a {ty}: `match` works on enums, integers and bools", m.subject.pos)

    def enum_patterns(self, m, ty):
        seen = set()
        for arm in m.arms:
            if arm.patterns is None:
                raise CompileError(
                    f"a `match` on an enum lists every variant of `{ty}`; `else` isn't allowed",
                    arm.pos)
            for p in arm.patterns:
                if p.value is not None:
                    if not (p.end is None and isinstance(p.value, S.Name)):
                        raise CompileError(f"expected a variant of `{ty}`", p.pos)
                    p.name = p.value.ident
                v = self.variants.get(p.name)
                if v is None:
                    raise CompileError(f"`{ty}` has no variant `{p.name}`", p.pos)
                self.require_visible(v.enum.decl, v.enum.name, p.pos)
                if v.enum is not ty:
                    raise CompileError(f"`{p.name}` is a variant of `{v.enum}`, not `{ty}`", p.pos)
                if p.name in seen:
                    raise CompileError(f"`{p.name}` is already matched", p.pos)
                seen.add(p.name)
                p.variant = v
                if p.value is not None:             # written without brackets
                    if v.fields:
                        raise CompileError(
                            f"`{v.name}` has fields: bind each one, as in `{v.name}("
                            f"{', '.join(name + ': _' for name, _ in v.fields)})`", p.pos)
                    p.bindings = []
                elif not v.fields:
                    raise CompileError(f"`{v.name}` has no fields: write `{v.name}`", p.pos)
                else:
                    self.named_fields(v.name, v.fields, [(f, pos) for f, _, pos in p.bindings],
                                      p.pos)
            if len(arm.patterns) > 1 and any(name != "_" for p in arm.patterns
                                             for _, name, _ in p.bindings):
                raise CompileError(
                    "an arm with several variants can't bind names: use `_`, "
                    "or give each variant its own arm", arm.pos)
        missing = [name for name in ty.variants if name not in seen]
        if missing:
            more = f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""
            raise CompileError(f"this `match` is missing `{missing[0]}`{more}", m.pos)

    def value_patterns(self, m, ty):
        lowest, highest = (0, 1) if ty == BOOL else (ty.min, ty.max)
        taken = []                                  # (lo, hi): lo <= value < hi
        else_arm = None
        for i, arm in enumerate(m.arms):
            if arm.patterns is None:
                if i != len(m.arms) - 1:
                    raise CompileError("`else` must be the last arm", arm.pos)
                else_arm = arm
                continue
            for p in arm.patterns:
                if p.value is None:
                    raise CompileError(
                        f"`{p.name}` is an enum variant, but this `match` is on {ty}", p.pos)
                p.lo = self.pattern_value(p.value, ty)
                if p.end is None:
                    p.hi = p.lo + 1
                else:
                    if ty == BOOL:
                        raise CompileError("a range needs integers", p.pos)
                    p.hi = self.pattern_value(p.end, ty)
                    if p.hi <= p.lo:
                        raise CompileError(
                            "this range is empty: its end must be greater than its start", p.pos)
                for lo, hi in taken:
                    if p.lo < hi and lo < p.hi:
                        raise CompileError("this pattern overlaps an earlier one", p.pos)
                taken.append((p.lo, p.hi))
        # The first value no arm matches, if any.
        gap = lowest
        for lo, hi in sorted(taken):
            if lo > gap:
                break
            gap = max(gap, hi)
        if gap > highest:
            if else_arm is not None:
                raise CompileError(
                    f"every {ty} value is already matched, so `else` would never run",
                    else_arm.pos)
        elif else_arm is None:
            if ty == BOOL:
                raise CompileError(f"this `match` is missing `{'true' if gap else 'false'}`",
                                   m.pos)
            raise CompileError(
                f"this `match` doesn't cover every {ty} value (such as {gap}); "
                f"add `else =>` for the rest", m.pos)

    def pattern_value(self, e, ty):
        """A pattern's value: a literal or a constant, computed now."""
        if isinstance(e, S.Name) and self.scopes and self.lookup(e.ident) is not None:
            raise CompileError(
                f"a pattern is a literal or a constant, and `{e.ident}` is a variable", e.pos)
        self.expr(e, ty)
        return self.const_eval(e)

    def enter_arm(self, m, arm):
        """Declare an arm's bindings in a new scope. Returns the variable to
        unlock when the arm ends, if this arm locked one."""
        self.scopes.append({})
        lends = []
        for p in arm.patterns or []:
            if p.variant is None:
                continue
            for field_name, name, pos in p.bindings:
                if name == "_":
                    continue
                require_snake(name, "variable", pos)
                offset, fty = p.variant.offset(field_name)
                agg = is_aggregate(fty)
                slot = self.new_local(name, fty, False, "ref" if agg else "value", pos=pos)
                p.slots.append((slot, offset, agg))
                if agg:
                    lends.append(name)
        root = root_name(m.subject)
        if lends and root is not None and root not in self.locked:
            self.locked[root] = lends[0]
            return root
        return None

    def exit_arm(self, unlock):
        self.scopes.pop()
        if unlock is not None:
            del self.locked[unlock]

    # ---- structs, places and lending ----------------------------------

    def send(self, e, actor):
        """`ref.handler(args)`: a message to an actor. A send to a `lossy` handler
        returns nothing; a send to a `reliable` one returns Raven's `Sent`."""
        if e.obj is None:
            raise CompileError(
                f"to send a message to yourself, write `self.{e.name}(...)`", e.pos)
        sig = actor.methods.get(e.name)
        if sig is None:
            raise CompileError(f"`{actor.name}` has no handler `{e.name}`", e.pos)
        if actor.decl.file != self.file and not sig.decl.public:
            raise CompileError(
                f"`{e.name}` is private to {os.path.basename(actor.decl.file)}; "
                f"mark the handler `pub` to send it from another file", e.pos)
        e.sig, e.send = sig, True
        e.handler_index = sig.decl.number
        self.check_args(f"`{actor.name}.{e.name}`", sig.params, e.args, e.pos, sig=sig)
        e.payload_words = sum(words(ty) for ty, _ in sig.params)
        e.payload = self.alloc(max(1, e.payload_words))
        if sig.decl.handler != "reliable":
            return UNIT
        sent = self.enums.get("Sent")
        if sent is None:
            raise CompileError(
                "a send to a `reliable` handler returns Raven's enum `Sent`, "
                "which this program doesn't declare", e.pos)
        e.temp = self.alloc(sent.words)
        return sent

    def named_fields(self, owner, fields, given, pos):
        """`given` [(name, pos)] must name every field once, in declared order."""
        wanted = [name for name, _ in fields]
        for name, at in given:
            if name not in wanted:
                raise CompileError(f"`{owner}` has no field `{name}`", at)
        names = [name for name, _ in given]
        if names != wanted:
            missing = [n for n in wanted if n not in names]
            if missing:
                raise CompileError(f"`{owner}(...)` is missing `{missing[0]}`", pos)
            raise CompileError(
                f"write the fields of `{owner}` once each, in the order they are declared: "
                f"{', '.join(wanted)}", pos)

    def require_unlocked(self, root, action, text, pos):
        if root in self.locked:
            raise CompileError(
                f"can't {action} `{text}` while `{self.locked[root]}` is lent from it "
                f"by this `match`", pos)

    def struct_of(self, e):
        """The struct type that `e.field` or `e.method()` acts on."""
        if e.obj is None:
            if self.receiver is None:
                what = e.field if isinstance(e, S.FieldAccess) else e.name
                raise CompileError(f"`.{what}` only works inside a struct's methods", e.pos)
            return self.receiver[0]
        ty = self.expr(e.obj)
        if is_actor(ty):
            return ty
        if not is_aggregate(ty):
            what = e.field if isinstance(e, S.FieldAccess) else e.name
            raise CompileError(f"{ty} has no field or method `{what}`", e.pos)
        return ty

    def check_args(self, callee, params, args, pos, receiver=None, receiver_mut=False,
                   sig=None):
        if len(args) != len(params):
            raise CompileError(f"{callee} takes {len(params)} argument(s), but {len(args)} given",
                               pos)
        lent = []                       # (root name, is_mut, pos) of structs lent by reference
        if receiver is not None and root_name(receiver) is not None:
            lent.append((root_name(receiver), receiver_mut, receiver.pos))
        owned = [p.type.iso for p in sig.decl.params] if sig is not None else []
        owned += [False] * (len(params) - len(owned))
        shared = [p.type.val and sig.decl.handler is not None for p in sig.decl.params] \
            if sig is not None else []
        shared += [False] * (len(params) - len(shared))
        for arg, (pty, is_mut), takes, readonly in zip(args, params, owned, shared):
            if readonly:
                self.expr(arg, pty)
                if not self.is_val_source(arg):
                    raise CompileError(
                        f"{callee} takes read-only data: pass a `val` field, a `val` "
                        f"parameter or text in quotes (a value from `freeze` arrives in step 4c)",
                        arg.pos)
                continue
            if isinstance(arg, S.Consume) and not takes:
                raise CompileError(
                    f"{callee} only borrows this argument, so there is nothing to give away: "
                    f"pass it as it is", arg.pos)
            if takes:
                self.expr(arg, pty)
                self.require_fresh(arg, f"giving it to {callee}")
                continue
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
                if is_aggregate(pty) and root_name(arg) is not None:
                    lent.append((root_name(arg), False, arg.pos))
        # A value lent as `mut` can't be lent again in the same call. Numbers
        # are copied when passed, so they can't alias.
        for i, (name, is_mut, at) in enumerate(lent):
            for other, other_mut, _ in lent[:i]:
                if name == other and (is_mut or other_mut):
                    raise CompileError(
                        f"`{name}` is lent as `mut` in this call, so it can't be lent again here",
                        at)

    def assign_in_new(self, s):
        """`.field = value` in an actor's `new`: every field is set once, at the
        top level of the body, so none is left unset or read before it is set."""
        actor = self.receiver[0]
        name = s.target.field
        if not any(n == name for n, _ in actor.fields):
            raise CompileError(f"`{actor.name}` has no field `{name}`", s.target.pos)
        if s.op != "=":
            raise CompileError("`new` sets each field once, with `=`", s.pos)
        if name in self.assigned:
            raise CompileError(f"`.{name}` is already set: `new` sets each field once", s.pos)
        if len(self.scopes) != 2:
            raise CompileError(
                "`new` sets each field at the top level of its body, not inside an `if`, "
                "a loop or a `match`", s.pos)
        fty = actor.offset(name)[1]
        s.target.struct = actor
        s.target.ty = fty
        self.expr(s.value, fty)
        if name in actor.iso_fields:
            self.require_fresh(s.value, f"`.{name}`")
        self.assigned.add(name)
        return False

    def require_mutable(self, place, pos, action="assign to"):
        root = root_name(place)
        if root is not None:
            self.require_unlocked(root, action, source_text(place), pos)
        if isinstance(place, S.SelfValue):
            if not self.receiver[1]:
                raise CompileError(
                    f"can't {action} `.`: only a `mut fn` can change its own value", pos)
            return
        if isinstance(place, S.Name):
            local = place.local if hasattr(place, "local") else None
            if local is None:
                raise CompileError(f"can't {action} `{place.ident}`", pos)
            if not local.mutable:
                if local.val:
                    raise CompileError(
                        f"can't {action} `{place.ident}`: it is `val`, read-only data", pos)
                if local.owned:
                    raise CompileError(
                        f"can't {action} `{place.ident}`: an `iso` parameter is read-only; "
                        f"copy it into a `mut` variable to change it", pos)
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
                if is_actor(self.receiver[0]):
                    if place.field not in self.receiver[0].mut_fields:
                        why = ("it is `val`, read-only data" if place.field in
                               self.receiver[0].val_fields else "the field isn't `mut`")
                        raise CompileError(f"can't {action} `.{place.field}`: {why}", pos)
                    return
                if not self.receiver[1]:
                    raise CompileError(
                        f"can't {action} `.{place.field}`: only a `mut fn` can change its struct",
                        pos)
                return
            self.require_mutable(place.obj, pos, action)
            return
        raise CompileError(f"can only {action} a variable or a field", pos)

    # ---- consuming and moved places ------------------------------------

    def moved_key(self, place):
        """What `consume` would record for `place`: its Local, or ".field" for an
        actor's field; None for anything else."""
        if isinstance(place, S.Name) and self.scopes:
            return self.lookup(place.ident)
        if (isinstance(place, S.FieldAccess) and place.obj is None and self.receiver is not None
                and is_actor(self.receiver[0])):
            return "." + place.field
        return None

    def require_not_consumed(self, key, name, pos):
        at = self.moved.get(key)
        if at is not None:
            raise CompileError(
                f"`{name}` was consumed at {at.line}:{at.col}, so it has no value here; "
                f"assign it again first", pos)

    def require_fields_back(self):
        """An actor's `iso` field a handler consumed must have a value again before
        the handler ends."""
        for key, at in self.moved.items():
            if isinstance(key, str):
                raise CompileError(
                    f"`{key}` is consumed here and not assigned again before the handler "
                    f"ends", at)

    def enter_loop(self):
        outside = {local for scope in self.scopes for local in scope.values()}
        self.loop_entries.append((dict(self.moved), outside))
        self.break_states.append([])

    def require_loop_ready(self, depth):
        """What the next turn of loop number `depth` would use must still have its value."""
        before, outside = self.loop_entries[depth]
        for key, at in self.moved.items():
            if key in before:
                continue
            if isinstance(key, str) or key in outside:
                name = key if isinstance(key, str) else key.name
                raise CompileError(
                    f"`{name}` is consumed here, inside a loop, but the next turn needs it: "
                    f"assign it again before the end of the loop", at)

    def leave_loop(self, body):
        before, _ = self.loop_entries[-1]
        if not block_diverges(body):
            self.require_loop_ready(len(self.loop_entries) - 1)
        breaks = self.break_states.pop()
        self.loop_entries.pop()
        self.moved = merge_moved(breaks + [before], before)

    def consume(self, e):
        if self.fn is None:
            raise CompileError("constants can't consume", e.pos)
        place = e.value
        if self.in_new:
            raise CompileError("`new` gives every field its first value; there is nothing "
                               "to consume yet", e.pos)
        if isinstance(place, S.Name) and self.scopes and self.lookup(place.ident) is not None:
            local = self.lookup(place.ident)
            if not is_aggregate(local.ty):
                raise CompileError(
                    f"{local.ty} values are always copied; `consume(...)` is for structs "
                    f"and enums", place.pos)
            if local.kind != "region" and not local.owned:
                raise CompileError(
                    f"`{place.ident}` is only lent to this function, so it isn't yours to "
                    f"consume; the parameter would have to be `iso`", place.pos)
        elif (isinstance(place, S.FieldAccess) and place.obj is None and self.receiver is not None
              and is_actor(self.receiver[0])):
            actor = self.receiver[0]
            if not any(n == place.field for n, _ in actor.fields):
                raise CompileError(f"`{actor.name}` has no field `{place.field}`", place.pos)
            if place.field in actor.val_fields:
                raise CompileError(f"`.{place.field}` is `val`: read-only data is never "
                                   f"consumed", place.pos)
            if not is_aggregate(actor.offset(place.field)[1]):
                raise CompileError(
                    f"{actor.offset(place.field)[1]} values are always copied; "
                    f"`consume(...)` is for structs and enums", place.pos)
            if place.field not in actor.mut_fields:
                raise CompileError(
                    f"can't consume `.{place.field}`: the field isn't `mut`", place.pos)
        else:
            raise CompileError(
                "`consume` gives away a whole variable or an actor's `mut iso` field, "
                "nothing else", e.pos)
        ty = self.expr(place)
        self.require_unlocked(root_name(place), "consume", source_text(place), e.pos)
        self.moved[self.moved_key(place)] = e.pos
        return ty

    def is_val_source(self, e):
        """Is `e` read-only data that a `val` parameter may be given: text in quotes, a
        `val` parameter, or a `val` field of the actor?"""
        if isinstance(e, S.StrLit):
            return True
        if isinstance(e, S.Name):
            if e.const_str is not None:
                return True
            local = self.lookup(e.ident) if self.scopes else None
            return local is not None and local.val
        if (isinstance(e, S.FieldAccess) and e.obj is None and self.receiver is not None
                and is_actor(self.receiver[0])):
            return e.field in self.receiver[0].val_fields
        return False

    def movable(self, e):
        """Could `consume(e)` be written? An owned variable, or a `mut iso` field."""
        if isinstance(e, S.Name) and self.scopes:
            local = self.lookup(e.ident)
            return local is not None and is_aggregate(local.ty) and (
                local.kind == "region" or local.owned)
        if (isinstance(e, S.FieldAccess) and e.obj is None and self.receiver is not None
                and is_actor(self.receiver[0])):
            actor = self.receiver[0]
            return e.field in actor.iso_fields and e.field in actor.mut_fields
        return False

    def require_fresh(self, e, what):
        """A struct value can be stored only if nothing else holds it."""
        if not is_fresh(e):
            name = source_text(e)
            movable = self.movable(e)
            raise CompileError(
                f"{what} would be a copy of {describe_place(e)}; write `copy({name})` "
                f"to copy it" + (f", or `consume({name})` to give it away" if movable else ""),
                e.pos)

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
            if is_enum(lty):
                raise CompileError(f"`{op}` doesn't compare enums; use `match`, or write a "
                                   f"method such as `equals` in `{lty}`", e.pos)
            if is_aggregate(lty):
                # Decision 74: a struct can only do what its declaration shows.
                raise CompileError(f"`{op}` doesn't compare structs; write a method such as "
                                   f"`equals` in `{lty}`", e.pos)
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


def is_fresh(e):
    """Is `e` a new struct or enum value that nothing else holds?"""
    if isinstance(e, (S.StructLit, S.Call, S.MethodCall, S.Copy, S.Consume, S.StrLit)):
        return True
    if isinstance(e, S.Name):
        return e.variant is not None or e.const_str is not None     # `Idle`, `HELLO`
    if isinstance(e, S.IfExpr):
        return is_fresh(e.then) and is_fresh(e.else_)
    if isinstance(e, S.MatchExpr):
        return all(is_fresh(arm.body) for arm in e.arms if isinstance(arm.body, S.Expr))
    return False


def diverges(st):
    """Does control never continue past statement `st` (it returns, breaks, continues
    or panics)?"""
    if isinstance(st, (S.Return, S.Break, S.Continue)):
        return True
    if isinstance(st, S.ExprStmt):
        return isinstance(st.expr, S.Call) and st.expr.callee == "panic"
    if isinstance(st, S.Block):
        return block_diverges(st)
    if isinstance(st, S.If):
        return (st.else_ is not None and block_diverges(st.then)
                and diverges(st.else_))
    if isinstance(st, S.Match):
        return all(diverges(arm.body) for arm in st.arms)
    if isinstance(st, S.Unsafe):
        return block_diverges(st.body)
    return False


def block_diverges(block):
    return any(diverges(st) for st in block.stmts)


def merge_moved(states, before):
    """The consumed places after paths join: consumed on any of them. With no path
    left (all returned or broke out), nothing after matters, so `before` stands."""
    if not states:
        return dict(before)
    merged = {}
    for state in states:
        merged.update(state)
    return merged


def root_name(e):
    """The variable a place starts from (`p` in `p.a.b`), or "self" for `.x` and `.`."""
    while isinstance(e, S.FieldAccess):
        if e.obj is None:
            return "self"
        e = e.obj
    if isinstance(e, S.SelfValue):
        return "self"
    if isinstance(e, S.Name) and e.variant is None and e.const_str is None:
        return e.ident
    return None


def source_text(e):
    if isinstance(e, S.Name):
        return e.ident
    if isinstance(e, S.FieldAccess):
        return f"{source_text(e.obj) if e.obj is not None else ''}.{e.field}"
    if isinstance(e, S.SelfValue):
        return "."
    return "value"


def describe_place(e):
    if isinstance(e, (S.Name, S.FieldAccess, S.SelfValue)):
        return f"`{source_text(e)}`"
    return "this value"


def breaks_to(node, label):
    """Does anything inside `node`, at any depth (including a `match` used as
    a value), `break label`?"""
    if isinstance(node, S.Break):
        return node.label == label
    if isinstance(node, list):
        return any(breaks_to(item, label) for item in node)
    if isinstance(node, tuple):
        return any(breaks_to(item, label) for item in node)
    if hasattr(node, "__dataclass_fields__"):
        return any(breaks_to(getattr(node, f), label) for f in node.__dataclass_fields__)
    return False
