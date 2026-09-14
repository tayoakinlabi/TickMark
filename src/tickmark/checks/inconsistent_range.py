"""Check 1 — an inconsistent formula in an otherwise uniform run.

The highest-value check in the product. A column of formulas where one cell was
overwritten — with a constant, or with a formula of a different shape — is where
money actually goes missing, because the sheet still looks right.

How it works:

1. Walk each column (then each row) and cut it into *maximal contiguous runs* of
   non-empty cells. Contiguity matters: a blank row is a deliberate break, and a
   run that spans one would compare a total against the rows above it.
2. Normalise every formula in a run to its R1C1 shape, via
   :mod:`tickmark.formula.shape`. Correctly filled-down formulas collapse to one
   shape; that is the whole trick.
3. If a run is long enough and one shape dominates it, every cell that departs
   from the dominant shape is a finding.

Three deliberate limits, all there to keep the signal high:

* **Minimum run length.** In a run of three, "two against one" is not evidence.
* **Dominance threshold.** If a run is a genuine mixture, there is no majority to
  depart from, and reporting every cell in it would be noise rather than a
  finding.
* **The data-line test.** A cell is not a departure if it belongs to a line that
  is data rather than calculation. Without this, two false positives appear in
  essentially every real workbook:

  - A **header row**. ``C1`` holding the text "VAT" sits in the same contiguous
    column run as the formulas beneath it, and looks exactly like an overwritten
    formula.
  - An **input column**. In ``label | amount | =tax | =total``, the amount is a
    legitimate input, not a broken formula.

  Rows and columns are treated **asymmetrically**, and that asymmetry is the
  whole point. A column of constants is data wherever it sits. A *row* of
  constants is only a header when it sits at the top of the used range — because
  an all-constant row in the middle of a formula block is exactly the bug, and in
  a narrow ``input | formula`` sheet, overwriting the formula is what makes its
  row all-constant. Treating the two axes the same way suppresses the best
  finding the product makes.

Rows are only reported where the column pass did not already cover the cell, so a
single overwritten cell in a grid produces one finding rather than two.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from tickmark.findings.model import Finding, Severity
from tickmark.formula.shape import ShapeError, formula_shape
from tickmark.layout import header_rows, input_columns
from tickmark.workbook.loader import Cell, Sheet

__all__ = ["CHECK_NAME", "InconsistentRangeCheck"]

CHECK_NAME = "inconsistent-range"

# A departure has to stand against enough neighbours to mean something.
_DEFAULT_MIN_RUN = 4
# Share of a run that must agree before the rest count as departures.
_DEFAULT_DOMINANCE = 0.75


@dataclass(frozen=True, slots=True)
class _Shaped:
    cell: Cell
    shape: str
    is_formula: bool


def _shape_of(cell: Cell) -> _Shaped | None:
    """Reduce a cell to the token check 1 compares.

    Literals collapse to a single ``<constant>`` shape rather than their value: a
    run of formulas interrupted by 4200 and one interrupted by 0 are the same
    finding, and keying on the value would split them apart.
    """
    if cell.is_formula:
        try:
            shape = formula_shape(cell.formula or "", cell.row, cell.column)
        except ShapeError:
            return None
        return _Shaped(cell, shape, True)
    return _Shaped(cell, "<constant>", False)


def _runs(cells: dict[tuple[int, int], Cell], *, by_column: bool) -> Iterator[list[Cell]]:
    """Yield maximal contiguous runs along columns (or rows)."""
    groups: dict[int, list[int]] = {}
    for row, col in cells:
        key, position = (col, row) if by_column else (row, col)
        groups.setdefault(key, []).append(position)

    for key, positions in groups.items():
        positions.sort()
        run: list[Cell] = []
        previous: int | None = None
        for position in positions:
            if previous is not None and position != previous + 1:
                if run:
                    yield run
                run = []
            coord = (position, key) if by_column else (key, position)
            run.append(cells[coord])
            previous = position
        if run:
            yield run


def _analyse(run: list[Cell], *, min_run: int, dominance: float) -> Iterator[tuple[Cell, str, str]]:
    """Yield ``(cell, its shape, the dominant shape)`` for departures in a run."""
    if len(run) < min_run:
        return

    shaped = [s for s in (_shape_of(c) for c in run) if s is not None]
    if len(shaped) < min_run:
        return

    # A run of pure constants is a data column, not a broken formula column.
    if not any(s.is_formula for s in shaped):
        return

    counts = Counter(s.shape for s in shaped)
    dominant, dominant_count = counts.most_common(1)[0]
    if dominant_count / len(shaped) < dominance:
        return
    if dominant == "<constant>":
        # The majority are literals; a formula among them is unusual but it is
        # not the "someone overwrote the formula" bug, and calling it that would
        # be wrong.
        return

    for entry in shaped:
        if entry.shape != dominant:
            yield entry.cell, entry.shape, dominant


class InconsistentRangeCheck:
    """Check 1."""

    name = CHECK_NAME

    def __init__(
        self, *, min_run: int = _DEFAULT_MIN_RUN, dominance: float = _DEFAULT_DOMINANCE
    ) -> None:
        self.min_run = min_run
        self.dominance = dominance

    def run(self, sheet: Sheet) -> list[Finding]:
        cells = {(c.row, c.column): c for c in sheet.cells()}
        if not cells:
            return []

        data_rows = header_rows(cells)
        data_columns = input_columns(cells)

        findings: dict[tuple[int, int], Finding] = {}

        for by_column in (True, False):
            # When scanning down a column, the perpendicular line is the row;
            # when scanning across a row, it is the column.
            veto = data_rows if by_column else data_columns
            for run in _runs(cells, by_column=by_column):
                for cell, shape, dominant in self._departures(run):
                    key = (cell.row, cell.column)
                    if key in findings:
                        continue
                    if (cell.row if by_column else cell.column) in veto:
                        continue
                    findings[key] = self._finding(cell, shape, dominant, by_column)

        return sorted(findings.values(), key=lambda f: (f.sheet, f.column, f.row))

    def _departures(self, run: list[Cell]) -> Iterable[tuple[Cell, str, str]]:
        return _analyse(run, min_run=self.min_run, dominance=self.dominance)

    def _finding(self, cell: Cell, shape: str, dominant: str, by_column: bool) -> Finding:
        axis = "column" if by_column else "row"
        if not cell.is_formula:
            summary = f"Constant in a {axis} of formulas"
            explanation = (
                f"Every other cell in this {axis} holds a formula, but this one holds "
                f"the fixed value {cell.value!r}. That usually means a formula was "
                "typed over by hand, and it will not update when the inputs change."
            )
            severity = Severity.HIGH
        else:
            summary = f"Formula differs from the rest of the {axis}"
            explanation = (
                f"The surrounding cells in this {axis} all share one formula pattern "
                "and this one does not. Either it was edited on purpose, or a "
                "fill-down went wrong."
            )
            severity = Severity.MEDIUM

        return Finding(
            check=CHECK_NAME,
            severity=severity,
            sheet=cell.sheet,
            row=cell.row,
            column=cell.column,
            summary=summary,
            explanation=explanation,
            formula=cell.formula,
            context={"expected_shape": dominant, "actual_shape": shape, "axis": axis},
        )
