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


class StructType:
    """A struct: compared by identity, so two structs are never the same type."""

    def __init__(self, name, decl):
        self.name = name
        self.decl = decl
        self.fields = []        # [(name, type)], filled in once all structs are known
        self.methods = {}       # name -> method signature
        self._words = None

    @property
    def words(self):
        """Size in 8-byte words. Every scalar field takes one word."""
        return self._words

    def offset(self, field):
        """Offset of `field` in words, and its type."""
        at = 0
        for name, ty in self.fields:
            if name == field:
                return at, ty
            at += words(ty)
        raise KeyError(field)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"StructType({self.name})"


def words(ty):
    return ty.words if isinstance(ty, StructType) else 1


def is_struct(ty):
    return isinstance(ty, StructType)


def is_int(ty):
    return isinstance(ty, IntType)


def lossless(src, dst):
    """Can every value of `src` be represented in `dst`?"""
    if src == BOOL:
        return is_int(dst)
    return is_int(src) and is_int(dst) and dst.min <= src.min and src.max <= dst.max
