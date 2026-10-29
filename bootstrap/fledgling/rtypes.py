"""Rook types known to Fledgling."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IntType:
    name: str
    bits: int
    signed: bool

    @property
    def min(self):
        return -(1 << (self.bits - 1)) if self.signed else 0

    @property
    def max(self):
        return (1 << (self.bits - 1)) - 1 if self.signed else (1 << self.bits) - 1

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class SimpleType:
    name: str

    def __str__(self):
        return self.name


BOOL = SimpleType("Bool")
UNIT = SimpleType("()")

INT_TYPES = {
    t.name: t for t in [
        IntType("U8", 8, False), IntType("U16", 16, False),
        IntType("U32", 32, False), IntType("U64", 64, False),
        IntType("I8", 8, True), IntType("I16", 16, True),
        IntType("I32", 32, True), IntType("I64", 64, True),
    ]
}
U8 = INT_TYPES["U8"]
U64 = INT_TYPES["U64"]

NAMED_TYPES = dict(INT_TYPES, Bool=BOOL)


def is_int(ty):
    return isinstance(ty, IntType)
