"""Parse the text of a ``RANGE`` token into structure.

The tokenizer hands back references as raw strings — ``'Sales Q3'!$B$2``,
``[1]Budget!A1``, ``Table1[Amount]``. Everything downstream needs them broken
apart: check 1 needs row/column and absoluteness to normalise anchors, check 4
needs the external-workbook prefix, and the dependency graph needs the cell
coordinates.

This is tractable precisely *because* the tokenizer ran first. A reference token
is already known to be one complete reference, so the ambiguities that make
formula regexes hopeless — a ``!`` inside a string literal, a comma inside a
quoted sheet name — cannot reach this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Excel's own limits. Anything beyond these is not a reference, whatever it looks
# like, and is reported as a defined name instead of being silently accepted.
_MAX_COLUMN = "XFD"
_MAX_ROW = 1_048_576

_CELL_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})$")
_COL_ONLY_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})$")
_ROW_ONLY_RE = re.compile(r"^(\$?)([0-9]{1,7})$")
_NAME_RE = re.compile(r"^[A-Za-z_\\][A-Za-z0-9_.\\]*$")
_TABLE_RE = re.compile(r"^([A-Za-z_\\][A-Za-z0-9_.\\]*)\[(.*)\]$", re.DOTALL)


class ReferenceParseError(ValueError):
    """Raised when a reference token cannot be parsed.

    Callers record a finding rather than aborting the audit — a workbook with one
    exotic reference still deserves a report on its other 40,000 formulas.
    """

    def __init__(self, text: str, reason: str) -> None:
        super().__init__(f"could not parse reference {text!r}: {reason}")
        self.text = text
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CellRef:
    """One corner of a reference.

    ``column`` is ``None`` for a whole-row reference (``1:1``) and ``row`` is
    ``None`` for a whole-column reference (``A:A``); both are never ``None`` at
    once.
    """

    column: str | None
    row: int | None
    abs_column: bool = False
    abs_row: bool = False

    @property
    def column_index(self) -> int | None:
        """1-based column index, or ``None`` for a whole-row reference."""
        if self.column is None:
            return None
        idx = 0
        for char in self.column:
            idx = idx * 26 + (ord(char) - ord("A") + 1)
        return idx

    def __str__(self) -> str:
        col = f"{'$' if self.abs_column else ''}{self.column}" if self.column else ""
        row = f"{'$' if self.abs_row else ''}{self.row}" if self.row is not None else ""
        return f"{col}{row}"


@dataclass(frozen=True, slots=True)
class RangeRef:
    """A cell or cell-range reference, optionally qualified by sheet and workbook.

    ``end`` is ``None`` for a single cell. ``workbook`` is set only for external
    references, which is what check 4 keys off.
    """

    start: CellRef
    end: CellRef | None = None
    sheet: str | None = None
    workbook: str | None = None

    @property
    def is_external(self) -> bool:
        return self.workbook is not None

    @property
    def is_single_cell(self) -> bool:
        return self.end is None


@dataclass(frozen=True, slots=True)
class TableRef:
    """A structured table reference such as ``Table1[Amount]``.

    Column names and ``#``-prefixed specifiers (``#Headers``, ``#Totals``,
    ``#All``, ``#Data``, ``#This Row``) are separated, because a reference into
    ``#Totals`` means something different to the audit than one into ``#Data``.
    """

    table: str
    columns: tuple[str, ...] = ()
    specifiers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NameRef:
    """A defined name, or anything reference-shaped we decline to over-interpret."""

    name: str
    sheet: str | None = None
    workbook: str | None = None


Reference = RangeRef | TableRef | NameRef


def _split_prefix(text: str) -> tuple[str | None, str]:
    """Split ``Sheet!Body`` into its prefix and body, honouring quoting.

    Returns ``(prefix, body)`` with ``prefix`` ``None`` when unqualified. The
    prefix is returned still carrying any ``[workbook]`` part and with surrounding
    quotes removed.
    """
    if text.startswith("'"):
        # Quoted prefix: scan for the closing quote, treating '' as an escape.
        i = 1
        buf: list[str] = []
        while i < len(text):
            if text[i] == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                break
            buf.append(text[i])
            i += 1
        else:
            raise ReferenceParseError(text, "unterminated quoted sheet name")
        if i + 1 >= len(text) or text[i + 1] != "!":
            raise ReferenceParseError(text, "quoted sheet name not followed by '!'")
        return "".join(buf), text[i + 2 :]

    bang = text.rfind("!")
    if bang == -1:
        return None, text
    return text[:bang], text[bang + 1 :]


def _split_workbook(prefix: str) -> tuple[str | None, str | None]:
    """Split a prefix into ``(workbook, sheet)``.

    Handles ``[1]Sheet``, ``[Book.xlsx]Sheet``, and a bare sheet name. An empty
    sheet is returned as ``None`` so ``[1]!Name`` (a workbook-scoped defined name)
    does not masquerade as a sheet called "".
    """
    if prefix.startswith("["):
        close = prefix.find("]")
        if close == -1:
            raise ReferenceParseError(prefix, "unterminated '[' in workbook prefix")
        workbook = prefix[1:close]
        sheet = prefix[close + 1 :]
        return workbook, sheet or None
    return None, prefix or None


def _parse_corner(text: str) -> CellRef:
    if match := _CELL_RE.match(text):
        abs_col, col, abs_row, row = match.groups()
        col = col.upper()
        row_num = int(row)
        if len(col) == 3 and col > _MAX_COLUMN:
            raise ReferenceParseError(text, f"column {col} beyond Excel's {_MAX_COLUMN}")
        if row_num < 1 or row_num > _MAX_ROW:
            raise ReferenceParseError(text, f"row {row_num} outside 1..{_MAX_ROW}")
        return CellRef(col, row_num, abs_column=bool(abs_col), abs_row=bool(abs_row))

    if match := _COL_ONLY_RE.match(text):
        abs_col, col = match.groups()
        col = col.upper()
        if len(col) == 3 and col > _MAX_COLUMN:
            raise ReferenceParseError(text, f"column {col} beyond Excel's {_MAX_COLUMN}")
        return CellRef(col, None, abs_column=bool(abs_col))

    if match := _ROW_ONLY_RE.match(text):
        abs_row, row = match.groups()
        row_num = int(row)
        if row_num < 1 or row_num > _MAX_ROW:
            raise ReferenceParseError(text, f"row {row_num} outside 1..{_MAX_ROW}")
        return CellRef(None, row_num, abs_row=bool(abs_row))

    raise ReferenceParseError(text, "not a cell, column or row reference")


def _parse_table(text: str) -> TableRef:
    match = _TABLE_RE.match(text)
    if match is None:  # pragma: no cover - guarded by the caller
        raise ReferenceParseError(text, "not a structured table reference")
    table, inner = match.groups()
    inner = inner.strip()
    if not inner:
        return TableRef(table)

    # Two shapes: a single selector (Table1[Amount]) or a bracketed list
    # (Table1[[#Headers],[Amount]]). Normalise both to a list of parts.
    parts = (
        [p.strip() for p in re.findall(r"\[([^\]]*)\]", inner)]
        if inner.startswith("[")
        else [inner]
    )
    columns = tuple(p for p in parts if p and not p.startswith("#"))
    specifiers = tuple(p for p in parts if p.startswith("#"))
    return TableRef(table, columns, specifiers)


def parse_reference(text: str) -> Reference:
    """Parse the value of a ``RANGE`` token.

    Returns a :class:`RangeRef` for cell and range references, a :class:`TableRef`
    for structured table references, and a :class:`NameRef` for defined names.

    Raises:
        ReferenceParseError: if the text is not reference-shaped at all.
    """
    text = text.strip()
    if not text:
        raise ReferenceParseError(text, "empty reference")

    # Structured table refs carry no '!' and open a bracket after the table name.
    # Checked before prefix splitting so 'Table1[#All]' is not mistaken for a
    # workbook prefix.
    if not text.startswith("[") and "[" in text and "!" not in text:
        return _parse_table(text)

    prefix, body = _split_prefix(text)
    workbook = sheet = None
    if prefix is not None:
        workbook, sheet = _split_workbook(prefix)

    if not body:
        raise ReferenceParseError(text, "reference has a sheet but no body")

    # A sheet-qualified structured ref is legal but rare; handle it rather than
    # letting it fall through to the defined-name branch.
    if "[" in body and not body.startswith("["):
        return _parse_table(body)

    if ":" in body:
        # Both corners must parse. A defined name cannot contain ':', so unlike
        # the single-cell branch below there is no name to fall back to — a bad
        # corner here is a genuine error rather than a differently-shaped thing.
        left, _, right = body.partition(":")
        start, end = _parse_corner(left), _parse_corner(right)
        if (start.column is None) != (end.column is None) or (start.row is None) != (
            end.row is None
        ):
            raise ReferenceParseError(text, "range mixes whole-row and whole-column ends")
        return RangeRef(start, end, sheet=sheet, workbook=workbook)

    try:
        return RangeRef(_parse_corner(body), sheet=sheet, workbook=workbook)
    except ReferenceParseError:
        # Cell-shaped text that exceeds Excel's grid is not a broken reference —
        # it is a legal defined name, and Excel treats it as one. 'XFE1' is past
        # the last column and 'A1048577' past the last row, so Excel allows both
        # as names precisely because neither can be a cell. Returning NameRef
        # here is the correct reading, and it keeps downstream code from ever
        # holding a CellRef that points outside the grid.
        if _NAME_RE.match(body):
            return NameRef(body, sheet=sheet, workbook=workbook)
        raise
