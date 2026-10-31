"""Lexer: source text -> tokens.

Rook has no semicolons. A newline ends a statement, so the lexer emits NEWLINE
tokens, except where a statement obviously continues:
  - inside ( ) or [ ], unless a { } block is open inside them (a `match`
    passed as an argument has one arm per line)
  - after a token that can't end an expression (an operator, `=`, `,`, `as`,
    an opening bracket)
"""

from dataclasses import dataclass

from .errors import CompileError, Pos

KEYWORDS = {
    "fn", "extern", "pub", "mut", "if", "else", "loop", "in", "break",
    "continue", "return", "true", "false", "as", "wrap", "discard",
    "and", "or", "xor", "not", "shl", "shr", "struct", "copy", "enum", "match",
    "unsafe", "actor", "on", "spawn", "self", "lossy", "reliable",
}

# Reserved for later milestones: using them now is an error, so programs
# written today keep working when the features arrive.
RESERVED = {
    "interface", "consume", "import", "freeze", "narrow",
}

# Words from other languages that Rook deliberately doesn't use, with a hint.
NOT_ROOK = {
    "let": "Rook has no `let`: declare with `name: type = value`",
    "var": "Rook has no `var`: declare with `mut name: type = value`",
    "const": "Rook has no `const`: a top-level `NAME: type = value` is a constant",
    "while": "Rook has no `while`: use `loop condition { ... }`",
    "for": "Rook has no `for`: use `loop i in start..end { ... }`",
}

# Longest first, so `..` wins over `.` and `->` over `-`.
PUNCT = [
    "==", "!=", "<=", ">=", "->", "=>", "..",
    "+=", "-=", "*=", "/=", "%=",
    "+", "-", "*", "/", "%", "<", ">", "=",
    "(", ")", "{", "}", "[", "]", ",", ":", ".",
]

# After these, a newline does not end the statement.
CONTINUES = {
    "+", "-", "*", "/", "%", "<", ">", "=", "==", "!=", "<=", ">=",
    "+=", "-=", "*=", "/=", "%=", "->", "=>", "..",
    "(", "[", ",", ":", ".",
    "as", "wrap", "and", "or", "xor", "not", "shl", "shr",
}

# Symbols from C-family languages, with what Rook uses instead.
SYMBOL_HINTS = {
    "&&": "and", "||": "or", "!": "not", "&": "and", "|": "or", "^": "xor",
    "~": "not", "<<": "shl",
}

ESCAPES = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, "'": 39, '"': 34}


def escape(source, j, pos):
    """Read the escape starting with the backslash at `j`. Returns (byte, next j)."""
    if j + 1 >= len(source):
        raise CompileError("unterminated escape", pos)
    esc = source[j + 1]
    if esc == "x":
        try:
            return int(source[j + 2:j + 4], 16), j + 4
        except ValueError:
            raise CompileError("invalid `\\x` escape (needs two hex digits)", pos)
    if esc in ESCAPES:
        return ESCAPES[esc], j + 2
    raise CompileError(f"unknown escape `\\{esc}`", pos)


@dataclass
class Token:
    kind: str      # "ident", "int", "char", "newline", "eof", or the keyword/punct text
    text: str
    value: object
    pos: Pos


def lex(source, file=None):
    tokens = []
    i, line, col = 0, 1, 1
    brackets = []  # open brackets; newlines are ignored when the innermost is ( or [

    def emit(kind, text, value, pos):
        tokens.append(Token(kind, text, value, pos))

    while i < len(source):
        c = source[i]
        pos = Pos(line, col, file)

        if c == "\n":
            prev = tokens[-1].kind if tokens else "newline"
            inside = brackets and brackets[-1] in "(["
            if not inside and prev != "newline" and prev not in CONTINUES:
                emit("newline", "\\n", None, pos)
            i, line, col = i + 1, line + 1, 1
            continue
        if c in " \r":
            i, col = i + 1, col + 1
            continue
        if c == "\t":
            raise CompileError("tabs aren't allowed; indent with spaces", pos)
        if source.startswith("//", i):
            while i < len(source) and source[i] != "\n":
                i += 1
            continue

        if c.isalpha() or c == "_":
            j = i
            while j < len(source) and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            if word in RESERVED:
                raise CompileError(
                    f"`{word}` is reserved for a later milestone and not supported by Fledgling yet",
                    pos)
            if word in NOT_ROOK:
                raise CompileError(NOT_ROOK[word], pos)
            emit(word if word in KEYWORDS else "ident", word, None, pos)
            col += j - i
            i = j
            continue

        if c.isdigit():
            j = i
            base = 10
            if source.startswith(("0x", "0X"), i):
                base, j = 16, i + 2
            elif source.startswith(("0b", "0B"), i):
                base, j = 2, i + 2
            start = j
            while j < len(source) and (source[j].isalnum() or source[j] == "_"):
                j += 1
            digits = source[start:j].replace("_", "")
            try:
                value = int(digits, base)
            except ValueError:
                raise CompileError(f"invalid number `{source[i:j]}`", pos)
            emit("int", source[i:j], value, pos)
            col += j - i
            i = j
            continue

        if c == '"':
            # A byte string: UTF-8 bytes, with the same escapes as characters.
            j = i + 1
            data = bytearray()
            while True:
                if j >= len(source) or source[j] == "\n":
                    raise CompileError("unterminated string literal", pos)
                if source[j] == '"':
                    j += 1
                    break
                if source[j] == "\\":
                    value, j = escape(source, j, pos)
                    data.append(value)
                else:
                    data += source[j].encode("utf-8")
                    j += 1
            emit("str", source[i:j], bytes(data), pos)
            col += j - i
            i = j
            continue

        if c == "'":
            j = i + 1
            if j < len(source) and source[j] == "\\":
                value, j = escape(source, j, pos)
            else:
                if j >= len(source) or source[j] in "'\n":
                    raise CompileError("empty character literal", pos)
                value = ord(source[j])
                if value > 127:
                    raise CompileError("character literals must be ASCII", pos)
                j += 1
            if j >= len(source) or source[j] != "'":
                raise CompileError("unterminated character literal", pos)
            j += 1
            emit("char", source[i:j], value, pos)
            col += j - i
            i = j
            continue

        for sym in ("&&", "||", "<<"):
            if source.startswith(sym, i):
                raise CompileError(f"Rook writes `{sym}` as `{SYMBOL_HINTS[sym]}`", pos)

        for p in PUNCT:
            if source.startswith(p, i):
                if p in ("(", "[", "{"):
                    brackets.append(p)
                elif p in (")", "]", "}") and brackets:
                    brackets.pop()
                emit(p, p, None, pos)
                i += len(p)
                col += len(p)
                break
        else:
            for sym in ("!", "&", "|", "^", "~"):
                if source.startswith(sym, i):
                    raise CompileError(f"Rook writes `{sym}` as `{SYMBOL_HINTS[sym]}`", pos)
            raise CompileError(f"unexpected character `{c}`", pos)

    if tokens and tokens[-1].kind != "newline":
        emit("newline", "\\n", None, Pos(line, col))
    emit("eof", "", None, Pos(line, col))
    return tokens
