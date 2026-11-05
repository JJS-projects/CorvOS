"""Plain-text dumps of the compiler's stages, in the formats the self-hosted compiler must match
(docs/self-hosting.md)."""

from .errors import CompileError
from .lexer import lex


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
