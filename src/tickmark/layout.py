"""Reading a sheet's shape: which lines are data and which are calculation.

Extracted from check 1 once check 33 needed the same header detection. Both ask
the same question — "is this cell part of a label or input line, or part of the
formula block?" — and answering it two different ways would eventually mean two
different answers on the same sheet.

Rows and columns are **not** symmetric here, and assuming they were is the
mistake this module exists to prevent:

* A **column** of constants is data wherever it sits — a label column, an input
  column. Nothing about its position changes that.
* A **row** of constants is a header only when it sits at the *top* of the used
  range. An all-constant row in the middle of a formula block is precisely the
  bug check 1 hunts: in a narrow ``input | formula`` sheet, overwriting the
  formula is exactly what makes its row all-constant.
"""

from __future__ import annotations

from tickmark.workbook.loader import Cell

__all__ = ["CellMap", "header_rows", "input_columns", "is_data_like"]

CellMap = dict[tuple[int, int], Cell]

# A column must hold at least this many cells before "it contains no formulas"
# counts as evidence rather than coincidence.
_MIN_LINE_CELLS = 2


def input_columns(cells: CellMap) -> set[int]:
    """Columns holding no formulas at all — label and input columns."""
    totals: dict[int, int] = {}
    formulas: dict[int, int] = {}
    for (_row, column), cell in cells.items():
        totals[column] = totals.get(column, 0) + 1
        if cell.is_formula:
            formulas[column] = formulas.get(column, 0) + 1

    return {
        column
        for column, total in totals.items()
        if total >= _MIN_LINE_CELLS and formulas.get(column, 0) == 0
    }


def header_rows(cells: CellMap) -> set[int]:
    """The band of label rows at the top of the used range.

    A header row must be both **formula-free** and **text-dominant**. The second
    condition is not decoration: without it, a column of numbers with a single
    ``=SUM`` at the bottom classifies every data row above the total as a header,
    because none of them contains a formula either. That is the commonest layout
    in any accounts workbook, so a definition that mislabels it is wrong about
    the ordinary case rather than an edge case.

    Multi-row headers are handled by walking down until the first row that
    breaks either condition.
    """
    has_formula: dict[int, bool] = {}
    text_cells: dict[int, int] = {}
    filled: dict[int, int] = {}

    for (row, _column), cell in cells.items():
        has_formula[row] = has_formula.get(row, False) or cell.is_formula
        filled[row] = filled.get(row, 0) + 1
        if isinstance(cell.value, str) and cell.value.strip():
            text_cells[row] = text_cells.get(row, 0) + 1

    header: set[int] = set()
    for row in sorted(has_formula):
        if has_formula[row]:
            break
        if text_cells.get(row, 0) * 2 < filled.get(row, 0):
            break
        header.add(row)
    return header


def is_data_like(cell: Cell | None) -> bool:
    """True if a cell looks like part of a numeric data block.

    Text stops a block. That is what keeps a "Total" label or a column heading
    from being read as one more row of data that a range forgot to include —
    without needing to know anything about what the text says.
    """
    if cell is None:
        return False
    if cell.is_formula:
        return True
    return isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool)
