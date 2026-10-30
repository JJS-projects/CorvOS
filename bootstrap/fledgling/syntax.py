"""Syntax tree for the Fledgling subset of Rook.

The checker fills in the fields marked "set by checker".
"""

from dataclasses import dataclass, field
from typing import Optional

from .errors import Pos


@dataclass
class TypeRef:
    name: str
    pos: Pos
    mut: bool = False           # `mut T` on a parameter: lent so it can be changed


# ---- expressions -------------------------------------------------------

@dataclass
class Expr:
    pos: Pos

    def __post_init__(self):
        self.ty = None          # set by checker
        self.parens = False     # set by parser: written inside ( )


@dataclass
class IntLit(Expr):
    value: int
    is_char: bool = False


@dataclass
class BoolLit(Expr):
    value: bool


@dataclass
class StrLit(Expr):
    """`"text"`: a `Bytes` value whose bytes live in read-only data."""
    data: bytes


@dataclass
class Name(Expr):
    ident: str

    def __post_init__(self):
        super().__post_init__()
        self.slot = None        # set by checker: local variable slot
        self.const_value = None  # set by checker: value if this names a const
        self.variant = None     # set by checker: if this is a variant without fields
        self.temp = None        # set by checker: frame slot for that variant's value
        self.const_str = None   # set by checker: the StrLit, if this names a text constant


@dataclass
class Unary(Expr):
    op: str                     # "-" or "not"
    operand: Expr


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Call(Expr):
    callee: str
    args: list

    def __post_init__(self):
        super().__post_init__()
        self.temp = None        # set by checker: frame slot for a struct result


@dataclass
class Cast(Expr):
    """`x as T` (never loses information) or `x wrap T` (keeps the low bits)."""
    expr: Expr
    target: TypeRef
    kind: str = "as"


@dataclass
class StructLit(Expr):
    """`Point(x: 1, y: 2)` or `Busy(job: 3)`: every field named, in declaration order."""
    name: str
    fields: list                # [(field name, Expr, Pos)]

    def __post_init__(self):
        super().__post_init__()
        self.temp = None        # set by checker: frame slot of the new value
        self.variant = None     # set by checker: the Variant, for an enum value


@dataclass
class FieldAccess(Expr):
    """`p.x`, or `.x` inside a method (obj is None: the method's own struct)."""
    obj: Optional[Expr]
    field: str


@dataclass
class MethodCall(Expr):
    """`p.m(args)`, or `.m(args)` inside a method."""
    obj: Optional[Expr]
    name: str
    args: list

    def __post_init__(self):
        super().__post_init__()
        self.temp = None        # set by checker: frame slot for a struct result
        self.sig = None         # set by checker


@dataclass
class Copy(Expr):
    """`copy(value)`: a new struct with the same fields."""
    value: Expr

    def __post_init__(self):
        super().__post_init__()
        self.temp = None        # set by checker: frame slot for the copy


@dataclass
class SelfValue(Expr):
    """`.` on its own inside a method: the method's whole struct or enum."""


@dataclass
class Pattern:
    """One pattern in a `match` arm. A variant pattern has `name` (and
    `bindings` if written with brackets); a value pattern has `value` (and
    `end` for a range `value..end`)."""
    pos: Pos
    name: Optional[str] = None
    bindings: Optional[list] = None     # [(field name, binding name or "_", Pos)]
    value: Optional[Expr] = None
    end: Optional[Expr] = None

    def __post_init__(self):
        self.variant = None     # set by checker: the Variant matched
        self.slots = []         # set by checker: [(binding slot, field offset, is_aggregate)]
        self.lo = None          # set by checker: the values matched are lo <= v < hi
        self.hi = None


@dataclass
class Arm:
    """`patterns => body`. `patterns` is None for `else`. `body` is an Expr in
    a match used as a value (or a Return/Break/Continue that leaves it), and a
    Block or a single statement otherwise."""
    pos: Pos
    patterns: Optional[list]
    body: object


@dataclass
class MatchExpr(Expr):
    """`match value { arms }` used as a value."""
    subject: Expr
    arms: list

    def __post_init__(self):
        super().__post_init__()
        self.subject_slot = None    # set by checker: the subject's value or address


@dataclass
class MutArg(Expr):
    """`mut place` passed to a `mut` parameter."""
    place: Expr


@dataclass
class IfExpr(Expr):
    """`if c { a } else { b }` used as a value: each branch is one expression."""
    cond: Expr
    then: Expr
    else_: Expr


# ---- statements --------------------------------------------------------

@dataclass
class Stmt:
    pos: Pos


@dataclass
class Decl(Stmt):
    """`name: type = value` or `mut name: type = value`."""
    name: str
    mutable: bool
    type: TypeRef
    init: Expr

    def __post_init__(self):
        self.slot = None        # set by checker


@dataclass
class Assign(Stmt):
    target: Expr                # a place: Name, FieldAccess or SelfValue
    op: str                     # "=", "+=", ...
    value: Expr                 # checker rewrites `x += e` to `x = x + e`


@dataclass
class ExprStmt(Stmt):
    expr: Expr
    discard: bool = False       # `discard f()`: the value is thrown away on purpose


@dataclass
class Block:
    stmts: list
    pos: Pos


@dataclass
class If(Stmt):
    cond: Expr
    then: Block
    else_: object = None        # Block, If, or None


@dataclass
class Loop(Stmt):
    """`label: loop { }` (cond is None) or `label: loop cond { }`."""
    label: str
    cond: Optional[Expr]
    body: Block


@dataclass
class ForRange(Stmt):
    """`label: loop i in start..end { }`: i goes from start up to, not including, end."""
    label: str
    var: str
    type: TypeRef
    start: Expr
    end: Expr
    body: Block

    def __post_init__(self):
        self.slot = None        # set by checker: the loop variable
        self.end_slot = None    # set by checker: the end value, evaluated once
        self.ty = None          # set by checker


@dataclass
class Match(Stmt):
    """`match value { arms }` as a statement."""
    subject: Expr
    arms: list

    def __post_init__(self):
        self.subject_slot = None    # set by checker


@dataclass
class Unsafe(Stmt):
    """`unsafe { ... }`: code the compiler can't check. Only in trusted files."""
    body: "Block"


@dataclass
class Break(Stmt):
    label: str


@dataclass
class Continue(Stmt):
    label: str


@dataclass
class Return(Stmt):
    value: Optional[Expr]


# ---- top level ---------------------------------------------------------

@dataclass
class Param:
    name: str
    type: TypeRef
    pos: Pos

    def __post_init__(self):
        self.slot = None        # set by checker
        self.ty = None          # set by checker


@dataclass
class FnDecl:
    name: str
    params: list
    ret: Optional[TypeRef]
    body: Optional[Block]       # None for `extern fn`
    pos: Pos
    public: bool = False
    mutating: bool = False      # `mut fn`: a method that may change its struct or enum
    owner: Optional[str] = None  # the struct or enum a method belongs to

    def __post_init__(self):
        self.file = None        # set by compile_program: the source file
        self.trusted = False    # set by compile_program: in a trusted directory
        self.nslots = 0         # set by checker: number of 8-byte local slots
        self.self_slot = None   # set by checker: methods' pointer to their struct
        self.result_slot = None  # set by checker: where a struct result goes (x8)

    @property
    def symbol(self):
        return f"{self.owner}__{self.name}" if self.owner else self.name


@dataclass
class ConstDecl:
    """A top-level `NAME: type = value`."""
    name: str
    type: TypeRef
    value: Expr
    pos: Pos
    public: bool = False

    def __post_init__(self):
        self.file = None
        self.trusted = False


@dataclass
class Field:
    name: str
    type: TypeRef
    pos: Pos
    public: bool = False        # `pub`: usable outside the struct's file


@dataclass
class StructDecl:
    name: str
    fields: list                # [Field]
    methods: list               # [FnDecl]
    pos: Pos
    public: bool = False

    def __post_init__(self):
        self.file = None
        self.trusted = False


@dataclass
class VariantDecl:
    name: str
    fields: Optional[list]      # [Field], or None for a variant written without brackets
    pos: Pos


@dataclass
class EnumDecl:
    name: str
    variants: list              # [VariantDecl]
    methods: list               # [FnDecl]
    pos: Pos
    public: bool = False

    def __post_init__(self):
        self.file = None
        self.trusted = False


@dataclass
class Program:
    items: list = field(default_factory=list)
