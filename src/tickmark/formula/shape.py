"""Formula shape normalisation — the primitive check 1 is built on.

Check 1 asks: in a contiguous run of formulas, is one of them different? Comparing
formula *text* cannot answer that, because ``=A1*2`` in B1 and ``=A2*2`` in B2 are
the same formula correctly filled down, while ``=A1*2`` in both is a bug. The
difference is invisible in A1 notation and obvious in R1C1, where both correct
cells read ``=R[0]C[-1]*2`` and the broken pair does not match.

So: normalise every formula to R1C1 relative to the cell it lives in, then compare
the normalised strings. Identical shape means consistent; a single odd one out in
a run is the finding — and the most valuable one this product makes, because it is
where money actually goes missing.

Nothing here evaluates anything. Shapes are strings, compared as strings.
"""

from __future__ import annotations

from tickmark.formula.references import (
    CellRef,
    NameRef,
    RangeRef,
    ReferenceParseError,
    TableRef,
    parse_reference,
)
from tickmark.formula.tokenizer import (
    FormulaSyntaxError,
    Token,
    TokenType,
    tokenize,
)

__all__ = ["formula_shape", "normalize_reference", "ShapeError"]


class ShapeError(ValueError):
    """Raised when a formula cannot be normalised to a shape."""


def _axis(label: str, value: int | None, origin: int, is_absolute: bool) -> str:
    """Render one R1C1 axis component.

    Absolute references keep their index (``C3``); relative ones become an offset
    from the origin cell (``C[-1]``), which is exactly what makes a correctly
    filled-down column compare equal.
    """
    if value is None:
        return ""
    if is_absolute:
        return f"{label}{value}"
    offset = value - origin
    return f"{label}[{offset}]" if offset else label


def _corner(ref: CellRef, origin_row: int, origin_col: int) -> str:
    return _axis("R", ref.row, origin_row, ref.abs_row) + _axis(
        "C", ref.column_index, origin_col, ref.abs_column
    )


def normalize_reference(text: str, origin_row: int, origin_column: int) -> str:
    """Normalise one reference string to R1C1 relative to an origin cell.

    ``origin_row`` is 1-based and ``origin_column`` is a 1-based column index, so
    cell B2 is ``(2, 2)``.

    References that are not cell-shaped — defined names, structured table refs —
    are returned unchanged. They do not shift when a formula is filled down, so
    leaving them literal is correct, not a shortcut.
    """
    try:
        ref = parse_reference(text)
    except ReferenceParseError:
        # Not reference-shaped; preserve it verbatim so the shape still differs
        # from a formula that has something else in this position.
        return text

    if isinstance(ref, (NameRef, TableRef)):
        return text

    assert isinstance(ref, RangeRef)
    prefix = ""
    if ref.workbook is not None:
        prefix += f"[{ref.workbook}]"
    if ref.sheet is not None:
        prefix += f"{ref.sheet}!"

    start = _corner(ref.start, origin_row, origin_column)
    if ref.end is None:
        return f"{prefix}{start}"
    end = _corner(ref.end, origin_row, origin_column)
    return f"{prefix}{start}:{end}"


def formula_shape(formula: str, origin_row: int, origin_column: int) -> str:
    """Return the R1C1-normalised shape of a formula.

    Two cells hold "the same" formula, in the sense check 1 cares about, exactly
    when their shapes are equal.

    Whitespace is collapsed to a single space rather than removed, because in
    Excel a space between two ranges is the intersection operator — dropping it
    would make ``=SUM(A1:A5 B1:B5)`` and ``=SUM(A1:A5,B1:B5)`` compare as
    unrelated while quietly corrupting the first one's meaning.

    Raises:
        ShapeError: if the formula cannot be tokenized.
    """
    try:
        tokens = tokenize(formula, keep_whitespace=True)
    except FormulaSyntaxError as exc:
        raise ShapeError(str(exc)) from exc

    parts: list[str] = []
    for token in tokens:
        parts.append(_render(token, origin_row, origin_column))
    return "".join(parts)


def _render(token: Token, origin_row: int, origin_column: int) -> str:
    if token.type is TokenType.WHITESPACE:
        return " "
    if token.is_range:
        return normalize_reference(token.value, origin_row, origin_column)
    return token.value
