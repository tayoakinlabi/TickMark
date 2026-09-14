from __future__ import annotations

import pytest

from tickmark.formula.references import (
    CellRef,
    NameRef,
    RangeRef,
    ReferenceParseError,
    TableRef,
    parse_reference,
)


class TestCellReferences:
    def test_plain_cell(self):
        ref = parse_reference("A1")
        assert ref == RangeRef(CellRef("A", 1))
        assert ref.is_single_cell
        assert not ref.is_external

    @pytest.mark.parametrize(
        ("text", "abs_col", "abs_row"),
        [("A1", False, False), ("$A1", True, False), ("A$1", False, True), ("$A$1", True, True)],
    )
    def test_absolute_markers(self, text, abs_col, abs_row):
        ref = parse_reference(text)
        assert ref.start.abs_column is abs_col
        assert ref.start.abs_row is abs_row

    def test_lowercase_is_normalized(self):
        assert parse_reference("bc12").start.column == "BC"

    def test_range(self):
        ref = parse_reference("A1:B10")
        assert ref.start == CellRef("A", 1)
        assert ref.end == CellRef("B", 10)
        assert not ref.is_single_cell

    def test_whole_column(self):
        ref = parse_reference("A:A")
        assert ref.start.row is None
        assert ref.start.column == "A"

    def test_whole_row(self):
        ref = parse_reference("1:1")
        assert ref.start.column is None
        assert ref.start.row == 1

    def test_mixed_whole_row_and_column_is_rejected(self):
        with pytest.raises(ReferenceParseError):
            parse_reference("A:1")


class TestColumnIndex:
    @pytest.mark.parametrize(("col", "idx"), [("A", 1), ("Z", 26), ("AA", 27), ("XFD", 16384)])
    def test_column_index(self, col, idx):
        assert CellRef(col, 1).column_index == idx

    def test_whole_row_has_no_column_index(self):
        assert CellRef(None, 5).column_index is None


class TestSheetAndWorkbook:
    def test_sheet_qualified(self):
        ref = parse_reference("Sheet1!A1")
        assert ref.sheet == "Sheet1"
        assert ref.workbook is None

    def test_quoted_sheet_with_space(self):
        ref = parse_reference("'Sales Q3'!$B$2")
        assert ref.sheet == "Sales Q3"
        assert ref.start == CellRef("B", 2, abs_column=True, abs_row=True)

    def test_quoted_sheet_with_escaped_quote(self):
        ref = parse_reference("'Bob''s Data'!A1")
        assert ref.sheet == "Bob's Data"

    def test_external_by_index(self):
        ref = parse_reference("[1]Budget!A1")
        assert ref.workbook == "1"
        assert ref.sheet == "Budget"
        assert ref.is_external

    def test_external_by_filename(self):
        ref = parse_reference("[Book.xlsx]Sheet1!A1")
        assert ref.workbook == "Book.xlsx"
        assert ref.sheet == "Sheet1"

    def test_quoted_external_prefix(self):
        ref = parse_reference("'[Book.xlsx]Sales Q3'!A1")
        assert ref.workbook == "Book.xlsx"
        assert ref.sheet == "Sales Q3"
        assert ref.is_external

    def test_sheet_qualified_range(self):
        ref = parse_reference("Data!$A:$C")
        assert ref.sheet == "Data"
        assert ref.end is not None

    def test_unterminated_quote_is_rejected(self):
        with pytest.raises(ReferenceParseError):
            parse_reference("'Sales Q3!A1")


class TestTableReferences:
    def test_single_column(self):
        assert parse_reference("Table1[Amount]") == TableRef("Table1", ("Amount",))

    def test_specifier_and_column(self):
        ref = parse_reference("Table1[[#Headers],[Amount]]")
        assert ref.table == "Table1"
        assert ref.columns == ("Amount",)
        assert ref.specifiers == ("#Headers",)

    def test_whole_table(self):
        assert parse_reference("Table1[]") == TableRef("Table1")

    def test_specifier_only(self):
        ref = parse_reference("Table1[#All]")
        assert ref.specifiers == ("#All",)
        assert ref.columns == ()


class TestDefinedNames:
    def test_bare_name(self):
        assert parse_reference("TaxRate") == NameRef("TaxRate")

    def test_sheet_scoped_name(self):
        ref = parse_reference("Sheet1!TaxRate")
        assert ref == NameRef("TaxRate", sheet="Sheet1")


class TestLimits:
    """Excel's grid limits, and what lies just past them.

    Text that is cell-shaped but outside the grid cannot be a cell, which is
    exactly why Excel permits it as a defined name. The limit check therefore
    does not reject these — it stops us from ever building a CellRef pointing
    off the grid, and hands them to the name branch instead.
    """

    def test_row_beyond_excel_maximum_becomes_a_name(self):
        assert parse_reference("A1048577") == NameRef("A1048577")

    def test_column_beyond_xfd_becomes_a_name(self):
        assert parse_reference("XFE1") == NameRef("XFE1")

    def test_last_valid_cell_is_still_a_cell(self):
        ref = parse_reference("XFD1048576")
        assert ref == RangeRef(CellRef("XFD", 1_048_576))

    def test_out_of_grid_corner_in_a_range_is_an_error(self):
        # A defined name cannot contain ':', so there is nothing to fall back to.
        with pytest.raises(ReferenceParseError):
            parse_reference("A1:A1048577")

    def test_empty_reference_is_rejected(self):
        with pytest.raises(ReferenceParseError):
            parse_reference("")
