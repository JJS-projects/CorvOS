"""Plain-text dumps of the compiler's stages, in the formats the self-hosted compiler must match
(docs/self-hosting.md)."""

from .errors import CompileError
from .lexer import lex
from .parser import parse
from .generics import expand
from .checker import check
from . import syntax as S


def dump_tokens(source):
    """The tokens of `source`, one per line: `LINE:COL KIND`, then the token's text if it has
    any, then for a number or a character its value in decimal, and for a string the bytes it
    holds as `x` and hex. A lexer error is one line, `error LINE:COL: MESSAGE`. This is the format
    the self-hosted lexer (compiler/lexdump.rook) prints, so the two can be compared byte for byte."""
    try:
        tokens = lex(source)
    except CompileError as err:
        return f"error {err.pos.line}:{err.pos.col}: {err.message}\n"
    lines = []
    for t in tokens:
        parts = [f"{t.pos.line}:{t.pos.col}", t.kind]
        if t.text != "":
            parts.append(t.text)
        if t.kind in ("int", "char"):
            parts.append(str(t.value))
        elif t.kind == "str":
            parts.append("x" + t.value.hex())
        lines.append(" ".join(parts))
    return "\n".join(lines) + "\n"


# ---- the syntax tree ---------------------------------------------------------------------------
#
# Every node is (kind, pos, text, kids): `pos` and `text` may be empty, `kids` is a list of nodes.
# A part that may be missing is the node `none`. A list that sits beside other children is wrapped in
# a group node (`Params`, `Fields`, ...). The self-hosted parser builds the same tree
# (compiler/lib/parser.rook) and docs/self-hosting.md lists every kind.

NONE = ("none", None, "", [])


def _words(*parts):
    return " ".join(p for p in parts if p)


def _opt(node):
    return NONE if node is None else tree(node)


def _group(kind, items, text=""):
    return (kind, None, text, [tree(i) for i in items])


_annotate = [False]     # set while `dump_bodies` builds a tree: nodes then carry what the checker found


NOTE_DEFAULTS = {"words": 1, "stopped": 0, "payload_words": 0, "args_words": 0}   # the parser's value: not a note

NOTE_KEYS = ("slot", "end_slot", "subject_slot", "self_slot", "result_slot", "temp", "args_slot",
             "args_words", "payload", "payload_words", "handler_index", "words", "number", "stopped",
             "lo", "hi", "const_value", "nslots")


def _notes(n):
    """What the checker recorded on node `n`, as the text ` : TYPE key=value ...` (decision 165): the
    type first, then the numbers in the order of NOTE_KEYS (`const_value` is written `const`), then
    `const_str=xHEX`, `variant=`, `struct=`, the flags `scalar` and `send`, and `slots=`. Nothing is
    written for what the checker did not set, or left at the value the parser gives it (NOTE_DEFAULTS); a function that was not checked has no notes at all."""
    parts = []
    ty = getattr(n, "ty", None)
    if ty is not None:
        parts.append(f": {ty}")
    if isinstance(n, S.FnDecl) and not getattr(n, "checked", False):
        return " ".join(parts)
    for key in NOTE_KEYS:
        value = getattr(n, key, None)
        if value is not None and NOTE_DEFAULTS.get(key) != value:
            parts.append(f"{'const' if key == 'const_value' else key}={int(value)}")
    if getattr(n, "const_str", None) is not None:
        parts.append(f"const_str=x{n.const_str.data.hex()}")
    variant = getattr(n, "variant", None)
    if variant is not None:
        parts.append(f"variant={variant.name}")
    struct = getattr(n, "struct", None)
    if struct is not None:
        parts.append(f"struct={struct.name}")
    if getattr(n, "scalar", False):
        parts.append("scalar")
    if getattr(n, "send", False):
        parts.append("send")
    slots = getattr(n, "slots", None)
    if slots:
        parts.append("slots=" + ",".join(f"{slot}/{offset}/{int(agg)}" for slot, offset, agg in slots))
    return " ".join(parts)


def tree(n):
    """The `(kind, pos, text, kids)` form of syntax node `n`. A top-level item of a program (it has
    `file`) ends with a `File` child; a generic's instance with a `GenericBase` child."""
    kind, pos, text, kids = _tree(n)
    if _annotate[0]:
        notes = _notes(n)
        if notes:
            text = f"{text} {notes}" if text else notes
    if isinstance(n, (S.StructDecl, S.EnumDecl)) and n.generic_base is not None:
        kids = kids + [("GenericBase", None, n.generic_base, [])]
    if getattr(n, "file", None) is not None:
        kids = kids + [("File", None, ("trusted " if n.trusted else "") + n.file, [])]
    return (kind, pos, text, kids)


def _tree(n):
    if isinstance(n, S.Program):
        return ("Program", None, "", [tree(i) for i in n.items])
    if isinstance(n, S.ModuleDecl):
        return ("Module", n.pos, "public", [])
    if isinstance(n, S.Import):
        kids = [("ModuleName", n.module_pos, n.module, [])]
        kids += [("ImportName", pos, name, []) for name, pos in n.names]
        return ("Import", n.pos, "", kids)
    if isinstance(n, S.TypeRef):
        mods = "mut" if n.mut else "val" if n.val else "iso" if n.iso else ""
        kids = [tree(a) for a in n.args]
        if n.visible_in is not None:                # set by generics.py
            kids.append(("VisibleIn", None, n.visible_in, []))
        return ("Type", n.pos, _words(mods, n.name), kids)
    if isinstance(n, S.Param):
        return ("Param", n.pos, n.name, [tree(n.type)])
    if isinstance(n, S.Field):
        return ("Field", n.pos, _words("pub" if n.public else "", "mut" if n.mutable else "", n.name),
                [tree(n.type)])
    if isinstance(n, S.FnDecl):
        text = _words("pub" if n.public else "", "mut" if n.mutating else "", n.handler, n.name)
        return ("Fn", n.pos, text, [_group("Params", n.params), _opt(n.ret), _opt(n.body)])
    if isinstance(n, S.ConstDecl):
        return ("Const", n.pos, _words("pub" if n.public else "", n.name), [tree(n.type), tree(n.value)])
    if isinstance(n, S.StructDecl):
        return ("Struct", n.pos, _words("pub" if n.public else "", n.name),
                [("TypeParams", None, " ".join(n.params), []), _group("Fields", n.fields),
                 _group("Methods", n.methods)])
    if isinstance(n, S.EnumDecl):
        return ("Enum", n.pos, _words("pub" if n.public else "", n.name),
                [("TypeParams", None, " ".join(n.params), []), _group("Variants", n.variants),
                 _group("Methods", n.methods)])
    if isinstance(n, S.VariantDecl):
        return ("Variant", n.pos, n.name, [NONE if n.fields is None else _group("Fields", n.fields)])
    if isinstance(n, S.ActorDecl):
        implements = ("Implements", None, "", [("Interface", pos, name, []) for name, pos in n.implements])
        return ("Actor", n.pos, _words("pub" if n.public else "", n.name),
                [implements, _group("Fields", n.fields), _opt(n.new), _group("Handlers", n.handlers)])
    if isinstance(n, S.InterfaceDecl):
        return ("Interface", n.pos, _words("pub" if n.public else "", n.name), [_group("Handlers", n.handlers)])
    if isinstance(n, S.Block):
        return ("Block", n.pos, "", [tree(s) for s in n.stmts])
    if isinstance(n, S.Decl):
        return ("Decl", n.pos, _words("mut" if n.mutable else "", n.name), [tree(n.type), tree(n.init)])
    if isinstance(n, S.Assign):
        return ("Assign", n.pos, n.op, [tree(n.target), tree(n.value)])
    if isinstance(n, S.ExprStmt):
        return ("ExprStmt", n.pos, "discard" if n.discard else "", [tree(n.expr)])
    if isinstance(n, S.If):
        return ("If", n.pos, "", [tree(n.cond), tree(n.then), _opt(n.else_)])
    if isinstance(n, S.Loop):
        return ("Loop", n.pos, n.label, [_opt(n.cond), tree(n.body)])
    if isinstance(n, S.ForRange):
        return ("ForRange", n.pos, f"{n.label} {n.var}",
                [tree(n.type), tree(n.start), tree(n.end), tree(n.body)])
    if isinstance(n, S.Match):
        return ("Match", n.pos, "", [tree(n.subject), _group("Arms", n.arms)])
    if isinstance(n, S.Arm):
        patterns = NONE if n.patterns is None else _group("Patterns", n.patterns)
        return ("Arm", n.pos, "", [patterns, tree(n.body)])
    if isinstance(n, S.Pattern):
        if n.value is None:
            return ("PatternVariant", n.pos, n.name,
                    [("Bind", pos, f"{field} {name}", []) for field, name, pos in n.bindings])
        return ("PatternValue", n.pos, "", [tree(n.value), _opt(n.end)])
    if isinstance(n, S.Unsafe):
        return ("Unsafe", n.pos, "", [tree(n.body)])
    if isinstance(n, S.Break):
        return ("Break", n.pos, n.label, [])
    if isinstance(n, S.Continue):
        return ("Continue", n.pos, n.label, [])
    if isinstance(n, S.Return):
        return ("Return", n.pos, "", [_opt(n.value)])
    if isinstance(n, S.IntLit):
        return ("Char" if n.is_char else "Int", n.pos, str(n.value), [])
    if isinstance(n, S.BoolLit):
        return ("Bool", n.pos, "true" if n.value else "false", [])
    if isinstance(n, S.StrLit):
        return ("Str", n.pos, "x" + n.data.hex(), [])
    if isinstance(n, S.Name):
        return ("Name", n.pos, n.ident, [])
    if isinstance(n, S.Unary):
        return ("Unary", n.pos, n.op, [tree(n.operand)])
    if isinstance(n, S.Binary):
        return ("Binary", n.pos, n.op, [tree(n.left), tree(n.right)])
    if isinstance(n, S.Call):
        return ("Call", n.pos, n.callee, [tree(a) for a in n.args])
    if isinstance(n, S.Cast):
        return ("Cast", n.pos, n.kind, [tree(n.expr), tree(n.target)])
    if isinstance(n, S.StructLit):
        return ("StructLit", n.pos, n.name, [("FieldInit", pos, name, [tree(e)]) for name, e, pos in n.fields])
    if isinstance(n, S.FieldAccess):
        return ("Access", n.pos, n.field, [_opt(n.obj)])
    if isinstance(n, S.MethodCall):
        return ("MethodCall", n.pos, n.name, [_opt(n.obj)] + [tree(a) for a in n.args])
    if isinstance(n, S.Copy):
        return ("Copy", n.pos, "", [tree(n.value)])
    if isinstance(n, S.Consume):
        return ("Consume", n.pos, "", [tree(n.value)])
    if isinstance(n, S.Narrow):
        return ("Narrow", n.pos, n.interface, [tree(n.value), ("Interface", n.interface_pos, n.interface, [])])
    if isinstance(n, S.Spawn):
        return ("Spawn", n.pos, n.name, [tree(a) for a in n.args])
    if isinstance(n, S.SelfRef):
        return ("SelfRef", n.pos, "", [])
    if isinstance(n, S.SelfValue):
        return ("SelfValue", n.pos, "", [])
    if isinstance(n, S.MatchExpr):
        return ("MatchExpr", n.pos, "", [tree(n.subject), _group("Arms", n.arms)])
    if isinstance(n, S.MutArg):
        return ("MutArg", n.pos, "", [tree(n.place)])
    if isinstance(n, S.IfExpr):
        return ("IfExpr", n.pos, "", [tree(n.cond), tree(n.then), tree(n.else_)])
    raise AssertionError(f"dump_tree: no form for {type(n).__name__}")


def dump_tree(source):
    """The syntax tree of `source`, one node per line, two spaces of indent per level:
    `KIND [LINE:COL] [TEXT]`. A lexer or parser error is one line, `error LINE:COL: MESSAGE`."""
    try:
        program = parse(lex(source))
    except CompileError as err:
        return f"error {err.pos.line}:{err.pos.col}: {err.message}\n"
    return _print_tree(tree(program))


def _print_tree(root):
    """One node a line. A place in another file than its top-level item's is written `FILE:LINE:COL`."""
    lines = []

    def walk(node, depth, here):
        kind, pos, text, kids = node
        if depth == 1 and pos is not None:
            here = pos.file
        parts = [kind]
        if pos is not None:
            where = f"{pos.line}:{pos.col}"
            if pos.file is not None and pos.file != here:
                where = f"{pos.file}:{where}"
            parts.append(where)
        if text != "":
            parts.append(text)
        lines.append("  " * depth + " ".join(parts))
        for kid in kids:
            walk(kid, depth + 1, here)

    walk(root, 0, None)
    return "\n".join(lines) + "\n"


# ---- whole programs ----------------------------------------------------------------------------
#
# A stage that works on a whole program is given several files in one stream: each starts with a
# line `@@@ PATH` (or `@@@ trusted PATH`), then the file's text. (`@` cannot start anything in Rook,
# so no file has such a line.)

def split_stream(stream):
    """[(path, trusted, source)] from the text of a stream of files."""
    files = []
    for line in stream.splitlines(keepends=True):
        if line.startswith("@@@ "):
            head = line[4:].rstrip("\n")
            trusted = head.startswith("trusted ")
            files.append([head[8:] if trusted else head, trusted, ""])
        elif files:
            files[-1][2] += line
    return [tuple(f) for f in files]


def make_stream(files):
    """The stream for [(path, trusted, source)]: what `split_stream` takes apart."""
    return "".join(f"@@@ {'trusted ' if trusted else ''}{path}\n{source}" for path, trusted, source in files)


def dump_expanded(stream):
    """The program in `stream` after generics are expanded (decision 154), as `dump_tree` prints it.
    An error is one line, `error FILE:LINE:COL: MESSAGE`. The self-hosted program is
    compiler/expanddump.rook."""
    program = S.Program()
    try:
        for path, trusted, source in split_stream(stream):
            for item in parse(lex(source, path)).items:
                item.file, item.trusted = path, trusted
                program.items.append(item)
        expand(program)
    except CompileError as err:
        return f"error {err.pos.file}:{err.pos.line}:{err.pos.col}: {err.message}\n"
    return _print_tree(tree(program))


# ---- the declarations of a checked program -----------------------------------------------------

def _params(sig, flags):
    """`(T, mut T)` for a function; for a handler or `new`, `(iso T, val T)` from the declaration."""
    parts = []
    for (ty, is_mut), p in zip(sig.params, sig.decl.params):
        if flags:
            mods = "iso " if p.type.iso else "val " if p.type.val else ""
        else:
            mods = "mut " if is_mut else ""
        parts.append(f"{mods}{ty}")
    return "(" + ", ".join(parts) + ")"


def _signature(sig, flags=False):
    return f"{_params(sig, flags)} -> {sig.ret}"


def dump_declarations(stream):
    """What the checker knows about a program once every declaration is checked (names, types,
    sizes, signatures, handler numbers, imports), before constants and bodies. An error is one
    line, `error FILE:LINE:COL: MESSAGE`. The self-hosted program is compiler/declsdump.rook."""
    program = S.Program()
    try:
        for path, trusted, source in split_stream(stream):
            for item in parse(lex(source, path)).items:
                item.file, item.trusted = path, trusted
                program.items.append(item)
        expand(program)
        checker = check(program, stop="declarations")
    except CompileError as err:
        return f"error {err.pos.file}:{err.pos.line}:{err.pos.col}: {err.message}\n"
    out = []
    for file, names in checker.imports.items():
        out.append(f"imports {file}: " + " ".join(f"{name}<-{target}" for name, target in names.items()))
    out.append("generics " + " ".join(checker.generic_types))
    out.append("generic-variants " + " ".join(f"{v}:{e}" for v, e in checker.generic_variants.items()))
    out.append("handler-numbers " + " ".join(f"{n}={i}" for n, i in checker.handler_numbers.items()))
    for st in checker.structs.values():
        out.append(f"struct {st.name} words={st.words}")
        for name, ty in st.fields:
            out.append(f"  field {name} {ty}" + (" pub" if name in st.public_fields else ""))
        if st.new_sig is not None:
            out.append(f"  new {_params(st.new_sig, True)}")
        for name, sig in st.methods.items():
            out.append(f"  method {name}{' mut' if sig.mutating else ''} {_signature(sig)}")
    for en in checker.enums.values():
        out.append(f"enum {en.name} words={en.words}")
        for v in en.variants.values():
            out.append(f"  variant {v.name} tag={v.tag}")
            for name, ty in v.fields:
                out.append(f"    field {name} {ty}")
        for name, sig in en.methods.items():
            out.append(f"  method {name}{' mut' if sig.mutating else ''} {_signature(sig)}")
    for actor in checker.actors.values():
        out.append(f"actor {actor.name} words={actor.words}")
        for name, ty in actor.fields:
            flags = "".join(f" {w}" for w, group in (("mut", actor.mut_fields), ("val", actor.val_fields),
                                                       ("iso", actor.iso_fields)) if name in group)
            out.append(f"  field {name} {ty}{flags}")
        if actor.new_sig is not None:
            out.append(f"  new {_params(actor.new_sig, True)}")
        for name, sig in actor.methods.items():
            out.append(f"  handler {name} {sig.decl.handler} {_params(sig, True)} number={sig.decl.number}")
        for iface in actor.implements:
            out.append(f"  implements {iface.name}")
    for iface in checker.interfaces.values():
        out.append(f"interface {iface.name}")
        for name, sig in iface.methods.items():
            out.append(f"  handler {name} {sig.decl.handler} {_params(sig, True)} number={sig.decl.number}")
    for name, sig in checker.fns.items():
        out.append(f"fn {name} {_signature(sig)}")
    return "\n".join(out) + "\n"


# ---- the bodies of a checked program -----------------------------------------------------------

def dump_bodies(stream, ownership=False, actors=False, bodies=True):
    """The program in `stream` checked as far as the self-hosted checker is (decision 165): the
    constants (`const NAME TYPE VALUE`), then the tree of the items declared in the stream's last file
    after the checker, every expression with `: TYPE` and the slots and values it recorded. The
    bodies of the other files are not checked (`bodies=` the last file); an actor's `new` and handlers
    are checked only with `actors`; with `bodies=False` no body is, with `bodies="all"` the bodies of every file are (the tree shown is still the last file's). An error is one line, `error FILE:LINE:COL: MESSAGE`. The
    self-hosted program is compiler/bodiesdump.rook."""
    program = S.Program()
    files = split_stream(stream)
    try:
        for path, trusted, source in files:
            for item in parse(lex(source, path)).items:
                item.file, item.trusted = path, trusted
                program.items.append(item)
        expand(program)
        last = files[-1][0] if files else None
        which = "all" if bodies == "all" else (last if bodies else None)
        checker = check(program, ownership=ownership, bodies=which or "none", actor_bodies=actors)
    except CompileError as err:
        return f"error {err.pos.file}:{err.pos.line}:{err.pos.col}: {err.message}\n"
    out = [f"const {name} {ty} " + (f"x{value.data.hex()}" if isinstance(value, S.StrLit) else str(int(value)))
           for name, (ty, value) in checker.consts.items()]
    shown = S.Program()
    shown.items = [item for item in program.items if item.file == last]
    _annotate[0] = True
    try:
        body = _print_tree(tree(shown))
    finally:
        _annotate[0] = False
    return "\n".join(out) + ("\n" if out else "") + body
