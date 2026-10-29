"""Parser: tokens -> syntax tree (recursive descent).

Operator precedence, loosest first (same as Rust):
    ||
    &&
    == != < <= > >=      (non-associative)
    |
    ^
    &
    << >>
    + -
    * / %
    unary - !
    as
"""

from .errors import CompileError
from . import syntax as S

BINARY_LEVELS = [
    ["||"],
    ["&&"],
    ["==", "!=", "<", "<=", ">", ">="],
    ["|"],
    ["^"],
    ["&"],
    ["<<", ">>"],
    ["+", "-"],
    ["*", "/", "%"],
]
COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}


def parse(tokens):
    return Parser(tokens).program()


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

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
            if self.at("fn"):
                prog.items.append(self.fn_decl(extern=False))
            elif self.at("extern"):
                self.advance()
                prog.items.append(self.fn_decl(extern=True))
            elif self.at("const"):
                prog.items.append(self.const_decl())
            else:
                tok = self.peek()
                raise CompileError(
                    f"expected `fn`, `extern fn` or `const` at top level, found {describe_tok(tok)}",
                    tok.pos)
            self.skip_newlines()
        return prog

    def fn_decl(self, extern):
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
        if self.at(":"):
            self.advance()
            ret = self.type_ref()
        body = None
        if extern:
            if self.at("{"):
                raise CompileError("`extern fn` declares a function written elsewhere and has no body",
                                   self.peek().pos)
            self.end_statement()
        else:
            body = self.block()
        return S.FnDecl(name, params, ret, body, start.pos)

    def const_decl(self):
        start = self.expect("const")
        name = self.expect("ident", "constant name").text
        self.expect(":", "`:` and a type (constants must be typed)")
        ty = self.type_ref()
        self.expect("=")
        value = self.expr()
        self.end_statement()
        return S.ConstDecl(name, ty, value, start.pos)

    def type_ref(self):
        tok = self.expect("ident", "a type")
        return S.TypeRef(tok.text, tok.pos)

    # ---- statements ----------------------------------------------------

    def block(self):
        start = self.expect("{")
        stmts = []
        self.skip_newlines()
        while not self.at("}"):
            if self.at("eof"):
                raise CompileError("unclosed `{`", start.pos)
            stmts.append(self.statement())
            self.skip_newlines()
        self.expect("}")
        return S.Block(stmts, start.pos)

    def statement(self):
        tok = self.peek()
        if tok.kind in ("let", "var"):
            self.advance()
            name = self.expect("ident", "variable name").text
            ty = None
            if self.at(":"):
                self.advance()
                ty = self.type_ref()
            self.expect("=", "`=` (variables must be initialised)")
            init = self.expr()
            self.end_statement()
            return S.Let(tok.pos, name, tok.kind == "var", ty, init)
        if tok.kind == "if":
            stmt = self.if_stmt()
            self.end_statement()
            return stmt
        if tok.kind == "while":
            self.advance()
            cond = self.expr()
            body = self.block()
            self.end_statement()
            return S.While(tok.pos, cond, body)
        if tok.kind == "break":
            self.advance()
            self.end_statement()
            return S.Break(tok.pos)
        if tok.kind == "continue":
            self.advance()
            self.end_statement()
            return S.Continue(tok.pos)
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
        then = self.block()
        else_ = None
        # Allow `else` on the line after the closing `}`.
        save = self.i
        self.skip_newlines()
        if self.at("else"):
            self.advance()
            else_ = self.if_stmt() if self.at("if") else self.block()
        else:
            self.i = save
        return S.If(start.pos, cond, then, else_)

    # ---- expressions ---------------------------------------------------

    def expr(self):
        return self.binary(0)

    def binary(self, level):
        if level == len(BINARY_LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        ops = BINARY_LEVELS[level]
        while self.peek().kind in ops:
            op = self.advance()
            right = self.binary(level + 1)
            if op.kind in COMPARISONS and self.peek().kind in COMPARISONS:
                raise CompileError("comparisons can't be chained; use `&&`", self.peek().pos)
            left = S.Binary(op.pos, op.kind, left, right)
        return left

    def unary(self):
        tok = self.peek()
        if tok.kind in ("-", "!"):
            self.advance()
            operand = self.unary()
            # Fold `-literal` so that e.g. -128 is a valid I8 literal.
            if tok.kind == "-" and isinstance(operand, S.IntLit) and not operand.is_char:
                return S.IntLit(tok.pos, -operand.value)
            return S.Unary(tok.pos, tok.kind, operand)
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


def describe(kind):
    return {"ident": "a name", "newline": "end of line", "eof": "end of file",
            "int": "a number"}.get(kind, f"`{kind}`")


def describe_tok(tok):
    if tok.kind == "ident":
        return f"`{tok.text}`"
    if tok.kind in ("int", "char"):
        return f"`{tok.text}`"
    return describe(tok.kind)
