"""Generics: a separate copy of a struct, enum, interface or actor for each list of type arguments.

`enum Option<T> { Some(value: T)  None }` is a template. Every `Option<u64>` written in the
program makes a real enum named `Option<u64>` (its `generic_base` is `Option`), with `T`
replaced by `u64` in its fields and in its methods' bodies; that copy is then checked and
compiled like any other enum. This runs before the checker, which never sees a type
argument: type references are rewritten to the name of the copy (decision 154).

An interface or an actor can be generic in the same way (decision 200): `interface Sink<T>`, `actor
Metered<T>: Sink<T>`; `spawn Metered<u8>(...)` and `narrow(x, Sink<u8>)` name the copy as a type does, and
the copy's handlers are the template's with `T` replaced.

Instances are made for every `Name<Args>` the program writes, also inside other instances,
until no new one appears. The templates stay in `program.generics` (the checker needs their
variant and struct names to build values) and are removed from `program.items`.
"""

import copy
import dataclasses

from .errors import CompileError
from . import syntax as S


def walk(node, visit):
    """Call `visit` on every dataclass node under `node`, parents first."""
    if isinstance(node, (list, tuple)):
        for x in node:
            walk(x, visit)
    elif dataclasses.is_dataclass(node) and not isinstance(node, type):
        visit(node)
        for f in dataclasses.fields(node):
            walk(getattr(node, f.name), visit)


def expand(program):
    generics = {}
    for item in program.items:
        if isinstance(item, (S.StructDecl, S.EnumDecl, S.InterfaceDecl, S.ActorDecl)) and item.params:
            if item.name in generics:
                raise CompileError(f"`{item.name}` is already defined", item.pos)
            generics[item.name] = item
    program.generics = generics
    if not generics:
        return
    templates = {id(g) for g in generics.values()}
    program.items = [i for i in program.items if id(i) not in templates]
    instances = {}          # name of the copy -> its declaration
    pending = []            # (declaration, file its type names are checked in)

    def rewrite(node, context):
        """Turn each `Name<Args>` under `node` into a plain reference to its copy."""
        def instance_of(name, args, pos):
            """The name of the copy of generic `name` for `args` (made if it is new), or None if `name` is not generic."""
            for arg in args:
                rewrite(arg, context)
            decl = generics.get(name)
            if decl is None:
                if args:
                    raise CompileError(f"`{name}` is not generic, so it takes no type arguments", pos)
                return None
            if not args:
                raise CompileError(
                    f"`{name}` needs type arguments: write `{name}<"
                    f"{', '.join(decl.params)}>` with the types", pos)
            if len(args) != len(decl.params):
                raise CompileError(
                    f"`{name}` takes {len(decl.params)} type argument(s), not {len(args)}", pos)
            # (`mut`, `val` and `iso` cannot be written on a type argument: the parser refuses.)
            key = f"{name}<{','.join(a.name for a in args)}>"
            if key not in instances:
                make_instance(key, decl, args, context)
            return key

        def visit(n):
            if isinstance(n, S.TypeRef):
                key = instance_of(n.name, n.args, n.pos)
                if key is not None:
                    n.name, n.args, n.visible_in = key, [], context
            elif isinstance(n, S.Spawn) and (n.type_args or n.name in generics):
                key = instance_of(n.name, n.type_args, n.pos)
                if key is not None:
                    n.name, n.type_args = key, []
            elif isinstance(n, S.Narrow) and (n.type_args or n.interface in generics):
                key = instance_of(n.interface, n.type_args, n.interface_pos)
                if key is not None:
                    n.interface, n.type_args = key, []
        walk(node, visit)

    def make_instance(key, decl, args, context):
        new = copy.deepcopy(decl)
        new.name, new.generic_base, new.params = key, decl.name, []
        binding = dict(zip(decl.params, args))

        def substitute(n):
            if isinstance(n, S.TypeRef) and n.name in binding and not n.args:
                # Errors about the argument point at where it was written, not into the template.
                arg = binding[n.name]
                n.name, n.visible_in, n.pos = arg.name, arg.visible_in or context, arg.pos
        walk(new, substitute)
        if isinstance(new, (S.StructDecl, S.EnumDecl)):
            owned = new.methods
        else:                                           # an actor's `new` and handlers, an interface's handlers
            owned = list(new.handlers) + ([new.new] if getattr(new, "new", None) is not None else [])
        for m in owned:
            m.owner = key
        instances[key] = new
        program.items.append(new)
        pending.append((new, new.file))      # the names the template wrote are the template's file's

    for item in list(program.items):
        rewrite(item, getattr(item, "file", None))
    while pending:
        decl, context = pending.pop()
        rewrite(decl, context)
    program.instances = instances
