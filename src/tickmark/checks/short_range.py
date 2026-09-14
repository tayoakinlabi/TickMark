"""Check 33 — a range that stops short of the data it is clearly aimed at.

``=SUM(B2:B12)`` when the block runs to B13. Rows were added and the total was
never extended; the sheet looks perfectly healthy and the number is quietly wrong.
This is the most common costly spreadsheet error there is, and until this check
existed Tickmark could not see it — there is no shape inconsistency, no cycle and
no error value to notice.

**It needs no evaluator** (T6, §10). The question is geometric: does the block of
data continue immediately past the edge of the range? That is answerable from
cell occupancy alone, and it stays answerable on a workbook that was never
recalculated — which no evaluation-based approach can claim.

**Downward only, deliberately.** A range can also start too low — someone
inserted a row at the top of the block — but detecting that reliably is a
different problem. Above a range sit headers, section titles, spacer rows and
numeric labels like a year, none of which announce themselves, and several of
which are indistinguishable from data by any local test. Downward, the block
ends naturally at a blank or a label, which is a signal you can trust. Reporting
the ambiguous direction too would add false positives to the one check whose
value depends entirely on being believed. Recorded as a v1.1 question rather
than an oversight.

Three suppressions, each removing a real false positive:

1. **The formula's own cell.** ``=SUM(B2:B12)`` living in B13 must not be told to
   include B13. That is a circular reference, and check 5's job.
2. **Another total.** If the cell below is itself an aggregate covering the same
   range, it is a grand total, not a forgotten row — and telling the user to
   include it would be advice to double-count, which is check 34's finding.
3. **Text.** A "Total" label or a column heading is not one more row of data.
"""

from __future__ import annotations

from collections.abc import Iterator

from tickmark.coordinates import coordinate
from tickmark.findings.model import Finding, Severity
from tickmark.formula.ast import FuncCall, ParseError, RefNode, parse, walk
from tickmark.formula.functions import is_aggregate
from tickmark.formula.references import RangeRef
from tickmark.layout import CellMap, is_data_like
from tickmark.workbook.loader import Cell, Sheet

__all__ = ["CHECK_NAME", "ShortRangeCheck"]

CHECK_NAME = "short-range"

# How far past the edge to look. Beyond a couple of blank cells the block has
# genuinely ended, and a gap usually means a deliberate separation.
_MAX_EXTENSION = 500


def _aggregate_ranges(cell: Cell, sheet_name: str) -> Iterator[RangeRef]:
    """Single-axis ranges passed to an aggregate, on this sheet."""
    try:
        tree = parse(cell.formula or "")
    except ParseError:
        return

    for node in walk(tree):
        if not isinstance(node, FuncCall) or not is_aggregate(node.name):
            continue
        for arg in node.args:
            if not isinstance(arg, RefNode):
                continue
            ref = arg.reference
            if not isinstance(ref, RangeRef) or ref.is_external or ref.end is None:
                continue
            if ref.sheet not in (None, sheet_name):
                continue
            # Whole-column and whole-row references already cover everything;
            # they cannot stop short.
            if ref.start.row is None or ref.start.column is None:
                continue
            yield ref


def _is_another_total(cell: Cell | None, sheet_name: str, column: int, rows: range) -> bool:
    """True if ``cell`` is an aggregate covering the range we are extending from.

    A grand total sitting under a subtotal column is not a row the subtotal
    forgot — telling the user to include it would be advice to double-count.
    """
    if cell is None or not cell.is_formula:
        return False
    for ref in _aggregate_ranges(cell, sheet_name):
        columns = range(
            min(ref.start.column_index or 0, ref.end.column_index or 0),
            max(ref.start.column_index or 0, ref.end.column_index or 0) + 1,
        )
        if column in columns and set(range(ref.start.row or 0, (ref.end.row or 0) + 1)) & set(rows):
            return True
    return False


class ShortRangeCheck:
    """Check 33."""

    name = CHECK_NAME

    def run(self, sheet: Sheet) -> list[Finding]:
        cells: CellMap = {(c.row, c.column): c for c in sheet.cells()}
        if not cells:
            return []

        findings: dict[tuple[int, int, str], Finding] = {}

        for cell in sheet.formulas():
            for ref in _aggregate_ranges(cell, sheet.name):
                finding = self._examine(cell, ref, cells, sheet.name)
                if finding is not None:
                    key = (cell.row, cell.column, finding.context["range"])
                    findings.setdefault(key, finding)

        return sorted(findings.values(), key=lambda f: (f.sheet, f.column, f.row))

    def _examine(
        self,
        cell: Cell,
        ref: RangeRef,
        cells: CellMap,
        sheet_name: str,
    ) -> Finding | None:
        start_column = ref.start.column_index or 0
        end_column = ref.end.column_index or 0 if ref.end else start_column
        row_start = min(ref.start.row or 0, ref.end.row or 0)
        row_end = max(ref.start.row or 0, ref.end.row or 0)

        # Only single-column ranges are handled. A rectangular block has four
        # edges and far more legitimate reasons to stop where it does; guessing
        # there would cost more in false positives than it earns.
        if start_column != end_column:
            return None
        column = start_column
        covered = range(row_start, row_end + 1)

        missed = self._extend_below(cells, column, row_end, cell, sheet_name, covered)
        if not missed:
            return None
        span = f"{coordinate(row_start, column)}:{coordinate(row_end, column)}"
        first, last = coordinate(missed[0], column), coordinate(missed[-1], column)
        listed = first if len(missed) == 1 else f"{first}:{last}"
        plural = "" if len(missed) == 1 else "s"

        return Finding(
            check=CHECK_NAME,
            severity=Severity.HIGH,
            sheet=cell.sheet,
            row=cell.row,
            column=cell.column,
            summary=f"Range stops short: {listed} left out",
            explanation=(
                f"This formula covers {span}, but the data in that column continues "
                f"at {listed}. {len(missed)} row{plural} of figures are missing from the "
                "total. Usually this means rows were added and the formula was never "
                "extended to reach them."
            ),
            formula=cell.formula,
            context={
                "range": span,
                "missed": listed,
                "missed_count": str(len(missed)),
            },
        )

    def _extend_below(
        self,
        cells: CellMap,
        column: int,
        edge: int,
        origin: Cell,
        sheet_name: str,
        covered: range,
    ) -> list[int]:
        """Rows of data continuing below the bottom of the range."""
        found: list[int] = []
        row = edge + 1

        for _ in range(_MAX_EXTENSION):
            neighbour = cells.get((row, column))

            # A blank ends the block, whatever lies beyond it.
            if not is_data_like(neighbour):
                break
            # The formula's own cell. Including it would be circular — check 5's
            # territory, not a forgotten row.
            if neighbour is origin or (neighbour.row, neighbour.column) == (
                origin.row,
                origin.column,
            ):
                break
            # A grand total under a subtotal is not a missing row.
            if _is_another_total(neighbour, sheet_name, column, covered):
                break

            found.append(row)
            row += 1

        return found
