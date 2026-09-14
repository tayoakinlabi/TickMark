"""What every check looks like.

Checks are deterministic functions from a workbook (or one sheet of it) to
findings. No model, no network, no state carried between runs — which is why this
product gets ordinary unit tests with fixture workbooks instead of a scored
golden set, and why it can be finished.

There are two shapes, because two of the checks genuinely are not per-sheet:

* :class:`SheetCheck` — the common case. Runs once per sheet.
* :class:`WorkbookCheck` — runs once per workbook. Check 5 needs this because a
  circular reference can run through three sheets, and check 7 because "the ten
  worst formulas" is a ranking across the whole book, not ten per sheet.

Pretending the second kind were per-sheet would mean either building the
dependency graph once per sheet, or reporting the same cycle once for every sheet
it touches. Both are worse than admitting there are two shapes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tickmark.findings.model import Finding
from tickmark.workbook.loader import LoadedWorkbook, Sheet

__all__ = ["Check", "SheetCheck", "WorkbookCheck"]


@runtime_checkable
class SheetCheck(Protocol):
    """A check that runs once per sheet."""

    name: str

    def run(self, sheet: Sheet) -> list[Finding]:
        """Return every finding this check makes on one sheet.

        Must not raise for malformed content. A single unparseable formula is
        skipped, never fatal — one bad cell must not cost a sheet its audit.
        """
        ...


@runtime_checkable
class WorkbookCheck(Protocol):
    """A check that runs once per workbook."""

    name: str

    def run_workbook(self, workbook: LoadedWorkbook) -> list[Finding]:
        """Return every finding this check makes across the whole workbook."""
        ...


# Retained as the general name for either shape.
Check = SheetCheck | WorkbookCheck
