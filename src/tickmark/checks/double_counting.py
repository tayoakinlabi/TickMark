"""Check 34 — a total that counts part of itself twice.

``=SUM(B2:B20)`` where ``B10`` is ``=SUM(B2:B9)``. The rows B2:B9 are added once
directly and again through the subtotal, so the grand total is inflated — silently,
by a plausible amount, with every individual formula looking correct.

**Invisible to every other check.** There is no cycle (B10 does not depend on
itself), no error value, no shape inconsistency, and nothing an evaluator would
flag either — the arithmetic is performed exactly as written. Only the *structure*
gives it away, which is why this is reachable without evaluation (T6, §10).

The condition is precise, and the precision is what keeps it quiet:

    a cell C inside range R, where C is itself an aggregate whose range
    overlaps R

Both halves matter. A cell inside R whose inputs come from somewhere else
entirely — ``B10 = SUM(D1:D5)`` — is just a number being included, which is
normal and common. Only the overlap makes it double counting.

Self-inclusion (``=SUM(B2:B20)`` living in B10) is left to check 5, which
reports it as the circular reference it is.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator

from tickmark.coordinates import coordinate
from tickmark.findings.model import Finding, Severity
from tickmark.formula.ast import FuncCall, ParseError, RefNode, parse, walk
from tickmark.formula.functions import is_aggregate, normalize
from tickmark.formula.references import RangeRef
from tickmark.graph.dependency import Region, region_from_ref
from tickmark.workbook.loader import Cell, Sheet

__all__ = ["CHECK_NAME", "DoubleCountingCheck"]

CHECK_NAME = "double-counting"


def _aggregate_regions(cell: Cell, sheet_name: str) -> Iterator[tuple[str, Region]]:
    """``(function name, region)`` for every aggregate range in a formula."""
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
            yield normalize(node.name), region_from_ref(ref, sheet_name)


class DoubleCountingCheck:
    """Check 34."""

    name = CHECK_NAME

    def run(self, sheet: Sheet) -> list[Finding]:
        formulas = {(c.row, c.column): c for c in sheet.formulas()}
        if not formulas:
            return []

        # Same per-column index the dependency graph uses, so a range costs one
        # bisect per column rather than a scan of the sheet.
        index: dict[int, list[int]] = {}
        for row, column in formulas:
            index.setdefault(column, []).append(row)
        for rows in index.values():
            rows.sort()

        findings: dict[tuple[int, int, str], Finding] = {}

        for (row, column), cell in formulas.items():
            for function, outer in _aggregate_regions(cell, sheet.name):
                for inner_cell in self._inside(outer, index, formulas):
                    if (inner_cell.row, inner_cell.column) == (row, column):
                        continue  # self-inclusion is check 5's finding
                    overlap = self._overlapping(inner_cell, outer, sheet.name)
                    if overlap is None:
                        continue
                    key = (row, column, inner_cell.coordinate)
                    findings.setdefault(
                        key, self._finding(cell, function, outer, inner_cell, overlap)
                    )

        return sorted(findings.values(), key=lambda f: (f.sheet, f.column, f.row))

    def _inside(
        self, region: Region, index: dict[int, list[int]], formulas: dict[tuple[int, int], Cell]
    ) -> Iterator[Cell]:
        for column in range(region.column_start, region.column_end + 1):
            rows = index.get(column)
            if not rows:
                continue
            left = bisect_left(rows, region.row_start)
            right = bisect_right(rows, region.row_end)
            for row in rows[left:right]:
                yield formulas[(row, column)]

    def _overlapping(self, inner: Cell, outer: Region, sheet_name: str) -> Region | None:
        for _function, region in _aggregate_regions(inner, sheet_name):
            if region.intersects(outer):
                return region
        return None

    def _finding(
        self, cell: Cell, function: str, outer: Region, inner: Cell, overlap: Region
    ) -> Finding:
        outer_span = (
            f"{coordinate(outer.row_start, outer.column_start)}:"
            f"{coordinate(outer.row_end, outer.column_end)}"
        )
        inner_span = (
            f"{coordinate(overlap.row_start, overlap.column_start)}:"
            f"{coordinate(overlap.row_end, overlap.column_end)}"
        )

        return Finding(
            check=CHECK_NAME,
            severity=Severity.HIGH,
            sheet=cell.sheet,
            row=cell.row,
            column=cell.column,
            summary=f"Double counting: {inner.coordinate} is a subtotal inside this range",
            explanation=(
                f"This {function} covers {outer_span}, which includes {inner.coordinate} — "
                f"and {inner.coordinate} is itself a total of {inner_span}. Those cells are "
                "counted twice: once directly and once through the subtotal. The result is "
                "too high, and nothing about either formula looks wrong on its own."
            ),
            formula=cell.formula,
            context={
                "range": outer_span,
                "subtotal_cell": inner.coordinate,
                "subtotal_range": inner_span,
            },
        )
