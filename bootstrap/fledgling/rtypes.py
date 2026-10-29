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


BOOL = SimpleType("bool")
UNIT = SimpleType("()")

INT_TYPES = {
    t.name: t for t in [
        IntType("u8", 8, False), IntType("u16", 16, False),
        IntType("u32", 32, False), IntType("u64", 64, False),
        IntType("i8", 8, True), IntType("i16", 16, True),
        IntType("i32", 32, True), IntType("i64", 64, True),
    ]
}
U8 = INT_TYPES["u8"]
U64 = INT_TYPES["u64"]

NAMED_TYPES = dict(INT_TYPES, bool=BOOL)


def is_int(ty):
    return isinstance(ty, IntType)
