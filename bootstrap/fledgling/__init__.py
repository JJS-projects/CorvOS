"""Fledgling: the throwaway bootstrap compiler for Rook.

Pipeline: source -> lexer -> parser -> checker -> codegen -> GNU AArch64 assembly.
"""

from .errors import CompileError
from .lexer import lex
from .parser import parse
from .checker import check
from .codegen import generate


def compile_source(source, filename="<input>"):
    """Compile Rook source text to AArch64 assembly text.

    Raises CompileError on the first error found.
    """
    tokens = lex(source)
    program = parse(tokens)
    check(program)
    return generate(program, filename)
