"""The legacy ``.xls`` backend — BIFF8, the Excel 97-2003 binary format.

04-tickmark.md section 7 item 7 excluded ``.xls`` on the grounds that it "needs
an entirely separate parser". That was correct, and this module is that parser.
It exists because the workbooks most likely to have rotted unattended for twenty
years are exactly the ones still in the old format, so refusing them meant
refusing a good share of the files the product is for.

**Why this is not a second implementation of the whole product.** ``xlrd``
already decodes almost all of BIFF: sheet names and visibility, cell values,
hidden rows and columns, error codes, defined names. The one thing it does not
expose is the thing Tickmark is built on — *formula text*. In a ``.xls`` file a
formula is stored as a reverse-Polish array of parsed tokens, not as the string
the author typed, and ``xlrd`` keeps only the cached result. So this module
walks the raw record stream for the formula records alone and hands their token
arrays to ``xlrd.formula.decompile_formula``, which does know how to turn Ptg
tokens back into text. Everything else is read through ``xlrd``'s ordinary API.

**Shared formulas are the whole difficulty.** When a formula is filled down a
column, Excel writes the tokens *once* in a ``SHRFMLA`` record and leaves every
cell in the run pointing at it with a three-byte stub. Expanded naively, all
twenty cells of a filled column read as the same formula and check 1 — the
product's most valuable finding — goes blind precisely where it matters most.
Each stub is therefore re-decompiled against its own cell position, so relative
references adjust per row exactly as they would in the ``.xlsx`` path. A cell
that was *overwritten with a constant* has no formula record at all, which is
what makes the classic bug visible.

Three normalisations reconcile the decompiler's output with what the rest of
Tickmark expects, and all three are correctness issues rather than cosmetics:

1. ``xlrd`` renders every numeric literal as a float, so ``=1/0`` arrives as
   ``=1.0/0.0`` and ``ROUND(x,2)`` as ``ROUND(x,2.0)``. Left alone, the same
   logical workbook would audit differently as ``.xls`` than as ``.xlsx`` —
   check 2's structural-argument table is keyed on argument position but its
   ignore list is keyed on value, and ``2.0`` is not ``2``.
2. A deleted-reference token renders as ``(?)``. Check 3 looks for ``#REF!``,
   so a broken reference would otherwise be invisible in exactly the format
   most likely to contain one.
3. A single-cell 3D reference renders as a degenerate range, ``Sheet1!C2:C2``.
   Excel writes ``Sheet1!C2``, and the dependency graph should see one cell.

Known limitation, recorded rather than hidden: the external-link table
(``SUPBOOK``/``EXTERNSHEET``) is not decoded here, so check 4 on a ``.xls`` file
reports the reference text without resolving it to a file on disk. The
references themselves are still found.
"""

from __future__ import annotations

import re
import struct
from typing import Any

__all__ = ["LegacyBook", "read_legacy_workbook"]

# Record type codes, from the BIFF8 specification.
_BOF = 0x0809
_EOF = 0x000A
_BOUNDSHEET = 0x0085
_FORMULA = 0x0006
_SHRFMLA = 0x04BC
_ARRAY = 0x0221

# The first token of a formula whose tokens live in a SHRFMLA or ARRAY record.
_PTG_EXP = 0x01

_SUBSTREAM_WORKSHEET = 0x0010


class _Record:
    __slots__ = ("code", "data", "offset")

    def __init__(self, code: int, data: bytes, offset: int) -> None:
        self.code = code
        self.data = data
        self.offset = offset


def _records(stream: bytes) -> list[_Record]:
    """Split the stream into records, keeping each one's absolute offset.

    The offset is not incidental: ``BOUNDSHEET`` records in the global substream
    identify each sheet by the stream position of its ``BOF``, and that is the
    only reliable way to attribute a formula record to a sheet. Substreams are
    not required to appear in tab order.
    """
    out: list[_Record] = []
    pos, size = 0, len(stream)
    while pos + 4 <= size:
        code, length = struct.unpack("<HH", stream[pos : pos + 4])
        if pos + 4 + length > size:
            break
        out.append(_Record(code, stream[pos + 4 : pos + 4 + length], pos))
        pos += 4 + length
    return out


def _unicode_string(data: bytes, pos: int, length: int) -> str:
    """Read a BIFF8 short string: one flags byte, then 8- or 16-bit characters."""
    if pos >= len(data):
        return ""
    flags = data[pos]
    pos += 1
    if flags & 0x01:
        raw = data[pos : pos + length * 2]
        return raw.decode("utf-16-le", errors="replace")
    return data[pos : pos + length].decode("latin-1", errors="replace")


def _boundsheets(records: list[_Record]) -> dict[int, str]:
    """Map each sheet substream's ``BOF`` offset to that sheet's name."""
    out: dict[int, str] = {}
    for record in records:
        if record.code != _BOUNDSHEET or len(record.data) < 8:
            continue
        position, _grbit = struct.unpack("<IH", record.data[:6])
        sheet_type = record.data[5]
        if sheet_type != 0x00:  # 0 is a worksheet; macro and chart sheets are not
            continue
        out[position] = _unicode_string(record.data, 7, record.data[6])
    return out


_QUOTED = re.compile(r'"(?:[^"]|"")*"')
_FLOAT_LITERAL = re.compile(r"(?<![\w.$])(\d+)\.0(?![\d.eE])")
_DEGENERATE_RANGE = re.compile(r"(\$?[A-Z]{1,3}\$?\d+):(\$?[A-Z]{1,3}\$?\d+)")


def _outside_strings(text: str, transform: Any) -> str:
    """Apply a transform to the parts of a formula that are not string literals.

    ``="2.0 each"`` must keep its text exactly; only the arithmetic around it is
    normalised.
    """
    out: list[str] = []
    last = 0
    for match in _QUOTED.finditer(text):
        out.append(transform(text[last : match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(transform(text[last:]))
    return "".join(out)


def _collapse_degenerate(segment: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return match.group(1) if match.group(1) == match.group(2) else match.group(0)

    return _DEGENERATE_RANGE.sub(repl, segment)


def normalize_formula(text: str) -> str:
    """Reconcile the decompiler's output with the ``.xlsx`` path's conventions.

    See the module docstring for why each of these is a correctness fix rather
    than a cosmetic one.
    """
    text = text.replace("(?)", "#REF!")

    def fix(segment: str) -> str:
        return _collapse_degenerate(_FLOAT_LITERAL.sub(r"\1", segment))

    return _outside_strings(text, fix)


class LegacyBook:
    """Formula text recovered from a ``.xls`` file, addressed by sheet and cell.

    Deliberately narrow. This carries *only* what ``xlrd`` cannot provide;
    values, visibility and inventory come from ``xlrd`` in
    :mod:`tickmark.workbook.loader`.
    """

    def __init__(self, formulas: dict[str, dict[tuple[int, int], str]], has_macros: bool) -> None:
        self._formulas = formulas
        self.has_macros = has_macros

    def formulas_for(self, sheet: str) -> dict[tuple[int, int], str]:
        """``{(row, column): formula}`` for one sheet, 1-based, with the ``=``."""
        return self._formulas.get(sheet, {})


def _decompile(book: Any, tokens: bytes, row: int, column: int, shared: bool) -> str | None:
    """Turn a Ptg token array into formula text, or give up quietly.

    ``browx``/``bcolx`` are the base cell the relative references are measured
    from; passing the *target* cell rather than the shared record's anchor is
    what makes a filled column expand correctly.
    """
    from xlrd.formula import FMLA_TYPE_CELL, FMLA_TYPE_SHARED, decompile_formula

    try:
        text = decompile_formula(
            book,
            tokens,
            len(tokens),
            FMLA_TYPE_SHARED if shared else FMLA_TYPE_CELL,
            row,
            column,
        )
    except Exception:  # noqa: BLE001 - one unreadable formula must not cost the file
        return None
    if not text:
        return None
    return "=" + normalize_formula(str(text))


def read_legacy_workbook(path: str, book: Any) -> LegacyBook:
    """Extract every formula from a ``.xls`` file.

    ``book`` is an already-opened ``xlrd`` book; the decompiler needs it to
    resolve sheet indexes and defined names to text.
    """
    import olefile

    with olefile.OleFileIO(path) as ole:
        name = "Workbook" if ole.exists("Workbook") else "Book"
        if not ole.exists(name):
            return LegacyBook({}, has_macros=False)
        stream = ole.openstream(name).read()
        has_macros = ole.exists("_VBA_PROJECT_CUR") or ole.exists("_VBA_PROJECT")

    records = _records(stream)
    sheet_at = _boundsheets(records)

    # Pass one: collect raw tokens, and the shared definitions they may point at.
    # Decompilation is deferred because a cell can reference a SHRFMLA record
    # that has not been read yet when its own FORMULA record appears.
    current: str | None = None
    pending: list[tuple[str, int, int, bytes]] = []
    stubs: list[tuple[str, int, int, int, int]] = []
    shared: dict[tuple[str, int, int], bytes] = {}

    for record in records:
        if record.code == _BOF:
            if record.offset in sheet_at:
                current = sheet_at[record.offset]
            elif len(record.data) >= 4:
                kind = struct.unpack("<H", record.data[2:4])[0]
                if kind != _SUBSTREAM_WORKSHEET:
                    current = None
            continue

        if record.code == _EOF:
            current = None
            continue

        if current is None:
            continue

        if record.code == _SHRFMLA and len(record.data) >= 10:
            first_row, _last_row = struct.unpack("<HH", record.data[0:4])
            first_col = record.data[4]
            length = struct.unpack("<H", record.data[8:10])[0]
            shared[(current, first_row, first_col)] = record.data[10 : 10 + length]
            continue

        if record.code == _ARRAY and len(record.data) >= 12:
            first_row, _last_row = struct.unpack("<HH", record.data[0:4])
            first_col = record.data[4]
            length = struct.unpack("<H", record.data[10:12])[0]
            shared[(current, first_row, first_col)] = record.data[12 : 12 + length]
            continue

        if record.code == _FORMULA and len(record.data) >= 22:
            row, column = struct.unpack("<HH", record.data[0:4])
            length = struct.unpack("<H", record.data[20:22])[0]
            tokens = record.data[22 : 22 + length]
            if not tokens:
                continue
            if tokens[0] == _PTG_EXP and len(tokens) >= 5:
                anchor_row, anchor_col = struct.unpack("<HH", tokens[1:5])
                stubs.append((current, row, column, anchor_row, anchor_col))
            else:
                pending.append((current, row, column, tokens))

    # Pass two: decompile, now that every shared definition is known.
    out: dict[str, dict[tuple[int, int], str]] = {}

    for sheet, row, column, tokens in pending:
        text = _decompile(book, tokens, row, column, shared=False)
        if text is not None:
            out.setdefault(sheet, {})[(row + 1, column + 1)] = text

    for sheet, row, column, anchor_row, anchor_col in stubs:
        tokens = shared.get((sheet, anchor_row, anchor_col))
        if tokens is None:
            continue
        text = _decompile(book, tokens, row, column, shared=True)
        if text is not None:
            out.setdefault(sheet, {})[(row + 1, column + 1)] = text

    return LegacyBook(out, has_macros=has_macros)
