"""Workbook inventory (item 9).

The part of the report that is not a finding. Before anyone reads what is wrong,
they need to know what they are looking at — how many sheets, how much of it is
calculation, what is hidden, and whether macros are involved.

Several of these are facts an auditor specifically asks for: a veryHidden sheet
cannot be unhidden through the Excel UI at all, and a workbook with a VBA project
can behave in ways no static audit can see. Reporting them is not a finding; it is
telling the reader what this audit could not cover.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tickmark.workbook.loader import LoadedWorkbook

__all__ = ["Inventory", "SheetInventory", "take_inventory"]


@dataclass(frozen=True, slots=True)
class SheetInventory:
    name: str
    is_hidden: bool
    is_very_hidden: bool
    max_row: int
    max_column: int
    cell_count: int
    formula_count: int
    hidden_rows: tuple[int, ...] = ()
    hidden_columns: tuple[str, ...] = ()

    @property
    def used_range(self) -> str:
        from tickmark.coordinates import column_letter

        if not self.cell_count:
            return "empty"
        return f"A1:{column_letter(max(self.max_column, 1))}{max(self.max_row, 1)}"


@dataclass(frozen=True, slots=True)
class Inventory:
    name: str
    sheets: tuple[SheetInventory, ...]
    has_macros: bool
    defined_names: tuple[tuple[str, str], ...] = ()
    external_links: dict[int, str] = field(default_factory=dict)

    @property
    def formula_count(self) -> int:
        return sum(s.formula_count for s in self.sheets)

    @property
    def cell_count(self) -> int:
        return sum(s.cell_count for s in self.sheets)

    @property
    def hidden_sheets(self) -> tuple[SheetInventory, ...]:
        return tuple(s for s in self.sheets if s.is_hidden)


def take_inventory(workbook: LoadedWorkbook) -> Inventory:
    """Describe a workbook without judging it."""
    sheets: list[SheetInventory] = []

    for sheet in workbook.sheets:
        cells = 0
        formulas = 0
        for cell in sheet.cells():
            cells += 1
            if cell.is_formula:
                formulas += 1
        sheets.append(
            SheetInventory(
                name=sheet.name,
                is_hidden=sheet.is_hidden,
                is_very_hidden=sheet.is_very_hidden,
                max_row=sheet.max_row,
                max_column=sheet.max_column,
                cell_count=cells,
                formula_count=formulas,
                hidden_rows=sheet.hidden_rows,
                hidden_columns=sheet.hidden_columns,
            )
        )

    return Inventory(
        name=workbook.name,
        sheets=tuple(sheets),
        has_macros=workbook.has_macros,
        defined_names=workbook.defined_names,
        external_links=dict(workbook.external_links),
    )
