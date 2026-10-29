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


# ---- expressions -------------------------------------------------------

@dataclass
class Expr:
    pos: Pos

    def __post_init__(self):
        self.ty = None          # set by checker


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
    op: str                     # "-" or "!"
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


@dataclass
class Cast(Expr):
    expr: Expr
    target: TypeRef


# ---- statements --------------------------------------------------------

@dataclass
class Stmt:
    pos: Pos


@dataclass
class Let(Stmt):
    name: str
    mutable: bool
    type: Optional[TypeRef]
    init: Expr

    def __post_init__(self):
        self.slot = None        # set by checker


@dataclass
class Assign(Stmt):
    target: Name
    op: str                     # "=", "+=", ...
    value: Expr                 # checker rewrites `x += e` to `x = x + e`


@dataclass
class ExprStmt(Stmt):
    expr: Expr


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
class While(Stmt):
    cond: Expr
    body: Block


@dataclass
class Break(Stmt):
    pass


@dataclass
class Continue(Stmt):
    pass


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

    def __post_init__(self):
        self.nslots = 0         # set by checker: number of 8-byte local slots


@dataclass
class ConstDecl:
    name: str
    type: TypeRef
    value: Expr
    pos: Pos


@dataclass
class Program:
    items: list = field(default_factory=list)
