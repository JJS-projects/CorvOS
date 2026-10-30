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


class AggregateType:
    """A struct or enum: lives in memory, is lent when passed, and is compared
    by identity, so two declarations are never the same type."""

    def __init__(self, name, decl):
        self.name = name
        self.decl = decl
        self.methods = {}       # name -> method signature
        self._words = None

    @property
    def words(self):
        """Size in 8-byte words. Every scalar field takes one word."""
        return self._words

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"{type(self).__name__}({self.name})"


class StructType(AggregateType):
    def __init__(self, name, decl):
        super().__init__(name, decl)
        self.fields = []        # [(name, type)], filled in once all types are known
        self.public_fields = set()

    def offset(self, field):
        """Offset of `field` in words, and its type."""
        return field_offset(self.fields, field)


class Variant:
    def __init__(self, name, tag, enum, pos):
        self.name, self.tag, self.enum, self.pos = name, tag, enum, pos
        self.fields = []        # [(name, type)]

    def offset(self, field):
        """Offset of `field` in words from the start of the enum value: the
        tag comes first."""
        at, ty = field_offset(self.fields, field)
        return at + 1, ty


class EnumType(AggregateType):
    """An enum value is one tag word (the variant's index) followed by the
    variant's fields, padded to the size of the largest variant."""

    def __init__(self, name, decl):
        super().__init__(name, decl)
        self.variants = {}      # name -> Variant, in declaration order


def field_offset(fields, field):
    at = 0
    for name, ty in fields:
        if name == field:
            return at, ty
        at += words(ty)
    raise KeyError(field)


def words(ty):
    return ty.words if isinstance(ty, AggregateType) else 1


def is_aggregate(ty):
    return isinstance(ty, AggregateType)


def is_struct(ty):
    return isinstance(ty, StructType)


def is_enum(ty):
    return isinstance(ty, EnumType)


def is_int(ty):
    return isinstance(ty, IntType)


def lossless(src, dst):
    """Can every value of `src` be represented in `dst`?"""
    if src == BOOL:
        return is_int(dst)
    return is_int(src) and is_int(dst) and dst.min <= src.min and src.max <= dst.max
