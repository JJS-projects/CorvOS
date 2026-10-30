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
class Name(Expr):
    ident: str

    def __post_init__(self):
        super().__post_init__()
        self.slot = None        # set by checker: local variable slot
        self.const_value = None  # set by checker: value if this names a const


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
    """`Point(x: 1, y: 2)`: every field named, in declaration order."""
    name: str
    fields: list                # [(field name, Expr, Pos)]

    def __post_init__(self):
        super().__post_init__()
        self.temp = None        # set by checker: frame slot of the new value


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
    target: Expr                # a place: Name or FieldAccess
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
    mutating: bool = False      # `mut fn`: a method that may change its struct
    owner: Optional[str] = None  # the struct a method belongs to

    def __post_init__(self):
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


@dataclass
class Field:
    name: str
    type: TypeRef
    pos: Pos


@dataclass
class StructDecl:
    name: str
    fields: list                # [Field]
    methods: list               # [FnDecl]
    pos: Pos
    public: bool = False


@dataclass
class Program:
    items: list = field(default_factory=list)
