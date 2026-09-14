"""Adapter over ``openpyxl.formula.tokenizer`` (T3).

openpyxl's tokenizer is kept behind this module rather than used directly
everywhere, for three reasons:

1. One file to change if openpyxl's token API moves.
2. Callers get real enums instead of bare strings, so a typo in a subtype
   comparison is a ``ValueError`` at import rather than a check that silently
   never fires.
3. It is the seam where the "never evaluate" rule is enforced — nothing below
   this module knows how to compute anything.

What openpyxl gives us, verified in 04-tickmark.md section 9: correct handling of
string literals containing commas and escaped quotes, quoted sheet names with
spaces, array constants, error literals, whitespace-as-intersection, and unary
prefix / postfix operators. What it does *not* give us is a tree — the output is
flat. See ``ast.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from openpyxl.formula.tokenizer import Token as _OpenpyxlToken
from openpyxl.formula.tokenizer import Tokenizer as _OpenpyxlTokenizer


class TokenType(Enum):
    """Top-level token category, mirroring openpyxl's ``Token.type``."""

    LITERAL = "LITERAL"
    OPERAND = "OPERAND"
    FUNC = "FUNC"
    ARRAY = "ARRAY"
    PAREN = "PAREN"
    SEP = "SEP"
    OPERATOR_PREFIX = "OPERATOR-PREFIX"
    OPERATOR_INFIX = "OPERATOR-INFIX"
    OPERATOR_POSTFIX = "OPERATOR-POSTFIX"
    WHITESPACE = "WHITE-SPACE"


class TokenSubtype(Enum):
    """Token subtype, mirroring openpyxl's ``Token.subtype``.

    ``NUMBER``, ``ERROR`` and ``RANGE`` are the ones that carry checks:
    check 2 keys off ``NUMBER``, check 3 off ``ERROR``, and checks 1/3/4 plus the
    dependency graph off ``RANGE``.
    """

    NONE = ""
    NUMBER = "NUMBER"
    TEXT = "TEXT"
    LOGICAL = "LOGICAL"
    ERROR = "ERROR"
    RANGE = "RANGE"
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    ARG = "ARG"
    ROW = "ROW"
    MATH = "MATH"


@dataclass(frozen=True, slots=True)
class Token:
    """A single lexical token.

    ``value`` is the raw source text. For a ``RANGE`` operand that means an
    unparsed reference string such as ``'Sales Q3'!$B$2`` — turning that into
    structure is ``references.parse_reference``'s job, not the tokenizer's.
    """

    value: str
    type: TokenType
    subtype: TokenSubtype

    @property
    def is_number(self) -> bool:
        return self.type is TokenType.OPERAND and self.subtype is TokenSubtype.NUMBER

    @property
    def is_range(self) -> bool:
        return self.type is TokenType.OPERAND and self.subtype is TokenSubtype.RANGE

    @property
    def is_error(self) -> bool:
        return self.type is TokenType.OPERAND and self.subtype is TokenSubtype.ERROR

    @property
    def is_func_open(self) -> bool:
        return self.type is TokenType.FUNC and self.subtype is TokenSubtype.OPEN

    @property
    def function_name(self) -> str | None:
        """Bare function name for a ``FUNC/OPEN`` token, else ``None``.

        openpyxl reports the value with its trailing paren (``"SUM("``), which is
        never what a caller wants to compare against.
        """
        if not self.is_func_open:
            return None
        return self.value.rstrip("(").rstrip()


class FormulaSyntaxError(ValueError):
    """Raised when a formula cannot be tokenized.

    Tickmark never refuses to audit a workbook because one cell is malformed, so
    callers are expected to catch this and record a finding rather than abort.
    """

    def __init__(self, formula: str, reason: str) -> None:
        super().__init__(f"could not tokenize {formula!r}: {reason}")
        self.formula = formula
        self.reason = reason


def _convert(token: _OpenpyxlToken) -> Token:
    try:
        ttype = TokenType(token.type)
    except ValueError as exc:  # pragma: no cover - guards an openpyxl API change
        raise FormulaSyntaxError(token.value, f"unknown token type {token.type!r}") from exc
    try:
        subtype = TokenSubtype(token.subtype)
    except ValueError as exc:  # pragma: no cover - guards an openpyxl API change
        raise FormulaSyntaxError(token.value, f"unknown token subtype {token.subtype!r}") from exc
    return Token(value=token.value, type=ttype, subtype=subtype)


# Tokenizing is pure and the results are immutable, so identical formula text
# always tokenizes identically. Worth caching because a workbook repeats formula
# text heavily — a measured benchmark found 3,020 formulas sharing only 604
# distinct texts — and because several checks each look at the same cell.
#
# Bounded rather than unbounded: a workbook with more distinct formulas than this
# degrades to re-tokenizing the excess, which is slow but correct. An unbounded
# cache on a 500,000-formula workbook would be a memory problem instead.
_CACHE_SIZE = 20_000


@lru_cache(maxsize=_CACHE_SIZE)
def _tokenize_cached(formula: str, keep_whitespace: bool) -> tuple[Token, ...]:
    text = formula.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1].strip()
    if not text:
        return ()
    if not text.startswith("="):
        text = "=" + text

    try:
        raw = _OpenpyxlTokenizer(text).items
    except Exception as exc:  # openpyxl raises bare TokenizerError/ValueError
        raise FormulaSyntaxError(formula, str(exc)) from exc

    tokens = tuple(_convert(t) for t in raw)
    if keep_whitespace:
        return tokens
    return tuple(t for t in tokens if t.type is not TokenType.WHITESPACE)


def clear_caches() -> None:
    """Drop memoised tokenizer results.

    For a long-running process that has finished with one workbook and does not
    want to hold its formulas in memory.
    """
    _tokenize_cached.cache_clear()


def tokenize(formula: str, *, keep_whitespace: bool = False) -> list[Token]:
    """Tokenize an Excel formula.

    Accepts the formula with or without a leading ``=``, and tolerates the
    ``{=...}`` array-formula wrapper that some writers emit.

    Whitespace is dropped by default because it is noise for most checks. It is
    *not* always noise: in Excel a space between two ranges is the intersection
    operator, so ``ast.py`` re-tokenizes with ``keep_whitespace=True``. Dropping
    it silently there would turn ``=SUM(A1:A5 B1:B5)`` into a syntax error.

    Results are memoised. A fresh list is returned each call so a caller can do
    what it likes with it; the copy costs far less than re-tokenizing.

    Raises:
        FormulaSyntaxError: if the formula cannot be tokenized.
    """
    return list(_tokenize_cached(formula, keep_whitespace))
