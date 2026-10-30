"""Compile errors with source positions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Pos:
    line: int
    col: int
    file: str = None            # the source file's path, when compiling several files


class CompileError(Exception):
    def __init__(self, message, pos):
        super().__init__(message)
        self.message = message
        self.pos = pos

    def format(self, filename, source=None):
        """Render as `file:line:col: error: message`, plus the source line.
        The position's own file, if it has one, wins over `filename`."""
        filename = self.pos.file or filename
        text = f"{filename}:{self.pos.line}:{self.pos.col}: error: {self.message}"
        if source is not None:
            lines = source.splitlines()
            if 1 <= self.pos.line <= len(lines):
                src = lines[self.pos.line - 1]
                text += f"\n    {src}\n    {' ' * (self.pos.col - 1)}^"
        return text
