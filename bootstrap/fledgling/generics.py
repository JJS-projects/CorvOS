"""Generics: a separate copy of a struct or enum for each list of type arguments.

`enum Option<T> { Some(value: T)  None }` is a template. Every `Option<u64>` written in the
program makes a real enum named `Option<u64>` (its `generic_base` is `Option`), with `T`
replaced by `u64` in its fields and in its methods' bodies; that copy is then checked and
compiled like any other enum. This runs before the checker, which never sees a type
argument: type references are rewritten to the name of the copy (decision 154).

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
        if isinstance(item, (S.StructDecl, S.EnumDecl)) and item.params:
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
        def visit(n):
            if not isinstance(n, S.TypeRef):
                return
            for arg in n.args:
                rewrite(arg, context)
            decl = generics.get(n.name)
            if decl is None:
                if n.args:
                    raise CompileError(f"`{n.name}` is not generic, so it takes no type arguments",
                                       n.pos)
                return
            if not n.args:
                raise CompileError(
                    f"`{n.name}` needs type arguments: write `{n.name}<"
                    f"{', '.join(decl.params)}>` with the types", n.pos)
            if len(n.args) != len(decl.params):
                raise CompileError(
                    f"`{n.name}` takes {len(decl.params)} type argument(s), not {len(n.args)}",
                    n.pos)
            # (`mut`, `val` and `iso` cannot be written on a type argument: the parser refuses.)
            key = f"{n.name}<{','.join(a.name for a in n.args)}>"
            if key not in instances:
                make_instance(key, decl, n.args, context)
            n.name, n.args, n.visible_in = key, [], context
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
        for m in new.methods:
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
