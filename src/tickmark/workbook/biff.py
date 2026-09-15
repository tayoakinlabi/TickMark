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

**External links, and why the decompiler alone is not enough.** ``xlrd`` writes
``<<external>>`` wherever a formula reaches into another workbook: it has the
externsheet index in hand but no file table to resolve it against. Left at that,
check 4 does not merely fail to name the target file — the reference parser never
recognises the formula as external at all, so the link is invisible in exactly
the format most likely to hold a stale one. So the ``SUPBOOK`` and
``EXTERNSHEET`` records are decoded here and the placeholder is rewritten to
``[n]SheetName``, the notation the ``.xlsx`` path already produces.

Substitution is positional and guarded: the nth placeholder is the nth 3D
reference in the token stream, attempted only when a full walk of the tokens
succeeds and yields exactly as many external references as the text has
placeholders. Otherwise the text is left alone. Naming the wrong file would be
worse than naming none, because check 4 reports whether the target resolves and
a confident pointer at an unrelated workbook turns a useful finding into a
misleading one.

One asymmetry between the formats survives all of this and is Excel's, not ours:
a `.xls` stores the link as a path *relative* to the workbook holding it, while
a `.xlsx` stores an absolute one. The same workbook saved both ways and then
moved therefore resolves from one and not the other. Both are reported faithfully
— the parity that is enforced is that both formats find the same references in
the same cells, not that a moved file resolves identically from each.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Any

__all__ = ["LegacyBook", "read_legacy_workbook"]

# Record type codes, from the BIFF8 specification.
_BOF = 0x0809
_EOF = 0x000A
_BOUNDSHEET = 0x0085
_FORMULA = 0x0006
_SHRFMLA = 0x04BC
_ARRAY = 0x0221
_SUPBOOK = 0x01AE
_EXTERNSHEET = 0x0017

# The first token of a formula whose tokens live in a SHRFMLA or ARRAY record.
_PTG_EXP = 0x01

# 3D reference tokens, after class normalisation: a cell and a range in another
# sheet or another workbook. Both carry a 2-byte externsheet index first.
_PTG_REF3D = 0x3A
_PTG_AREA3D = 0x3B

# Variable-length tokens the size table cannot answer for on its own.
_PTG_STR = 0x17
_PTG_ATTR = 0x19

# A SUPBOOK whose URL length field is this is the workbook referring to itself,
# not a link out. Excel writes one of these in every file that has any
# externsheet table at all.
_SUPBOOK_SELF = 0x0401

# What xlrd's decompiler writes where a reference into another workbook should
# be. It has the externsheet index in hand at that point but no file table to
# resolve it against, so it gives up and emits this.
_EXTERNAL_PLACEHOLDER = "<<external>>"

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


@dataclass(frozen=True, slots=True)
class _Supbook:
    """One entry of the workbook's external-reference table.

    ``path`` is ``None`` for the self-reference entry Excel writes for the
    current workbook, and for add-in books, neither of which is a link out.
    """

    path: str | None
    sheets: tuple[str, ...] = ()

    @property
    def is_external(self) -> bool:
        return self.path is not None


# Control characters Excel uses inside a stored link path. The path is not plain
# text: it encodes volume and directory structure in bytes below 0x20 so the same
# string can describe a drive, a UNC share or a relative location.
_PATH_CONTROL = {
    0x01: "",  # volume marker; the drive letter or '@server' follows verbatim
    0x02: "",  # same directory as the workbook holding the link
    0x03: "\\",  # directory separator
    0x04: "..\\",  # one directory up
    0x05: "",  # unused in practice, seen in the wild
    0x06: "\\",  # start of a path inside the current volume
    0x07: "",
    0x08: "",
}


def _decode_link_path(raw: str) -> str:
    """Turn Excel's encoded link path into something a person can read.

    Deliberately lossy about the volume marker. Excel writes ``\\x01`` before an
    absolute location and ``\\x02`` before one relative to the linking workbook;
    dropping both leaves a path that is either already absolute or resolves
    against the audited file's directory, which is exactly what check 4 does with
    it. Guessing a drive letter that is not in the bytes would be worse than
    handing back a relative path and letting the existence test resolve it.
    """
    out: list[str] = []
    for char in raw:
        code = ord(char)
        if code < 0x20:
            out.append(_PATH_CONTROL.get(code, ""))
        else:
            out.append(char)
    return "".join(out).strip()


def _long_unicode_string(data: bytes, pos: int) -> tuple[str, int]:
    """Read a BIFF8 string with a 2-byte length. Returns the text and the new position."""
    if pos + 3 > len(data):
        return "", len(data)
    length = struct.unpack("<H", data[pos : pos + 2])[0]
    flags = data[pos + 2]
    pos += 3
    if flags & 0x01:
        raw = data[pos : pos + length * 2]
        return raw.decode("utf-16-le", errors="replace"), pos + length * 2
    raw = data[pos : pos + length]
    return raw.decode("latin-1", errors="replace"), pos + length


def _supbooks(records: list[_Record]) -> list[_Supbook]:
    """Every SUPBOOK record, in the order the externsheet table indexes them."""
    out: list[_Supbook] = []
    for record in records:
        if record.code != _SUPBOOK or len(record.data) < 4:
            continue
        sheet_count, marker = struct.unpack("<HH", record.data[:4])
        if marker == _SUPBOOK_SELF:
            # This workbook referring to itself: not a link out.
            out.append(_Supbook(None))
            continue
        url, pos = _long_unicode_string(record.data, 2)
        sheets: list[str] = []
        for _ in range(sheet_count):
            if pos >= len(record.data):
                break
            name, pos = _long_unicode_string(record.data, pos)
            sheets.append(name)
        out.append(_Supbook(_decode_link_path(url) or None, tuple(sheets)))
    return out


def _externsheet(records: list[_Record]) -> list[tuple[int, int, int]]:
    """The XTI table: each entry is ``(supbook index, first sheet, last sheet)``."""
    for record in records:
        if record.code != _EXTERNSHEET or len(record.data) < 2:
            continue
        count = struct.unpack("<H", record.data[:2])[0]
        out: list[tuple[int, int, int]] = []
        for i in range(count):
            start = 2 + i * 6
            if start + 6 > len(record.data):
                break
            out.append(struct.unpack("<hhh", record.data[start : start + 6]))
        return out
    return []


def _normalise_token(op: int) -> int:
    """Collapse a token's operand class so it can be looked up in the size table.

    Operand tokens exist three times over — reference, value and array class, at
    ``0x20``, ``0x40`` and ``0x60`` — and all three are the same token with the
    same layout.
    """
    return op if op < 0x20 else ((op & 0x1F) | 0x20)


def _external_indexes(tokens: bytes, xti: list[tuple[int, int, int]]) -> list[int] | None:
    """Externsheet indexes of the 3D references in a formula, in token order.

    Returns ``None`` — meaning "do not guess" — if the token stream contains
    anything this walker cannot size confidently. A wrong answer here would
    attribute a link to the wrong file, which is worse than not naming it.
    """
    from xlrd.formula import sztab4

    found: list[int] = []
    pos, size = 0, len(tokens)
    while pos < size:
        op = tokens[pos]
        base = _normalise_token(op)

        if base in (_PTG_REF3D, _PTG_AREA3D):
            if pos + 3 > size:
                return None
            found.append(struct.unpack("<h", tokens[pos + 1 : pos + 3])[0])

        if base == _PTG_STR:
            # 1 opcode + 1 length + 1 flags + the characters themselves.
            if pos + 3 > size:
                return None
            length, flags = tokens[pos + 1], tokens[pos + 2]
            pos += 3 + length * (2 if flags & 0x01 else 1)
            continue

        if base == _PTG_ATTR:
            if pos + 4 > size:
                return None
            subtype = tokens[pos + 1]
            count = struct.unpack("<H", tokens[pos + 2 : pos + 4])[0]
            # tAttrChoose carries a jump table of its own after the header.
            pos += 4 + (2 * (count + 1) if subtype & 0x04 else 0)
            continue

        if base >= len(sztab4):
            return None
        step = sztab4[base]
        if step is None or step < 1:
            # Variable or unknown: stop rather than mis-walk the rest.
            return None
        pos += step

    if any(index < 0 or index >= len(xti) for index in found):
        return None
    return found


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

    def __init__(
        self,
        formulas: dict[str, dict[tuple[int, int], str]],
        has_macros: bool,
        external_links: dict[int, str] | None = None,
    ) -> None:
        self._formulas = formulas
        self.has_macros = has_macros
        self.external_links = external_links or {}

    def formulas_for(self, sheet: str) -> dict[tuple[int, int], str]:
        """``{(row, column): formula}`` for one sheet, 1-based, with the ``=``."""
        return self._formulas.get(sheet, {})


@dataclass(frozen=True, slots=True)
class _LinkTable:
    """Everything needed to turn an externsheet index into ``[n]`` in formula text."""

    supbooks: list[_Supbook]
    xti: list[tuple[int, int, int]]
    # Externsheet index -> the 1-based number used in '[n]', matching how the
    # .xlsx path numbers the same links so both formats read identically.
    numbering: dict[int, int]
    links: dict[int, str]

    def label_for(self, externsheet_index: int) -> str | None:
        """``[n]SheetName`` for an external reference, or ``None`` if it is local."""
        number = self.numbering.get(externsheet_index)
        if number is None:
            return None
        _supbook_index, first, _last = self.xti[externsheet_index]
        supbook = self.supbooks[_supbook_index]
        sheet = supbook.sheets[first] if 0 <= first < len(supbook.sheets) else ""
        return f"[{number}]{sheet}" if sheet else f"[{number}]"


def _build_link_table(records: list[_Record]) -> _LinkTable:
    supbooks = _supbooks(records)
    xti = _externsheet(records)

    numbering: dict[int, int] = {}
    links: dict[int, str] = {}
    next_number = 1
    seen: dict[int, int] = {}

    for index, (supbook_index, _first, _last) in enumerate(xti):
        if not (0 <= supbook_index < len(supbooks)):
            continue
        supbook = supbooks[supbook_index]
        if not supbook.is_external:
            continue
        # One number per external *workbook*, not per sheet of it, so two
        # references into different sheets of the same file share a '[1]'.
        if supbook_index not in seen:
            seen[supbook_index] = next_number
            links[next_number] = supbook.path or ""
            next_number += 1
        numbering[index] = seen[supbook_index]

    return _LinkTable(supbooks, xti, numbering, links)


def _resolve_externals(text: str, tokens: bytes, table: _LinkTable) -> str:
    """Replace the decompiler's ``<<external>>`` with ``[n]SheetName``.

    Substitution is positional — the nth placeholder in the text is the nth 3D
    reference in the token stream — and is only attempted when those two counts
    agree. If the token walk cannot be completed, or produces a different number
    of references than the text has placeholders, the text is left alone.
    Naming the wrong file is worse than naming none: check 4 reports whether a
    link resolves, and a confident pointer at an unrelated workbook would turn a
    useful finding into a misleading one.
    """
    placeholders = text.count(_EXTERNAL_PLACEHOLDER)
    if not placeholders:
        return text

    indexes = _external_indexes(tokens, table.xti)
    if indexes is None:
        return text

    labels = [table.label_for(i) for i in indexes]
    external = [label for label in labels if label is not None]
    if len(external) != placeholders:
        return text

    out = text
    for label in external:
        out = out.replace(_EXTERNAL_PLACEHOLDER, label, 1)
    return out


def _decompile(
    book: Any,
    tokens: bytes,
    row: int,
    column: int,
    shared: bool,
    table: _LinkTable | None = None,
) -> str | None:
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
    rendered = str(text)
    if table is not None:
        rendered = _resolve_externals(rendered, tokens, table)
    return "=" + normalize_formula(rendered)


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
    table = _build_link_table(records)

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
        text = _decompile(book, tokens, row, column, shared=False, table=table)
        if text is not None:
            out.setdefault(sheet, {})[(row + 1, column + 1)] = text

    for sheet, row, column, anchor_row, anchor_col in stubs:
        tokens = shared.get((sheet, anchor_row, anchor_col))
        if tokens is None:
            continue
        text = _decompile(book, tokens, row, column, shared=True, table=table)
        if text is not None:
            out.setdefault(sheet, {})[(row + 1, column + 1)] = text

    return LegacyBook(out, has_macros=has_macros, external_links=table.links)
