"""Build a tree from the flat token stream.

The tokenizer settles *what* the pieces are; this settles how they nest. Checks 5
(circular references) and 7 (complexity ranking) and the dependency graph all need
structure rather than a sequence.

Excel's precedence has two traps that a naive implementation gets wrong, and both
are pinned by tests:

1. **Negation binds tighter than exponentiation.** ``=-2^2`` is ``4`` in Excel,
   not ``-4`` — it parses as ``(-2)^2``. Nearly every other language disagrees.
2. **Exponentiation is left-associative.** ``=2^3^2`` is ``64`` in Excel, not
   ``512`` — it parses as ``(2^3)^2``.

We never compute either value. But a parser that nests them the other way would
report the wrong depth for check 7 and the wrong precedents for the graph, so the
shape still has to be right.

A third trap is lexical rather than precedence: a space between two operands is
Excel's intersection operator, while a space anywhere else is formatting. See
``_is_intersection``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tickmark.formula.references import Reference, ReferenceParseError, parse_reference
from tickmark.formula.tokenizer import (
    FormulaSyntaxError,
    Token,
    TokenSubtype,
    TokenType,
    tokenize,
)

__all__ = [
    "ArrayLit",
    "BinaryOp",
    "ErrorLit",
    "FuncCall",
    "LogicalLit",
    "Node",
    "NumberLit",
    "ParseError",
    "PostfixOp",
    "RefNode",
    "TextLit",
    "UnaryOp",
    "depth",
    "parse",
    "walk",
]


class ParseError(ValueError):
    """Raised when a token stream cannot be assembled into a tree."""


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NumberLit:
    """A numeric literal. Check 2 hunts these when they sit inside a formula."""

    text: str

    @property
    def value(self) -> float:
        """The literal as a float.

        This reads the number the author wrote; it does not evaluate anything.
        Check 2 needs it to tell an innocuous ``*100`` from a suspicious
        ``*1.075``.
        """
        return float(self.text)


@dataclass(frozen=True, slots=True)
class TextLit:
    text: str

    @property
    def value(self) -> str:
        """Literal contents with Excel's doubled-quote escaping undone."""
        inner = self.text[1:-1] if len(self.text) >= 2 else ""
        return inner.replace('""', '"')


@dataclass(frozen=True, slots=True)
class LogicalLit:
    text: str

    @property
    def value(self) -> bool:
        return self.text.upper() == "TRUE"


@dataclass(frozen=True, slots=True)
class ErrorLit:
    """An error literal written into the formula, e.g. ``#REF!`` (check 3)."""

    text: str


@dataclass(frozen=True, slots=True)
class RefNode:
    """A reference, with its parsed form when it could be parsed.

    ``reference`` is ``None`` for text that is reference-shaped to the tokenizer
    but that :mod:`tickmark.formula.references` declined — the raw text is always
    kept so a check can still report it.
    """

    text: str
    reference: Reference | None = None


@dataclass(frozen=True, slots=True)
class FuncCall:
    name: str
    args: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class BinaryOp:
    op: str
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class UnaryOp:
    op: str
    operand: Node


@dataclass(frozen=True, slots=True)
class PostfixOp:
    op: str
    operand: Node


@dataclass(frozen=True, slots=True)
class ArrayLit:
    rows: tuple[tuple[Node, ...], ...] = field(default=())


Node = (
    NumberLit
    | TextLit
    | LogicalLit
    | ErrorLit
    | RefNode
    | FuncCall
    | BinaryOp
    | UnaryOp
    | PostfixOp
    | ArrayLit
)


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------

# Higher binds tighter. Ordering follows Excel's documented table; the two traps
# in the module docstring are what the gaps between 5, 6 and 7 encode.
_COMPARISON = 1
_CONCAT = 2
_ADDITIVE = 3
_MULTIPLICATIVE = 4
_POWER = 5
_UNARY = 6
_POSTFIX = 7
_UNION = 8
_INTERSECT = 9
_RANGE = 10

_BINARY_PRECEDENCE: dict[str, int] = {
    "=": _COMPARISON,
    "<>": _COMPARISON,
    "<": _COMPARISON,
    ">": _COMPARISON,
    "<=": _COMPARISON,
    ">=": _COMPARISON,
    "&": _CONCAT,
    "+": _ADDITIVE,
    "-": _ADDITIVE,
    "*": _MULTIPLICATIVE,
    "/": _MULTIPLICATIVE,
    "^": _POWER,
    ",": _UNION,
    ":": _RANGE,
}

_OPERAND_STARTERS = frozenset({TokenType.OPERAND, TokenType.FUNC, TokenType.PAREN, TokenType.ARRAY})


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0
        # What immediately encloses the expression being parsed. A ',' means
        # different things depending on it: an argument separator inside a
        # function call or array constant, and the union operator inside plain
        # parentheses. Precedence cannot express that difference — =IF(A1>0,1,0)
        # and =SUM((A1:A5,B1:B5)) both need full-precedence sub-expressions.
        self._context: list[str] = []

    @property
    def _comma_is_union(self) -> bool:
        return not self._context or self._context[-1] == "PAREN"

    # -- token helpers --

    def _peek(self, offset: int = 0) -> Token | None:
        idx = self._pos + offset
        return self._tokens[idx] if idx < len(self._tokens) else None

    def _next(self) -> Token:
        token = self._peek()
        if token is None:
            raise ParseError("unexpected end of formula")
        self._pos += 1
        return token

    def _skip_whitespace(self) -> None:
        while (t := self._peek()) is not None and t.type is TokenType.WHITESPACE:
            self._pos += 1

    def _is_intersection(self) -> bool:
        """True if whitespace at the cursor is Excel's intersection operator.

        A space is the intersection operator only between two operands. The same
        token type appears in ``=SUM( A1 , B1 )``, where it is formatting. The
        difference is entirely positional: look past the run of whitespace and
        see whether something that can begin an operand follows.
        """
        token = self._peek()
        if token is None or token.type is not TokenType.WHITESPACE:
            return False
        offset = 0
        while (t := self._peek(offset)) is not None and t.type is TokenType.WHITESPACE:
            offset += 1
        nxt = self._peek(offset)
        if nxt is None:
            return False
        if nxt.type is TokenType.PAREN and nxt.subtype is TokenSubtype.CLOSE:
            return False
        if nxt.type in (TokenType.FUNC, TokenType.ARRAY) and nxt.subtype is TokenSubtype.CLOSE:
            return False
        return nxt.type in _OPERAND_STARTERS or nxt.type is TokenType.OPERATOR_PREFIX

    # -- grammar --

    def parse(self) -> Node:
        node = self._parse_expr(0)
        self._skip_whitespace()
        if (leftover := self._peek()) is not None:
            raise ParseError(f"unexpected token {leftover.value!r} after end of expression")
        return node

    def _parse_expr(self, min_precedence: int) -> Node:
        left = self._parse_unary()

        while True:
            if self._is_intersection():
                if min_precedence > _INTERSECT:
                    break
                self._skip_whitespace()
                right = self._parse_expr(_INTERSECT + 1)
                left = BinaryOp(" ", left, right)
                continue

            self._skip_whitespace()
            token = self._peek()
            if token is None:
                break

            op = token.value
            if token.type is TokenType.OPERATOR_INFIX:
                precedence = _BINARY_PRECEDENCE.get(op)
            elif token.type is TokenType.SEP and token.subtype is TokenSubtype.ARG:
                if not self._comma_is_union:
                    break
                precedence = _UNION
            else:
                break

            if precedence is None or precedence < min_precedence:
                break

            self._pos += 1
            # Every Excel binary operator is left-associative, exponentiation
            # included: 2^3^2 is (2^3)^2. Hence precedence + 1.
            right = self._parse_expr(precedence + 1)
            left = BinaryOp(op, left, right)

        return left

    def _parse_unary(self) -> Node:
        self._skip_whitespace()
        token = self._peek()
        if token is not None and token.type is TokenType.OPERATOR_PREFIX:
            self._pos += 1
            # Negation binds tighter than '^': -2^2 is (-2)^2. Parsing the
            # operand at _POSTFIX rather than _UNARY is what produces that.
            operand = self._parse_unary()
            return self._parse_postfix(UnaryOp(token.value, operand))
        return self._parse_postfix(self._parse_primary())

    def _parse_postfix(self, node: Node) -> Node:
        while True:
            token = self._peek()
            if token is None or token.type is not TokenType.OPERATOR_POSTFIX:
                return node
            self._pos += 1
            node = PostfixOp(token.value, node)

    def _parse_primary(self) -> Node:
        self._skip_whitespace()
        token = self._next()

        if token.type is TokenType.OPERAND:
            return self._parse_operand(token)

        if token.type is TokenType.FUNC and token.subtype is TokenSubtype.OPEN:
            name = token.function_name or token.value
            args = self._parse_arguments(TokenType.FUNC)
            return FuncCall(name, args)

        if token.type is TokenType.PAREN and token.subtype is TokenSubtype.OPEN:
            self._context.append("PAREN")
            try:
                inner = self._parse_expr(0)
            finally:
                self._context.pop()
            self._skip_whitespace()
            closing = self._peek()
            if (
                closing is None
                or closing.type is not TokenType.PAREN
                or closing.subtype is not TokenSubtype.CLOSE
            ):
                raise ParseError("unbalanced '('")
            self._pos += 1
            # Grouping is carried by the tree itself, so no node is needed.
            return inner

        if token.type is TokenType.ARRAY and token.subtype is TokenSubtype.OPEN:
            return self._parse_array()

        raise ParseError(f"unexpected token {token.value!r}")

    def _parse_operand(self, token: Token) -> Node:
        match token.subtype:
            case TokenSubtype.NUMBER:
                return NumberLit(token.value)
            case TokenSubtype.TEXT:
                return TextLit(token.value)
            case TokenSubtype.LOGICAL:
                return LogicalLit(token.value)
            case TokenSubtype.ERROR:
                return ErrorLit(token.value)
            case TokenSubtype.RANGE:
                try:
                    return RefNode(token.value, parse_reference(token.value))
                except ReferenceParseError:
                    # Keep the raw text: a check can still report it, and
                    # refusing the whole formula over one odd reference would
                    # lose the other findings in the cell.
                    return RefNode(token.value, None)
            case _:
                raise ParseError(f"unsupported operand {token.value!r}")

    def _parse_arguments(self, closer: TokenType) -> tuple[Node, ...]:
        args: list[Node] = []
        self._context.append("FUNC")
        try:
            self._skip_whitespace()

            token = self._peek()
            if token is not None and token.type is closer and token.subtype is TokenSubtype.CLOSE:
                self._pos += 1
                return ()

            while True:
                self._skip_whitespace()
                token = self._peek()
                # An omitted argument, as in =IF(A1,,0). Excel allows it, and
                # dropping the gap would shift the indices of every argument
                # after it, so a finding would name the wrong one.
                if token is not None and token.type is TokenType.SEP:
                    args.append(RefNode("", None))
                else:
                    args.append(self._parse_expr(0))

                self._skip_whitespace()
                token = self._peek()
                if token is None:
                    raise ParseError("unterminated argument list")
                if token.type is closer and token.subtype is TokenSubtype.CLOSE:
                    self._pos += 1
                    return tuple(args)
                if token.type is TokenType.SEP and token.subtype is TokenSubtype.ARG:
                    self._pos += 1
                    continue
                raise ParseError(f"unexpected token {token.value!r} in argument list")
        finally:
            self._context.pop()

    def _parse_array(self) -> ArrayLit:
        rows: list[tuple[Node, ...]] = []
        current: list[Node] = []

        self._context.append("ARRAY")
        try:
            self._skip_whitespace()
            token = self._peek()
            if (
                token is not None
                and token.type is TokenType.ARRAY
                and token.subtype is TokenSubtype.CLOSE
            ):
                self._pos += 1
                return ArrayLit(())

            while True:
                self._skip_whitespace()
                current.append(self._parse_expr(0))
                self._skip_whitespace()

                token = self._peek()
                if token is None:
                    raise ParseError("unterminated array constant")
                if token.type is TokenType.ARRAY and token.subtype is TokenSubtype.CLOSE:
                    self._pos += 1
                    rows.append(tuple(current))
                    return ArrayLit(tuple(rows))
                if token.type is TokenType.SEP and token.subtype is TokenSubtype.ROW:
                    self._pos += 1
                    rows.append(tuple(current))
                    current = []
                    continue
                if token.type is TokenType.SEP and token.subtype is TokenSubtype.ARG:
                    self._pos += 1
                    continue
                raise ParseError(f"unexpected token {token.value!r} in array constant")
        finally:
            self._context.pop()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def parse(formula: str) -> Node:
    """Parse a formula into a tree.

    Raises:
        ParseError: if the formula cannot be tokenized or assembled.
    """
    try:
        tokens = tokenize(formula, keep_whitespace=True)
    except FormulaSyntaxError as exc:
        raise ParseError(str(exc)) from exc
    if not tokens:
        raise ParseError("empty formula")
    return _Parser(tokens).parse()


def walk(node: Node):
    """Yield every node in the tree, parents before children."""
    yield node
    match node:
        case BinaryOp(left=left, right=right):
            yield from walk(left)
            yield from walk(right)
        case UnaryOp(operand=operand) | PostfixOp(operand=operand):
            yield from walk(operand)
        case FuncCall(args=args):
            for arg in args:
                yield from walk(arg)
        case ArrayLit(rows=rows):
            for row in rows:
                for item in row:
                    yield from walk(item)
        case _:
            return


def depth(node: Node) -> int:
    """Maximum nesting depth of the tree — the metric behind check 7."""
    match node:
        case BinaryOp(left=left, right=right):
            return 1 + max(depth(left), depth(right))
        case UnaryOp(operand=operand) | PostfixOp(operand=operand):
            return 1 + depth(operand)
        case FuncCall(args=args):
            return 1 + max((depth(a) for a in args), default=0)
        case ArrayLit(rows=rows):
            return 1 + max((depth(i) for row in rows for i in row), default=0)
        case _:
            return 1
