"""Fledgling: the throwaway bootstrap compiler for Rook.

Pipeline: source -> lexer -> parser -> checker -> codegen -> GNU AArch64 assembly.

A program is one or more files compiled together. Until Rook has modules and
`import`, every `pub` item of every file is visible in all of them, and items
without `pub` are private to their file. Files under a trusted directory may
use `unsafe { }` and `extern fn`; nothing else may (decision 90).
"""

import os

from .errors import CompileError
from .lexer import lex
from .parser import parse
from .checker import check
from .codegen import generate
from . import syntax as S


def is_trusted(path, trusted_dirs):
    """Is `path` inside one of `trusted_dirs`?"""
    real = os.path.realpath(path)
    for d in trusted_dirs:
        root = os.path.realpath(d)
        if os.path.commonpath([real, root]) == root:
            return True
    return False


def compile_program(files, trusted_dirs=()):
    """Compile [(path, source text)] to one AArch64 assembly text.

    Raises CompileError on the first error found; its position names the file.
    """
    program = S.Program()
    for path, source in files:
        trusted = is_trusted(path, trusted_dirs)
        for item in parse(lex(source, path)).items:
            item.file, item.trusted = path, trusted
            program.items.append(item)
    check(program)
    return generate(program)


def compile_source(source, filename="<input>"):
    """Compile one untrusted file of Rook source text."""
    return compile_program([(filename, source)])
