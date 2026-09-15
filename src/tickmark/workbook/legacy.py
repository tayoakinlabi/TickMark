"""``.xls`` presented through the same interface as ``.xlsx``.

Every check in the product is written against :class:`~tickmark.workbook.loader.Sheet`
and :class:`~tickmark.workbook.loader.LoadedWorkbook`. Rather than teach nine
checks about a second file format, this module makes a legacy workbook *look*
like the one they already know — same attributes, same iteration order, same
1-based addressing, same ``Cell`` type.

The split of labour is worth stating plainly, because it is what keeps the
legacy path small: ``xlrd`` supplies values, visibility, hidden rows and columns,
defined names and error codes; :mod:`tickmark.workbook.biff` supplies the one
thing ``xlrd`` cannot, the formula text. This module joins the two.

``xlrd`` is 0-based throughout and Tickmark is 1-based, matching Excel. Every
conversion happens here, at the boundary, so nothing above this file has to know
which format it is auditing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from functools import cached_property
from pathlib import Path
from typing import Any

from tickmark.workbook.biff import LegacyBook, read_legacy_workbook
from tickmark.workbook.loader import (
    Cell,
    CorruptWorkbookError,
    LoadedWorkbook,
    PasswordProtectedError,
    Sheet,
    UnsupportedFormatError,
)

__all__ = ["LegacySheet", "LegacyWorkbook", "open_legacy_workbook"]

# xlrd's cell type codes.
_XL_CELL_EMPTY = 0
_XL_CELL_TEXT = 1
_XL_CELL_NUMBER = 2
_XL_CELL_DATE = 3
_XL_CELL_BOOLEAN = 4
_XL_CELL_ERROR = 5
_XL_CELL_BLANK = 6

# xlrd's sheet visibility codes.
_VISIBLE = 0
_HIDDEN = 1
_VERY_HIDDEN = 2


def _error_text(code: object) -> str:
    """Render an xlrd error code as the text Excel shows.

    Check 3 matches on ``#REF!`` and friends, so a bare integer code would make
    every legacy error invisible.
    """
    from xlrd.biffh import error_text_from_code

    try:
        return str(error_text_from_code[int(code)])  # type: ignore[index]
    except Exception:  # noqa: BLE001 - an unknown code is still an error
        return "#VALUE!"


class LegacySheet(Sheet):
    """One worksheet of a ``.xls`` file.

    Subclasses :class:`Sheet` so that annotations and ``isinstance`` checks
    elsewhere keep working; every member that would have touched openpyxl is
    overridden.
    """

    def __init__(self, workbook: LegacyWorkbook, sheet: Any, formulas: dict) -> None:
        self._workbook = workbook
        self._sheet = sheet
        self._formulas = formulas

    @property
    def workbook(self) -> LegacyWorkbook:
        return self._workbook

    @property
    def name(self) -> str:
        return str(self._sheet.name)

    @property
    def is_hidden(self) -> bool:
        return int(getattr(self._sheet, "visibility", _VISIBLE)) != _VISIBLE

    @property
    def is_very_hidden(self) -> bool:
        return int(getattr(self._sheet, "visibility", _VISIBLE)) == _VERY_HIDDEN

    @property
    def max_row(self) -> int:
        return int(self._sheet.nrows or 0)

    @property
    def max_column(self) -> int:
        return int(self._sheet.ncols or 0)

    @property
    def hidden_rows(self) -> tuple[int, ...]:
        info = getattr(self._sheet, "rowinfo_map", None) or {}
        return tuple(sorted(index + 1 for index, row in info.items() if getattr(row, "hidden", 0)))

    @property
    def hidden_columns(self) -> tuple[str, ...]:
        info = getattr(self._sheet, "colinfo_map", None) or {}
        return tuple(
            sorted(
                _column_letter(index + 1)
                for index, column in info.items()
                if getattr(column, "hidden", 0)
            )
        )

    def _value(self, row: int, column: int) -> object:
        """The cached value at a 0-based address, in Tickmark's value domain."""
        kind = self._sheet.cell_type(row, column)
        if kind in (_XL_CELL_EMPTY, _XL_CELL_BLANK):
            return None
        raw = self._sheet.cell_value(row, column)
        if kind == _XL_CELL_ERROR:
            return _error_text(raw)
        if kind == _XL_CELL_BOOLEAN:
            return bool(raw)
        return raw

    def cells(self) -> Iterator[Cell]:
        """Every non-empty cell, row-major, formulas and literals alike.

        A cell carrying a formula is yielded as a formula even though ``xlrd``
        also has a cached value for it — matching the ``.xlsx`` path, where
        formulas and values come from two separate loads.
        """
        for row in range(self.max_row):
            for column in range(self.max_column):
                formula = self._formulas.get((row + 1, column + 1))
                if formula is not None:
                    yield Cell(self.name, row + 1, column + 1, formula=formula)
                    continue
                value = self._value(row, column)
                if value is None:
                    continue
                yield Cell(self.name, row + 1, column + 1, value=value)

    def error_values(self) -> Iterator[tuple[int, int, str]]:
        for row in range(self.max_row):
            for column in range(self.max_column):
                if self._sheet.cell_type(row, column) == _XL_CELL_ERROR:
                    yield row + 1, column + 1, _error_text(self._sheet.cell_value(row, column))


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


class LegacyWorkbook(LoadedWorkbook):
    """An opened ``.xls`` file. Read-only, like its modern counterpart."""

    def __init__(self, path: Path, book: Any, legacy: LegacyBook) -> None:
        self.path = path
        self._book = book
        self._legacy = legacy
        self._value_cache: dict[str, dict[tuple[int, int], object]] = {}

    @cached_property
    def sheets(self) -> tuple[LegacySheet, ...]:
        return tuple(
            LegacySheet(self, sheet, self._legacy.formulas_for(sheet.name))
            for sheet in self._book.sheets()
        )

    @property
    def has_macros(self) -> bool:
        """True if the container holds a VBA project.

        Read from the OLE container rather than from a file extension: ``.xls``
        has no macro-bearing variant the way ``.xlsm`` is distinct from
        ``.xlsx``, so the extension says nothing at all here.
        """
        return self._legacy.has_macros

    @cached_property
    def external_links(self) -> dict[int, str]:
        """Empty for legacy files — see the limitation in :mod:`~tickmark.workbook.biff`.

        Check 4 still reports external references; it just cannot name the file
        on disk. Returning an empty map is what makes it degrade that way rather
        than claim a wrong target.
        """
        return {}

    @cached_property
    def defined_names(self) -> tuple[tuple[str, str], ...]:
        out: list[tuple[str, str]] = []
        for name in getattr(self._book, "name_obj_list", ()) or ():
            result = getattr(name, "result", None)
            text = ""
            if result is not None:
                text = str(getattr(result, "text", "") or "")
            out.append((str(name.name), text))
        return tuple(out)

    def cached_values(self, name: str) -> dict[tuple[int, int], object]:
        if name in self._value_cache:
            return self._value_cache[name]
        sheet = self.sheet(name)
        table: dict[tuple[int, int], object] = {}
        if isinstance(sheet, LegacySheet):
            for row in range(sheet.max_row):
                for column in range(sheet.max_column):
                    value = sheet._value(row, column)
                    if value is not None:
                        table[(row + 1, column + 1)] = value
        self._value_cache[name] = table
        return table

    def close(self) -> None:
        release = getattr(self._book, "release_resources", None)
        if release is not None:
            with contextlib.suppress(Exception):  # closing is best-effort
                release()


def open_legacy_workbook(path: Path) -> LegacyWorkbook:
    """Open a ``.xls`` file, raising the same errors as the modern path."""
    try:
        import olefile  # noqa: F401
        import xlrd
    except ImportError as exc:  # pragma: no cover - dependencies are declared
        raise UnsupportedFormatError(
            path, "legacy .xls support requires the 'xlrd' and 'olefile' packages"
        ) from exc

    if not olefile.isOleFile(str(path)):
        # A .xls that is not an OLE container is usually something else wearing
        # the extension: an HTML or CSV export Excel will open but this will not.
        raise UnsupportedFormatError(
            path, "not a real .xls file — the extension may not match the contents"
        )

    try:
        # formatting_info is what carries hidden rows and columns; xlrd supports
        # it for .xls only, which is the one format this path ever sees.
        book = xlrd.open_workbook(str(path), formatting_info=True, on_demand=False)
    except xlrd.XLRDError as exc:
        message = str(exc)
        if "encrypt" in message.lower() or "password" in message.lower():
            raise PasswordProtectedError(
                path, "file is password-protected; remove the password and audit again"
            ) from exc
        raise CorruptWorkbookError(path, f"could not parse workbook ({exc})") from exc
    except Exception as exc:
        raise CorruptWorkbookError(path, f"could not parse workbook ({exc})") from exc

    try:
        legacy = read_legacy_workbook(str(path), book)
    except Exception as exc:
        raise CorruptWorkbookError(path, f"could not read legacy formulas ({exc})") from exc

    return LegacyWorkbook(path, book, legacy)
