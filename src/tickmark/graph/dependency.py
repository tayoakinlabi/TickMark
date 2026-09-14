"""The dependency graph (item 8).

Not a feature — the engine checks 5 and 7 stand on. For every formula cell it
answers "what does this read?" and "what reads this?".

**Ranges are never expanded.** ``=SUM(A:A)`` names 1,048,576 cells; materialising
that would exhaust memory on a workbook that opens fine in Excel. Instead a
reference is kept as a *region*, and edges are found by asking which formula
cells fall inside it. Since only formula cells can participate in a cycle — a
constant has nothing to point at — the node set is the formulas, which is
typically a small fraction of the sheet.

Lookups use a per-column index of formula rows, so a region costs one bisect per
column it spans rather than a scan of every cell in the workbook. That is what
keeps a whole-column reference cheap.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass

from tickmark.formula.ast import ParseError, RefNode, parse, walk
from tickmark.formula.references import CellRef, RangeRef
from tickmark.workbook.loader import Cell, LoadedWorkbook

__all__ = ["CellKey", "DependencyGraph", "Region", "region_from_ref"]

_MAX_ROW = 1_048_576
_MAX_COLUMN = 16_384


@dataclass(frozen=True, slots=True)
class CellKey:
    """A cell's identity across the whole workbook."""

    sheet: str
    row: int
    column: int

    def __str__(self) -> str:
        from tickmark.coordinates import coordinate

        return f"{self.sheet}!{coordinate(self.row, self.column)}"


@dataclass(frozen=True, slots=True)
class Region:
    """A rectangle of cells a formula reads from, kept unexpanded."""

    sheet: str
    row_start: int
    row_end: int
    column_start: int
    column_end: int

    def contains(self, key: CellKey) -> bool:
        return (
            key.sheet == self.sheet
            and self.row_start <= key.row <= self.row_end
            and self.column_start <= key.column <= self.column_end
        )

    def intersects(self, other: Region) -> bool:
        """True if the two rectangles share at least one cell.

        Check 34 turns on this: a subtotal inside a total's range only
        double-counts when the two ranges overlap. A cell inside the range whose
        own inputs lie somewhere else entirely is just a number being included,
        which is perfectly normal.
        """
        return (
            self.sheet == other.sheet
            and self.row_start <= other.row_end
            and other.row_start <= self.row_end
            and self.column_start <= other.column_end
            and other.column_start <= self.column_end
        )


def region_from_ref(ref: RangeRef, default_sheet: str) -> Region:
    """Build a region from a parsed range reference."""
    row_start, row_end, column_start, column_end = _bounds(ref.start, ref.end)
    return Region(ref.sheet or default_sheet, row_start, row_end, column_start, column_end)


def _bounds(ref: CellRef, other: CellRef | None) -> tuple[int, int, int, int]:
    """Rectangle covered by one or two corners, filling in open axes.

    A whole-column reference has no row, a whole-row reference has no column; both
    become the full extent of that axis rather than an error.
    """
    rows = [r for r in (ref.row, other.row if other else None) if r is not None]
    columns = [
        c for c in (ref.column_index, other.column_index if other else None) if c is not None
    ]
    row_start, row_end = (min(rows), max(rows)) if rows else (1, _MAX_ROW)
    column_start, column_end = (min(columns), max(columns)) if columns else (1, _MAX_COLUMN)
    return row_start, row_end, column_start, column_end


def _regions(cell: Cell, default_sheet: str) -> Iterator[Region]:
    """Every region a formula reads from.

    External references are skipped: they leave the workbook, so no edge inside
    it can represent them, and a cycle through another file is not something this
    graph can see.
    """
    try:
        tree = parse(cell.formula or "")
    except ParseError:
        return

    for node in walk(tree):
        if not isinstance(node, RefNode):
            continue
        ref = node.reference
        if not isinstance(ref, RangeRef) or ref.is_external:
            continue
        yield region_from_ref(ref, default_sheet)


class DependencyGraph:
    """Formula-to-formula dependencies across a workbook."""

    def __init__(self, workbook: LoadedWorkbook) -> None:
        self._formulas: dict[CellKey, Cell] = {}
        # (sheet, column) -> sorted rows holding formulas, for fast region lookup.
        self._index: dict[tuple[str, int], list[int]] = {}
        self._precedents: dict[CellKey, frozenset[CellKey]] = {}
        self._dependents: dict[CellKey, set[CellKey]] = {}

        self._collect(workbook)
        self._build()

    # -- construction --

    def _collect(self, workbook: LoadedWorkbook) -> None:
        for sheet in workbook.sheets:
            for cell in sheet.formulas():
                key = CellKey(sheet.name, cell.row, cell.column)
                self._formulas[key] = cell
                self._index.setdefault((sheet.name, cell.column), []).append(cell.row)
        for rows in self._index.values():
            rows.sort()

    def _build(self) -> None:
        for key, cell in self._formulas.items():
            precedents: set[CellKey] = set()
            for region in _regions(cell, key.sheet):
                precedents.update(self._formulas_in(region))
            frozen = frozenset(precedents)
            self._precedents[key] = frozen
            for precedent in frozen:
                self._dependents.setdefault(precedent, set()).add(key)

    def _formulas_in(self, region: Region) -> Iterator[CellKey]:
        """Formula cells inside a region, without expanding it."""
        for column in range(region.column_start, region.column_end + 1):
            rows = self._index.get((region.sheet, column))
            if not rows:
                continue
            left = bisect_left(rows, region.row_start)
            right = bisect_right(rows, region.row_end)
            for row in rows[left:right]:
                yield CellKey(region.sheet, row, column)

    # -- queries --

    @property
    def cells(self) -> frozenset[CellKey]:
        """Every formula cell in the workbook."""
        return frozenset(self._formulas)

    def formula(self, key: CellKey) -> str | None:
        cell = self._formulas.get(key)
        return cell.formula if cell else None

    def precedents(self, key: CellKey) -> frozenset[CellKey]:
        """Formula cells this one reads from."""
        return self._precedents.get(key, frozenset())

    def dependents(self, key: CellKey) -> frozenset[CellKey]:
        """Formula cells that read from this one."""
        return frozenset(self._dependents.get(key, ()))

    def reads_itself(self, key: CellKey) -> bool:
        """True if a formula's own region covers the cell it lives in.

        The classic accident: ``=SUM(A1:A5)`` typed into ``A3``.
        """
        cell = self._formulas.get(key)
        if cell is None:
            return False
        return any(region.contains(key) for region in _regions(cell, key.sheet))
