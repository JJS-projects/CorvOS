"""Parser: tokens -> syntax tree (recursive descent).

Operator precedence, loosest first:
    or  xor
    and
    not                         (prefix)
    ==  !=  <  <=  >  >=        (non-associative)
    +  -
    *  /  %  shl  shr
    -                           (prefix)
    as  wrap
`and`, `or`, `xor` and `not` are logical on bool and bitwise on integers.
Because `and` is looser than comparisons, `x and MASK != 0` means
`x and (MASK != 0)`, which the checker rejects; write `(x and MASK) != 0`.

Where readers from other languages would disagree about the meaning, brackets
are required instead of a precedence rule (principle 4):
  - `or` and `xor` can't be mixed, and `and` can't appear directly inside them
  - `not` can't be applied directly to a comparison
  - shifts can't be mixed with arithmetic

Blocks are closed by `}`. A named closer, `} name`, closes every open block
back to and including the one called `name`. Only names written in the code can
be used: a function's, struct's or enum's name, or a loop's label. Every loop is labelled
(`rows: loop ...`), and `break` and `continue` name the loop they act on.

A capitalised name is always a type, a variant or a constant (the compiler
enforces naming), so `Point(x: 1, y: 2)` is a struct value, `Busy(job: 3)` an
enum value, and `point(1, 2)` a function call.

`match` arms are one per line: `pattern => body`. In a match used as a value,
each body is one expression, or `return`, `break` or `continue` to leave
instead. In a match statement, each body is one statement or a `{ block }`.
"""

from .errors import CompileError
from . import syntax as S

BINARY_LEVELS = [
    ["or", "xor"],
    ["and"],
    None,                       # prefix `not` sits here
    ["==", "!=", "<", "<=", ">", ">="],
    ["+", "-"],
    ["*", "/", "%", "shl", "shr"],
]
COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%="}
ARITH_OPS = {"+", "-", "*", "/", "%"}
SHIFT_OPS = {"shl", "shr"}
CLOSER_NAMES = {"ident"}


def parse(tokens):
    return Parser(tokens).program()


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0
        self.open_blocks = []   # label sets of the blocks currently open

    # ---- helpers -------------------------------------------------------

    def peek(self, offset=0):
        return self.tokens[min(self.i + offset, len(self.tokens) - 1)]

    def at(self, kind):
        return self.peek().kind == kind

    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, kind, what=None):
        tok = self.peek()
        if tok.kind != kind:
            raise CompileError(f"expected {what or describe(kind)}, found {describe_tok(tok)}",
                               tok.pos)
        return self.advance()

    def skip_newlines(self):
        while self.at("newline"):
            self.advance()

    def end_statement(self):
        """A statement ends at a newline, or just before `}`."""
        if self.at("newline"):
            self.advance()
        elif not self.at("}") and not self.at("eof"):
            tok = self.peek()
            raise CompileError(f"expected end of line, found {describe_tok(tok)}", tok.pos)

    # ---- top level -----------------------------------------------------

    def program(self):
        prog = S.Program()
        self.skip_newlines()
        while not self.at("eof"):
            public = False
            if self.at("pub"):
                self.advance()
                public = True
            if self.at("actor"):
                prog.items.append(self.actor_decl(public))
            elif self.at("interface"):
                prog.items.append(self.interface_decl(public))
            elif self.at("struct"):
                prog.items.append(self.struct_decl(public))
            elif self.at("enum"):
                prog.items.append(self.enum_decl(public))
            elif self.at("fn"):
                prog.items.append(self.fn_decl(extern=False, public=public))
            elif self.at("extern"):
                self.advance()
                prog.items.append(self.fn_decl(extern=True, public=public))
            elif self.at("mut"):
                raise CompileError(
                    "there is no global mutable state in Rook: top-level declarations can't be `mut`",
                    self.peek().pos)
            elif self.at("ident") and self.peek(1).kind == ":":
                prog.items.append(self.const_decl(public))
            else:
                tok = self.peek()
                raise CompileError(
                    f"expected `fn`, `extern fn`, `struct`, `enum`, `actor`, `interface` or a constant at top level, "
                    f"found {describe_tok(tok)}", tok.pos)
            self.skip_newlines()
        return prog

    def struct_decl(self, public):
        start = self.expect("struct")
        name = self.expect("ident", "struct name").text
        self.expect("{")
        self.open_blocks.append({name})
        fields, methods = [], []
        while True:
            self.skip_newlines()
            if self.at("}"):
                self.close_block({name}, start)
                break
            if self.at("eof"):
                raise CompileError("unclosed `{`", start.pos)
            member_public = False
            if self.at("pub"):
                self.advance()
                member_public = True
            if self.at("fn") or (self.at("mut") and self.peek(1).kind == "fn"):
                mutating = self.at("mut")
                if mutating:
                    self.advance()
                methods.append(self.fn_decl(extern=False, public=member_public,
                                            mutating=mutating, owner=name))
            elif self.at("mut"):
                raise CompileError(
                    "struct fields aren't marked `mut`: a field can change when the variable "
                    "holding the struct is `mut`", self.peek().pos)
            elif self.at("ident") and self.peek(1).kind == ":":
                ftok = self.advance()
                self.advance()
                fields.append(S.Field(ftok.text, self.type_ref(), ftok.pos, member_public))
                self.end_statement()
            else:
                tok = self.peek()
                raise CompileError(f"expected a field or a method, found {describe_tok(tok)}",
                                   tok.pos)
        self.open_blocks.pop()
        self.end_statement()
        return S.StructDecl(name, fields, methods, start.pos, public)

    def actor_decl(self, public):
        start = self.expect("actor")
        name = self.expect("ident", "actor name").text
        implements = []
        if self.at(":"):
            self.advance()
            while True:
                itok = self.expect("ident", "an interface name")
                implements.append((itok.text, itok.pos))
                if not self.at(","):
                    break
                self.advance()
        self.expect("{")
        self.open_blocks.append({name})
        fields, handlers, new = [], [], None
        while True:
            self.skip_newlines()
            if self.at("}"):
                self.close_block({name}, start)
                break
            if self.at("eof"):
                raise CompileError("unclosed `{`", start.pos)
            member_public = False
            if self.at("pub"):
                self.advance()
                member_public = True
            if self.at("ident") and self.peek().text == "new" and self.peek(1).kind == "(":
                if member_public:
                    raise CompileError("`new` is not marked `pub`: it runs when the actor is "
                                       "spawned", self.peek().pos)
                if new is not None:
                    raise CompileError(f"`{name}` already has a `new`", self.peek().pos)
                new = self.actor_fn(name, handler=None)
            elif self.at("lossy") or self.at("reliable") or self.at("on"):
                handlers.append(self.actor_fn(name, handler=True, public=member_public))
            elif self.at("mut") or (self.at("ident") and self.peek(1).kind == ":"):
                mutable = False
                if self.at("mut"):
                    self.advance()
                    mutable = True
                ftok = self.expect("ident", "a field name")
                self.expect(":", "`:` and the field's type")
                field = S.Field(ftok.text, self.type_ref(allow_val=True, allow_iso=True), ftok.pos,
                                member_public,
                                mutable)
                fields.append(field)
                self.end_statement()
            else:
                tok = self.peek()
                raise CompileError(
                    f"expected a field, `new` or a handler, found {describe_tok(tok)}", tok.pos)
        self.open_blocks.pop()
        self.end_statement()
        return S.ActorDecl(name, fields, new, handlers, start.pos, public, implements)

    def interface_decl(self, public):
        """`interface Name { [lossy|reliable] on name(params) }`: handlers with no bodies."""
        start = self.expect("interface")
        name = self.expect("ident", "interface name").text
        self.expect("{")
        self.open_blocks.append({name})
        handlers = []
        while True:
            self.skip_newlines()
            if self.at("}"):
                self.close_block({name}, start)
                break
            if self.at("eof"):
                raise CompileError("unclosed `{`", start.pos)
            if self.at("pub"):
                raise CompileError(
                    "an interface's handlers are not marked `pub`: anything that holds the "
                    "interface can send them", self.peek().pos)
            if not (self.at("lossy") or self.at("reliable") or self.at("on")):
                tok = self.peek()
                raise CompileError(f"expected a handler, found {describe_tok(tok)}", tok.pos)
            if self.at("on"):
                raise CompileError(
                    "a handler is `lossy` or `reliable`: write one of them before `on`",
                    self.peek().pos)
            kind = self.advance().kind
            if not self.at("on"):
                raise CompileError(f"`{kind}` goes before `on`, as in `{kind} on name(...)`",
                                   self.peek().pos)
            on = self.advance()
            name_tok = self.expect("ident", "handler name")
            self.expect("(")
            params = []
            while not self.at(")"):
                ptok = self.expect("ident", "parameter name")
                self.expect(":")
                params.append(S.Param(ptok.text, self.type_ref(allow_mut=True), ptok.pos))
                if not self.at(")"):
                    self.expect(",", "`,` or `)`")
            self.expect(")")
            if self.at("->"):
                raise CompileError(
                    "a handler has no result: to answer, send a message to a reference you "
                    "were given", self.peek().pos)
            if self.at("{"):
                raise CompileError(
                    "an interface only declares handlers: the actor that implements it "
                    "writes the body", self.peek().pos)
            self.end_statement()
            # Public: whoever holds the interface may send it.
            handlers.append(S.FnDecl(name_tok.text, params, None, None, on.pos, True, False,
                                     name, kind))
        self.open_blocks.pop()
        self.end_statement()
        return S.InterfaceDecl(name, handlers, start.pos, public)

    def actor_fn(self, owner, handler, public=False):
        """`new(params) { }` or `[lossy|reliable] on name(params) { }` in an actor."""
        kind = None
        if handler:
            if self.at("on"):
                raise CompileError(
                    "a handler is `lossy` or `reliable`: write one of them before `on`",
                    self.peek().pos)
            kind = self.advance().kind
            if not self.at("on"):
                raise CompileError(f"`{kind}` goes before `on`, as in `{kind} on name(...)`",
                                   self.peek().pos)
            start = self.advance()
            name_tok = self.expect("ident", "handler name")
        else:
            start = name_tok = self.advance()       # the word `new`
        self.expect("(")
        params = []
        while not self.at(")"):
            ptok = self.expect("ident", "parameter name")
            self.expect(":")
            params.append(S.Param(ptok.text, self.type_ref(allow_mut=True, allow_val=True,
                                                           allow_iso=True), ptok.pos))
            if not self.at(")"):
                self.expect(",", "`,` or `)`")
        self.expect(")")
        ret = None
        if self.at("->"):
            raise CompileError(
                "a handler has no result: to answer, send a message to a reference you were given",
                self.peek().pos)
        body = self.block({name_tok.text})
        self.end_statement()
        return S.FnDecl(name_tok.text, params, ret, body, start.pos, public, True, owner, kind)

    def enum_decl(self, public):
        start = self.expect("enum")
        name = self.expect("ident", "enum name").text
        self.expect("{")
        self.open_blocks.append({name})
        variants, methods = [], []
        while True:
            self.skip_newlines()
            if self.at("}"):
                self.close_block({name}, start)
                break
            if self.at("eof"):
                raise CompileError("unclosed `{`", start.pos)
            member_public = False
            if self.at("pub") and self.peek(1).kind in ("fn", "mut"):
                self.advance()
                member_public = True
            if self.at("fn") or (self.at("mut") and self.peek(1).kind == "fn"):
                mutating = self.at("mut")
                if mutating:
                    self.advance()
                methods.append(self.fn_decl(extern=False, public=member_public,
                                            mutating=mutating, owner=name))
            elif self.at("ident") and self.peek(1).kind == ":":
                raise CompileError(
                    "an enum has variants, not fields: write `Name` or `Name(field: type)`",
                    self.peek().pos)
            elif self.at("ident"):
                vtok = self.advance()
                fields = None
                if self.at("("):
                    ptok = self.advance()
                    if self.at(")"):
                        raise CompileError(
                            f"a variant with no fields is written without brackets: `{vtok.text}`",
                            ptok.pos)
                    fields = []
                    while not self.at(")"):
                        ftok = self.expect("ident", "a field name")
                        self.expect(":", "`:` and the field's type")
                        fields.append(S.Field(ftok.text, self.type_ref(), ftok.pos))
                        if not self.at(")"):
                            self.expect(",", "`,` or `)`")
                    self.expect(")")
                variants.append(S.VariantDecl(vtok.text, fields, vtok.pos))
                self.end_statement()
            else:
                tok = self.peek()
                raise CompileError(f"expected a variant or a method, found {describe_tok(tok)}",
                                   tok.pos)
        self.open_blocks.pop()
        self.end_statement()
        return S.EnumDecl(name, variants, methods, start.pos, public)

    def fn_decl(self, extern, public, mutating=False, owner=None):
        start = self.expect("fn")
        name = self.expect("ident", "function name").text
        self.expect("(")
        params = []
        while not self.at(")"):
            ptok = self.expect("ident", "parameter name")
            self.expect(":")
            params.append(S.Param(ptok.text, self.type_ref(allow_mut=True, allow_iso=True), ptok.pos))
            if not self.at(")"):
                self.expect(",", "`,` or `)`")
        self.expect(")")
        ret = None
        if self.at("->"):
            self.advance()
            ret = self.type_ref()
        elif self.at(":"):
            raise CompileError("return types are written with `->`, as in `fn f() -> u64`",
                               self.peek().pos)
        body = None
        if extern:
            if self.at("{"):
                raise CompileError("`extern fn` declares a function written elsewhere and has no body",
                                   self.peek().pos)
            self.end_statement()
        else:
            body = self.block({name})
            self.end_statement()
        return S.FnDecl(name, params, ret, body, start.pos, public, mutating, owner)

    def const_decl(self, public):
        name = self.expect("ident")
        self.expect(":")
        ty = self.type_ref()
        self.expect("=", "`=` and a value")
        value = self.expr()
        self.end_statement()
        return S.ConstDecl(name.text, ty, value, name.pos, public)

    def type_ref(self, allow_mut=False, allow_val=False, allow_iso=False):
        if (allow_val and self.at("ident") and self.peek().text == "val"
                and self.peek(1).kind == "ident"):
            self.advance()
            tok = self.expect("ident", "a type")
            return S.TypeRef(tok.text, tok.pos, val=True)
        if (allow_iso and self.at("ident") and self.peek().text == "iso"
                and self.peek(1).kind == "ident"):
            self.advance()
            tok = self.expect("ident", "a type")
            return S.TypeRef(tok.text, tok.pos, iso=True)
        if (not allow_iso and self.at("ident") and self.peek().text == "iso"
                and self.peek(1).kind == "ident"):
            raise CompileError(
                "`iso` goes only on an actor's fields and on parameters", self.peek().pos)
        if (not allow_val and self.at("ident") and self.peek().text == "val"
                and self.peek(1).kind == "ident"):
            raise CompileError(
                "`val` goes only on an actor's fields and `new` parameters for now",
                self.peek().pos)
        mut = False
        if self.at("mut"):
            if not allow_mut:
                raise CompileError(
                    "`mut` goes before the name, as in `mut count: u64 = 0`", self.peek().pos)
            self.advance()
            mut = True
            if self.at("ident") and self.peek().text == "iso" and self.peek(1).kind == "ident":
                raise CompileError("a parameter is lent as `mut` or owned as `iso`, not both",
                                   self.peek().pos)
        tok = self.expect("ident", "a type")
        return S.TypeRef(tok.text, tok.pos, mut)

    # ---- blocks and named closers -------------------------------------

    def block(self, labels):
        """Parse `{ statements }`. `labels` are the names a named closer can
        use for this block. If a named closer names an outer block, this block
        ends without consuming it, and the outer block consumes it."""
        start = self.expect("{")
        self.open_blocks.append(labels)
        stmts = []
        while True:
            self.skip_newlines()
            if self.at("}"):
                self.close_block(labels, start)
                break
            if self.at("eof"):
                raise CompileError("unclosed `{`", start.pos)
            stmts.append(self.statement())
        self.open_blocks.pop()
        return S.Block(stmts, start.pos)

    def close_block(self, labels, start):
        """At a `}` that ends the innermost open block. Consume it (and its
        name) if it closes this block; leave a named closer for an outer
        block to consume."""
        name_tok = self.peek(1)
        if name_tok.kind in ("loop", "if"):
            raise CompileError(
                f"a closer names a function, struct or loop label, not `{name_tok.kind}`",
                name_tok.pos)
        if name_tok.kind not in CLOSER_NAMES:
            self.advance()                          # plain `}`
            return
        name = name_tok.text
        if name in labels:
            self.advance()                          # `} name` closing this block
            self.advance()
            return
        if any(name in outer for outer in self.open_blocks[:-1]):
            return                                  # closes an outer block too
        raise CompileError(f"`}} {name}` doesn't match any open block", name_tok.pos)

    # ---- statements ----------------------------------------------------

    def statement(self):
        tok = self.peek()
        if tok.kind == "ident" and self.peek(1).kind == ":" and self.peek(2).kind == "loop":
            label = self.advance().text
            self.advance()
            for outer in self.open_blocks:
                if label in outer:
                    raise CompileError(f"`{label}` already names an enclosing block", tok.pos)
            stmt = self.loop_stmt(label, tok.pos)
            self.end_statement()
            return stmt
        if tok.kind == "mut" or (tok.kind == "ident" and self.peek(1).kind == ":"):
            mutable = tok.kind == "mut"
            if mutable:
                self.advance()
            name = self.expect("ident", "variable name")
            if not self.at(":"):
                raise CompileError("declarations need a type: `mut name: type = value`",
                                   self.peek().pos)
            self.advance()
            ty = self.type_ref()
            self.expect("=", "`=` (variables must be initialised)")
            init = self.expr()
            self.end_statement()
            return S.Decl(tok.pos, name.text, mutable, ty, init)
        if tok.kind == "if":
            stmt = self.if_stmt()
            self.end_statement()
            return stmt
        if tok.kind == "match":
            start, subject, open_ = self.match_head()
            arms = self.match_arms(False, start, open_)
            self.end_statement()
            return S.Match(start.pos, subject, arms)
        if tok.kind == "unsafe":
            self.advance()
            if not self.at("{"):
                raise CompileError("`unsafe` marks a block: `unsafe { ... }`", self.peek().pos)
            body = self.block(set())
            self.end_statement()
            return S.Unsafe(tok.pos, body)
        if tok.kind == "loop":
            raise CompileError("every loop is named: write `name: loop ...`", tok.pos)
        if tok.kind in ("break", "continue"):
            self.advance()
            if not self.at("ident"):
                raise CompileError(
                    f"`{tok.kind}` names the loop it acts on, as in `{tok.kind} rows`", tok.pos)
            label = self.advance().text
            self.end_statement()
            cls = S.Break if tok.kind == "break" else S.Continue
            return cls(tok.pos, label)
        if tok.kind == "discard":
            self.advance()
            expr = self.expr()
            self.end_statement()
            return S.ExprStmt(tok.pos, expr, discard=True)
        if tok.kind == "return":
            self.advance()
            value = None
            if not self.at("newline") and not self.at("}"):
                value = self.expr()
            self.end_statement()
            return S.Return(tok.pos, value)

        expr = self.expr()
        if self.peek().kind in ASSIGN_OPS:
            op = self.advance()
            if not isinstance(expr, (S.Name, S.FieldAccess, S.SelfValue)):
                raise CompileError("can only assign to a variable or a field", expr.pos)
            value = self.expr()
            self.end_statement()
            return S.Assign(op.pos, expr, op.kind, value)
        self.end_statement()
        return S.ExprStmt(tok.pos, expr)

    def if_stmt(self):
        start = self.expect("if")
        cond = self.expr()
        then = self.block(set())
        else_ = None
        # Allow `else` on the line after the closing `}`.
        save = self.i
        self.skip_newlines()
        if self.at("else"):
            self.advance()
            else_ = self.if_stmt() if self.at("if") else self.block(set())
        else:
            self.i = save
        return S.If(start.pos, cond, then, else_)

    # ---- match ---------------------------------------------------------

    def match_head(self):
        start = self.expect("match")
        subject = self.expr()
        open_ = self.expect("{", "`{` to start the arms")
        return start, subject, open_

    def match_arms(self, as_value, start, open_):
        self.open_blocks.append(set())
        arms = []
        while True:
            self.skip_newlines()
            if self.at("}"):
                self.close_block(set(), open_)
                break
            if self.at("eof"):
                raise CompileError("unclosed `{`", open_.pos)
            arms.append(self.arm(as_value))
        self.open_blocks.pop()
        if not arms:
            raise CompileError("a `match` needs at least one arm", start.pos)
        return arms

    def arm(self, as_value):
        pos = self.peek().pos
        if self.at("else"):
            self.advance()
            patterns = None
        else:
            patterns = [self.pattern()]
            while self.at(","):
                self.advance()
                patterns.append(self.pattern())
        self.expect("=>", "`=>` after the pattern")
        if as_value:
            if self.peek().kind in ("return", "break", "continue"):
                body = self.statement()             # leaves the match instead
            elif self.at("{"):
                raise CompileError(
                    "each arm of a `match` used as a value is a single expression, without `{ }`",
                    self.peek().pos)
            else:
                body = self.expr()
                self.end_statement()
        elif self.at("{"):
            body = self.block(set())
            self.end_statement()
        else:
            tok = self.peek()
            if tok.kind == "mut" or (tok.kind == "ident" and self.peek(1).kind == ":"):
                raise CompileError("a declaration in an arm needs a block: `=> { ... }`", tok.pos)
            body = self.statement()
        return S.Arm(pos, patterns, body)

    def pattern(self):
        tok = self.peek()
        if tok.kind == "ident" and tok.text[0].isupper() and self.peek(1).kind == "(":
            self.advance()
            ptok = self.advance()
            if self.at(")"):
                raise CompileError(
                    f"a variant with no fields is written without brackets: `{tok.text}`", ptok.pos)
            bindings = []
            while not self.at(")"):
                if not (self.at("ident") and self.peek(1).kind == ":"):
                    raise CompileError(
                        f"fields are bound by name, as in `{tok.text}(field: name)`",
                        self.peek().pos)
                ftok = self.advance()
                self.advance()
                btok = self.peek()
                if btok.kind != "ident" or btok.text[0].isupper():
                    raise CompileError(
                        "a field binds a new name, or `_` to ignore it; patterns can't be nested",
                        btok.pos)
                self.advance()
                bindings.append((ftok.text, btok.text, btok.pos))
                if not self.at(")"):
                    self.expect(",", "`,` or `)`")
            self.expect(")")
            return S.Pattern(tok.pos, name=tok.text, bindings=bindings)
        value = self.unary()
        end = None
        if self.at(".."):
            self.advance()
            end = self.unary()
        return S.Pattern(tok.pos, value=value, end=end)

    def loop_stmt(self, label, pos):
        self.expect("loop")
        if self.at("{"):
            return S.Loop(pos, label, None, self.block({label}))
        if self.at("ident") and self.peek(1).kind in ("in", ":"):
            var = self.advance().text
            if not self.at(":"):
                raise CompileError(
                    f"write the loop variable's type, as in `{label}: loop {var}: u64 in ...`",
                    self.peek().pos)
            self.advance()
            ty = self.type_ref()
            self.expect("in")
            first = self.expr()
            self.expect("..", "`..` (a range is written `start..end`)")
            last = self.expr()
            return S.ForRange(pos, label, var, ty, first, last, self.block({label}))
        cond = self.expr()
        return S.Loop(pos, label, cond, self.block({label}))

    # ---- expressions ---------------------------------------------------

    def expr(self):
        return self.binary(0)

    def binary(self, level):
        if level == len(BINARY_LEVELS):
            return self.unary()
        ops = BINARY_LEVELS[level]
        if ops is None:
            return self.not_expr(level)
        left = self.binary(level + 1)
        while self.peek().kind in ops:
            op = self.advance()
            right = self.binary(level + 1)
            if op.kind in COMPARISONS and self.peek().kind in COMPARISONS:
                raise CompileError("comparisons can't be chained; use `and`", self.peek().pos)
            left = S.Binary(op.pos, op.kind, left, right)
            require_brackets(left)
        return left

    def not_expr(self, level):
        if self.at("not"):
            tok = self.advance()
            operand = self.not_expr(level)
            if (isinstance(operand, S.Binary) and operand.op in COMPARISONS
                    and not operand.parens):
                raise CompileError(
                    "`not` before a comparison is ambiguous: write `not (a == b)` or `(not a) == b`",
                    tok.pos)
            return S.Unary(tok.pos, "not", operand)
        return self.binary(level + 1)

    def unary(self):
        tok = self.peek()
        if tok.kind == "-":
            self.advance()
            operand = self.unary()
            # Fold `-literal` so that e.g. -128 is a valid i8 literal.
            if isinstance(operand, S.IntLit) and not operand.is_char:
                return S.IntLit(tok.pos, -operand.value)
            return S.Unary(tok.pos, "-", operand)
        return self.cast()

    def cast(self):
        expr = self.postfix(self.primary())
        while self.at("as") or self.at("wrap"):
            tok = self.advance()
            expr = S.Cast(tok.pos, expr, self.type_ref(), tok.kind)
        return expr

    def primary(self):
        tok = self.peek()
        if tok.kind == "int":
            self.advance()
            return S.IntLit(tok.pos, tok.value)
        if tok.kind == "char":
            self.advance()
            return S.IntLit(tok.pos, tok.value, is_char=True)
        if tok.kind in ("true", "false"):
            self.advance()
            return S.BoolLit(tok.pos, tok.kind == "true")
        if tok.kind == "str":
            self.advance()
            return S.StrLit(tok.pos, tok.value)
        if tok.kind == "if":
            return self.if_expr()
        if tok.kind == "match":
            start, subject, open_ = self.match_head()
            return S.MatchExpr(start.pos, subject, self.match_arms(True, start, open_))
        if tok.kind == "spawn":
            self.advance()
            name = self.expect("ident", "an actor name")
            if not self.at("("):
                raise CompileError(f"write `spawn {name.text}(...)` with its arguments",
                                   self.peek().pos)
            return S.Spawn(tok.pos, name.text, self.positional_args(name.text))
        if tok.kind == "self":
            self.advance()
            return S.SelfRef(tok.pos)
        if tok.kind == "narrow":
            self.advance()
            if not self.at("("):
                raise CompileError("write `narrow(reference, Interface)`", self.peek().pos)
            self.advance()
            value = self.expr()
            self.expect(",", "`,` and the interface")
            itok = self.expect("ident", "an interface name")
            self.expect(")")
            return S.Narrow(tok.pos, value, itok.text, itok.pos)
        if tok.kind == "consume":
            self.advance()
            if not self.at("("):
                raise CompileError("write `consume(variable)`", self.peek().pos)
            self.advance()
            value = self.expr()
            self.expect(")")
            return S.Consume(tok.pos, value)
        if tok.kind == "copy":
            self.advance()
            if not self.at("("):
                raise CompileError("write `copy(value)`", self.peek().pos)
            self.advance()
            value = self.expr()
            self.expect(")")
            return S.Copy(tok.pos, value)
        if tok.kind == "." and self.peek(1).kind == "ident":
            # `.field` or `.method(...)` inside a method: the method's own struct.
            self.advance()
            name = self.advance()
            if self.at("("):
                return S.MethodCall(name.pos, None, name.text, self.positional_args(name.text))
            return S.FieldAccess(name.pos, None, name.text)
        if tok.kind == ".":
            if self.peek(1).kind == "copy":
                raise CompileError("copying is written `copy(value)`, not as a method",
                                   self.peek(1).pos)
            self.advance()
            return S.SelfValue(tok.pos)         # `.` alone: the method's whole value
        if tok.kind == "ident":
            self.advance()
            if self.at("("):
                if tok.text[0].isupper():
                    return S.StructLit(tok.pos, tok.text, self.field_args(tok.text))
                return S.Call(tok.pos, tok.text, self.positional_args(tok.text))
            return S.Name(tok.pos, tok.text)
        if tok.kind == "(":
            self.advance()
            inner = self.expr()
            self.expect(")")
            inner.parens = True
            return inner
        raise CompileError(f"expected an expression, found {describe_tok(tok)}", tok.pos)

    def postfix(self, expr):
        """`expr.field` and `expr.method(args)`, chained."""
        while self.at(".") and self.peek(1).kind in ("ident", "copy"):
            if self.peek(1).kind == "copy":
                raise CompileError(f"copying is written `copy(value)`, not as a method",
                                   self.peek(1).pos)
            self.advance()
            name = self.advance()
            if self.at("("):
                expr = S.MethodCall(name.pos, expr, name.text, self.positional_args(name.text))
            else:
                expr = S.FieldAccess(name.pos, expr, name.text)
        return expr

    def positional_args(self, callee):
        """`(a, mut b, c)` for a function or method call."""
        self.expect("(")
        args = []
        while not self.at(")"):
            if self.at("ident") and self.peek(1).kind == ":":
                raise CompileError(
                    f"`{callee}` is a function: its arguments aren't named", self.peek().pos)
            if self.at("mut"):
                tok = self.advance()
                args.append(S.MutArg(tok.pos, self.expr()))
            else:
                args.append(self.expr())
            if not self.at(")"):
                self.expect(",", "`,` or `)`")
        self.expect(")")
        return args

    def field_args(self, type_name):
        """`(x: 1, y: 2)` for a struct value: every field named."""
        self.expect("(")
        fields = []
        while not self.at(")"):
            if not (self.at("ident") and self.peek(1).kind == ":"):
                raise CompileError(
                    f"fields of `{type_name}` are named, as in `{type_name}(x: 1, y: 2)`",
                    self.peek().pos)
            ftok = self.advance()
            self.advance()
            fields.append((ftok.text, self.expr(), ftok.pos))
            if not self.at(")"):
                self.expect(",", "`,` or `)`")
        self.expect(")")
        return fields

    def if_expr(self):
        """`if c { a } else { b }` as a value."""
        start = self.expect("if")
        cond = self.expr()
        then = self.branch_value()
        save = self.i
        self.skip_newlines()
        if not self.at("else"):
            self.i = save
            raise CompileError("an `if` used as a value needs an `else`", start.pos)
        self.advance()
        else_ = self.if_expr() if self.at("if") else self.branch_value()
        return S.IfExpr(start.pos, cond, then, else_)

    def branch_value(self):
        self.expect("{")
        self.skip_newlines()
        value = self.expr()
        self.skip_newlines()
        if not self.at("}"):
            raise CompileError("each branch of an `if` used as a value must be a single expression",
                               self.peek().pos)
        self.advance()
        return value


def require_brackets(e):
    """Reject operator mixes whose meaning readers would disagree about."""
    def unbracketed(child, ops):
        return isinstance(child, S.Binary) and child.op in ops and not child.parens

    for child in (e.left, e.right):
        if e.op in ("or", "xor"):
            other = {"or", "xor"} - {e.op}
            if unbracketed(child, other):
                raise CompileError("mixing `or` and `xor` needs brackets to show the order", e.pos)
            if unbracketed(child, {"and"}):
                raise CompileError(
                    f"`and` inside `{e.op}` needs brackets to show the order, "
                    f"as in `a {e.op} (b and c)`", e.pos)
        if e.op in ARITH_OPS and unbracketed(child, SHIFT_OPS):
            raise CompileError(
                f"mixing `{child.op}` with `{e.op}` needs brackets to show the order", e.pos)
        if e.op in SHIFT_OPS and unbracketed(child, ARITH_OPS):
            raise CompileError(
                f"mixing `{e.op}` with `{child.op}` needs brackets to show the order", e.pos)


def describe(kind):
    return {"ident": "a name", "newline": "end of line", "eof": "end of file",
            "int": "a number"}.get(kind, f"`{kind}`")


def describe_tok(tok):
    if tok.kind in ("ident", "int", "char", "str"):
        return f"`{tok.text}`"
    return describe(tok.kind)
