"""Formula parsing for Tickmark.

SCOPE DISCIPLINE — read before adding anything to this package.

    You parse formulas. You never evaluate them.

That single rule is what keeps this project finishable (04-tickmark.md section 7,
item 10). Evaluating Excel semantics means implementing hundreds of functions,
their coercion rules, their error propagation, and their locale behaviour — an
unbounded commitment with no end state. Tickmark reports on the *text and shape*
of formulas; it never computes a value.

The dependency choice enforces this structurally rather than by intention. The
tokenizer underneath is ``openpyxl.formula.tokenizer`` — purely lexical, with no
evaluation machinery anywhere in its dependency tree. Alternatives (``formulas``,
``pycel``, ``xlcalculator``) are evaluation engines that happen to contain
parsers; taking one would have put "just compute this cell" a single import away.

Layering, lowest to highest:

    tokenizer.py    flat token stream (adapter over openpyxl)
    references.py   RANGE token text -> structured CellRef / RangeRef / TableRef
    functions.py    function classification (volatile set, check 6)
    shape.py        R1C1 normalisation — the primitive check 1 is built on
    ast.py          precedence climbing over the token stream (checks 5 and 7)
"""

from tickmark.formula.references import (
    CellRef,
    NameRef,
    RangeRef,
    Reference,
    TableRef,
    parse_reference,
)
from tickmark.formula.tokenizer import Token, TokenSubtype, TokenType, tokenize

__all__ = [
    "CellRef",
    "NameRef",
    "RangeRef",
    "Reference",
    "TableRef",
    "Token",
    "TokenSubtype",
    "TokenType",
    "parse_reference",
    "tokenize",
]
