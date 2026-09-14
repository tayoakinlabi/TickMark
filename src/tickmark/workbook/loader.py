"""Open workbooks for reading. The one place NF 4 is enforced.

**Tickmark never writes to an audited workbook.** That guarantee lives here, and
it is structural rather than advisory: this module returns wrapper objects that
expose no mutation and no save path, and nothing above it ever touches an
openpyxl workbook directly.

A note on the word "read-only", because openpyxl overloads it. openpyxl's
``read_only=True`` is a *memory* mode — it streams cells instead of building the
full object graph, and in exchange it loses row and column dimension data, which
is exactly what the hidden-rows part of the inventory (item 9) needs. It is not
a safety setting. openpyxl never writes to a file unless ``save()`` is called, so
our guarantee comes from never calling it, not from that flag.

Two loads, not one, and only on demand. openpyxl surfaces either formulas or the
values Excel last cached, never both from a single load. Check 3 needs cached
values — ``=1/0`` shows its error only there — while every other check needs
formulas. So values are loaded lazily and the cost is paid only by the checks
that ask.
"""

from __future__ import annotations

import contextlib
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.formula import ArrayFormula

__all__ = [
    "Cell",
    "LoadedWorkbook",
    "PasswordProtectedError",
    "Sheet",
    "UnsupportedFormatError",
    "WorkbookError",
    "open_workbook",
]

# openpyxl reads these. '.xls' and '.xlsb' are deliberately absent — see
# 04-tickmark.md section 7, items 7 and 9.
_SUPPORTED_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xltx", ".xltm"})
_MACRO_SUFFIXES = frozenset({".xlsm", ".xltm"})


class WorkbookError(Exception):
    """A workbook could not be opened.

    Every subclass carries a reason a human can act on, because these land in the
    "could not read" report rather than aborting the run — one unreadable file in
    a folder of 200 must never cost the other 199 their audit.
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path.name}: {reason}")
        self.path = path
        self.reason = reason


class UnsupportedFormatError(WorkbookError):
    """The file is not a format Tickmark reads."""


class PasswordProtectedError(WorkbookError):
    """The file is encrypted and cannot be opened without a password."""


class CorruptWorkbookError(WorkbookError):
    """The file claims to be a workbook but could not be parsed."""


@dataclass(frozen=True, slots=True)
class Cell:
    """One non-empty cell, holding either a formula or a literal.

    Check 1 needs both. The bug it hunts is a *constant* sitting in a run of
    formulas — someone typed 4200 over the formula in C4 — so a cell model that
    only knew about formulas would be blind to the most valuable finding in the
    product.

    ``row`` and ``column`` are 1-based, matching Excel and
    :class:`~tickmark.formula.references.CellRef`.
    """

    sheet: str
    row: int
    column: int
    formula: str | None = None
    value: object = None
    is_array: bool = False

    @property
    def is_formula(self) -> bool:
        return self.formula is not None

    @property
    def coordinate(self) -> str:
        """Excel-style address, e.g. ``B12``."""
        letters = ""
        index = self.column
        while index > 0:
            index, remainder = divmod(index - 1, 26)
            letters = chr(ord("A") + remainder) + letters
        return f"{letters}{self.row}"


class Sheet:
    """One worksheet, exposed read-only."""

    def __init__(self, workbook: LoadedWorkbook, worksheet: Any) -> None:
        self._workbook = workbook
        self._ws = worksheet

    @property
    def workbook(self) -> LoadedWorkbook:
        """The book this sheet belongs to.

        Checks reach book-level context through here — check 4 needs the
        external-link table, check 3 needs the cached-value pass.
        """
        return self._workbook

    @property
    def name(self) -> str:
        return str(self._ws.title)

    @property
    def is_hidden(self) -> bool:
        """True for both ``hidden`` and ``veryHidden`` sheets.

        Worth reporting: a hidden sheet driving visible numbers is a common way
        for a workbook to become unmaintainable without anyone noticing.
        """
        return self._ws.sheet_state != "visible"

    @property
    def is_very_hidden(self) -> bool:
        """True only for ``veryHidden``, which the Excel UI cannot unhide."""
        return self._ws.sheet_state == "veryHidden"

    @property
    def max_row(self) -> int:
        return int(self._ws.max_row or 0)

    @property
    def max_column(self) -> int:
        return int(self._ws.max_column or 0)

    @property
    def hidden_rows(self) -> tuple[int, ...]:
        return tuple(sorted(idx for idx, dim in self._ws.row_dimensions.items() if dim.hidden))

    @property
    def hidden_columns(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, dim in self._ws.column_dimensions.items() if dim.hidden))

    def cells(self) -> Iterator[Cell]:
        """Yield every non-empty cell, row-major, formulas and literals alike.

        Array formulas are reported at their anchor cell with ``is_array`` set;
        the spill range is not expanded, because a finding should name the cell
        the author actually edits.
        """
        for row in self._ws.iter_rows():
            for cell in row:
                raw = cell.value
                if raw is None:
                    continue
                if isinstance(raw, ArrayFormula):
                    text = raw.text or ""
                    if text:
                        yield Cell(self.name, cell.row, cell.column, formula=text, is_array=True)
                    continue
                if isinstance(raw, str) and raw.startswith("="):
                    yield Cell(self.name, cell.row, cell.column, formula=raw)
                    continue
                yield Cell(self.name, cell.row, cell.column, value=raw)

    def formulas(self) -> Iterator[Cell]:
        """Yield only the formula-bearing cells."""
        return (c for c in self.cells() if c.is_formula)

    def error_values(self) -> Iterator[tuple[int, int, str]]:
        """Yield ``(row, column, error)`` for cells whose cached value is an error.

        This is the other half of check 3. ``=A1+#REF!`` carries its error in the
        formula text, but ``=1/0`` and ``=VLOOKUP(...)`` on a missing key show
        nothing there — only the value Excel last cached reveals them.
        """
        ws = self._workbook._values_sheet(self.name)
        if ws is None:
            return
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                if isinstance(value, str) and value.startswith("#"):
                    yield cell.row, cell.column, value


class LoadedWorkbook:
    """An opened workbook. Exposes reading only — there is no save path."""

    def __init__(self, path: Path, workbook: Any) -> None:
        self.path = path
        self._wb = workbook
        self._values_wb: Any | None = None
        self._values_loaded = False

    @property
    def name(self) -> str:
        return self.path.name

    @cached_property
    def sheets(self) -> tuple[Sheet, ...]:
        return tuple(Sheet(self, ws) for ws in self._wb.worksheets)

    def sheet(self, name: str) -> Sheet | None:
        return next((s for s in self.sheets if s.name == name), None)

    @property
    def has_macros(self) -> bool:
        """True if the workbook carries a VBA project.

        Presence only. Tickmark never parses VBA (04-tickmark.md section 7,
        item 8) — but a macro-bearing workbook behaves in ways a static audit
        cannot see, and the report should say so.
        """
        if getattr(self._wb, "vba_archive", None) is not None:
            return True
        return self.path.suffix.lower() in _MACRO_SUFFIXES

    @cached_property
    def external_links(self) -> dict[int, str]:
        """Map a formula's ``[n]`` workbook index to the target it refers to.

        A finding that says ``[1]Budget!A1 is broken`` is close to useless — the
        reader has no idea what workbook 1 is. This table is what lets check 4
        name the file. Indexes are 1-based, matching how Excel writes them.

        The requirement notes these live in several places in the OOXML package;
        this reads the workbook-level link table, which is where the file targets
        are. Formula text supplies the rest.
        """
        links = getattr(self._wb, "_external_links", None) or []
        out: dict[int, str] = {}
        for index, link in enumerate(links, start=1):
            target = getattr(getattr(link, "file_link", None), "Target", None)
            if target:
                out[index] = str(target)
        return out

    def resolve_external(self, index_or_name: str) -> str | None:
        """Resolve a ``[...]`` prefix to a target path, if it names one.

        Excel writes either an index (``[1]``) or, for some links, the file name
        directly. Both arrive here as text.
        """
        if index_or_name.isdigit():
            return self.external_links.get(int(index_or_name))
        return index_or_name or None

    @cached_property
    def defined_names(self) -> tuple[tuple[str, str], ...]:
        """``(name, refers_to)`` pairs, workbook-scoped."""
        out: list[tuple[str, str]] = []
        try:
            items = self._wb.defined_names.items()
        except AttributeError:  # pragma: no cover - openpyxl API drift
            return ()
        for name, defn in items:
            out.append((str(name), str(getattr(defn, "attr_text", "") or "")))
        return tuple(out)

    def formulas(self) -> Iterator[Cell]:
        """Every formula in the workbook, sheet by sheet."""
        for sheet in self.sheets:
            yield from sheet.formulas()

    def _values_sheet(self, name: str) -> Any | None:
        """Lazily load the cached-values view, then return one sheet from it."""
        if not self._values_loaded:
            self._values_loaded = True
            try:
                self._values_wb = load_workbook(
                    self.path, data_only=True, read_only=True, keep_links=False
                )
            except Exception:
                # A workbook that opens for formulas but not for values should
                # degrade to "no cached errors found", never take down the audit.
                self._values_wb = None
        if self._values_wb is None:
            return None
        return self._values_wb[name] if name in self._values_wb.sheetnames else None

    def close(self) -> None:
        for wb in (self._wb, self._values_wb):
            if wb is not None:
                with contextlib.suppress(Exception):  # close is best-effort
                    wb.close()

    def __enter__(self) -> LoadedWorkbook:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def open_workbook(path: str | Path) -> LoadedWorkbook:
    """Open a workbook for auditing.

    Raises:
        UnsupportedFormatError: for ``.xls``, ``.xlsb`` and anything else not read.
        PasswordProtectedError: if the file is encrypted.
        CorruptWorkbookError: if the file cannot be parsed.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if not path.exists():
        raise UnsupportedFormatError(path, "file does not exist")

    if suffix == ".xls":
        raise UnsupportedFormatError(
            path,
            "legacy .xls is not supported — re-save as .xlsx and audit that",
        )
    if suffix == ".xlsb":
        raise UnsupportedFormatError(
            path,
            "binary .xlsb is not supported — re-save as .xlsx and audit that",
        )
    if suffix not in _SUPPORTED_SUFFIXES:
        raise UnsupportedFormatError(path, f"unsupported file type '{suffix or 'none'}'")

    try:
        # data_only=False keeps formulas. read_only=False is deliberate: the
        # streaming mode drops the row and column dimensions the inventory needs.
        # Neither flag has anything to do with writing — see the module docstring.
        wb = load_workbook(path, data_only=False, read_only=False, keep_links=True)
    except InvalidFileException as exc:
        raise UnsupportedFormatError(path, str(exc)) from exc
    except zipfile.BadZipFile as exc:
        # Encrypted workbooks are OLE containers, not zips, so this is the usual
        # symptom of password protection rather than of real corruption.
        raise PasswordProtectedError(
            path, "file is encrypted or corrupt; if it is password-protected, remove the password"
        ) from exc
    except Exception as exc:
        raise CorruptWorkbookError(path, f"could not parse workbook ({exc})") from exc

    return LoadedWorkbook(path, wb)
