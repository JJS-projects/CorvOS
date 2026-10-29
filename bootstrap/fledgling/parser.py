"""Parser: tokens -> syntax tree (recursive descent).

Operator precedence, loosest first:
    or  xor
    and
    not                         (prefix)
    ==  !=  <  <=  >  >=        (non-associative)
    +  -
    *  /  %  shl  shr
    -                           (prefix)
    as
`and`, `or`, `xor` and `not` are logical on bool and bitwise on integers.
Because `and` is looser than comparisons, `x and MASK != 0` means
`x and (MASK != 0)`, which the checker rejects; write `(x and MASK) != 0`.

Blocks are closed by `}`. A named closer, `} name`, closes every open block
back to and including the one called `name`. Only names written in the code can
be used: a function's name, or a loop's label. Every loop is labelled
(`rows: loop ...`), and `break` and `continue` name the loop they act on.
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
            if self.at("fn"):
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
                    f"expected `fn`, `extern fn` or a constant at top level, found {describe_tok(tok)}",
                    tok.pos)
            self.skip_newlines()
        return prog

    def fn_decl(self, extern, public):
        start = self.expect("fn")
        name = self.expect("ident", "function name").text
        self.expect("(")
        params = []
        while not self.at(")"):
            ptok = self.expect("ident", "parameter name")
            self.expect(":")
            params.append(S.Param(ptok.text, self.type_ref(), ptok.pos))
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
        return S.FnDecl(name, params, ret, body, start.pos, public)

    def const_decl(self, public):
        name = self.expect("ident")
        self.expect(":")
        ty = self.type_ref()
        self.expect("=", "`=` and a value")
        value = self.expr()
        self.end_statement()
        return S.ConstDecl(name.text, ty, value, name.pos, public)

    def type_ref(self):
        tok = self.expect("ident", "a type")
        return S.TypeRef(tok.text, tok.pos)

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
                name_tok = self.peek(1)
                if name_tok.kind in ("loop", "if"):
                    raise CompileError(
                        f"a closer names a function or a loop label, not `{name_tok.kind}`",
                        name_tok.pos)
                if name_tok.kind not in CLOSER_NAMES:
                    self.advance()                  # plain `}`
                    break
                name = name_tok.text
                if name in labels:
                    self.advance()                  # `} name` closing this block
                    self.advance()
                    break
                if any(name in outer for outer in self.open_blocks[:-1]):
                    break                           # closes an outer block too
                raise CompileError(f"`}} {name}` doesn't match any open block", name_tok.pos)
            if self.at("eof"):
                raise CompileError("unclosed `{`", start.pos)
            stmts.append(self.statement())
        self.open_blocks.pop()
        return S.Block(stmts, start.pos)

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
            if not isinstance(expr, S.Name):
                raise CompileError("can only assign to a variable", expr.pos)
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

    def loop_stmt(self, label, pos):
        self.expect("loop")
        if self.at("{"):
            return S.Loop(pos, label, None, self.block({label}))
        if self.at("ident") and self.peek(1).kind in ("in", ":"):
            var = self.advance().text
            ty = None
            if self.at(":"):
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
        return left

    def not_expr(self, level):
        if self.at("not"):
            tok = self.advance()
            return S.Unary(tok.pos, "not", self.not_expr(level))
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
        expr = self.primary()
        while self.at("as"):
            tok = self.advance()
            expr = S.Cast(tok.pos, expr, self.type_ref())
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
        if tok.kind == "if":
            return self.if_expr()
        if tok.kind == "ident":
            self.advance()
            if self.at("("):
                self.advance()
                args = []
                while not self.at(")"):
                    args.append(self.expr())
                    if not self.at(")"):
                        self.expect(",", "`,` or `)`")
                self.expect(")")
                return S.Call(tok.pos, tok.text, args)
            return S.Name(tok.pos, tok.text)
        if tok.kind == "(":
            self.advance()
            inner = self.expr()
            self.expect(")")
            return inner
        raise CompileError(f"expected an expression, found {describe_tok(tok)}", tok.pos)

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


def describe(kind):
    return {"ident": "a name", "newline": "end of line", "eof": "end of file",
            "int": "a number"}.get(kind, f"`{kind}`")


def describe_tok(tok):
    if tok.kind in ("ident", "int", "char"):
        return f"`{tok.text}`"
    return describe(tok.kind)
