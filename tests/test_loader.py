from __future__ import annotations

from pathlib import Path

import pytest

from tickmark.workbook import (
    Cell,
    UnsupportedFormatError,
    WorkbookError,
    open_workbook,
)


class TestFormulaDiscovery:
    def test_finds_every_formula(self, simple_workbook: Path):
        with open_workbook(simple_workbook) as wb:
            formulas = list(wb.formulas())
        assert len(formulas) == 5
        assert all(f.sheet == "Sales" for f in formulas)

    def test_coordinates_are_one_based(self, simple_workbook: Path):
        with open_workbook(simple_workbook) as wb:
            by_coord = {f.coordinate: f.formula for f in wb.formulas()}
        assert by_coord["C2"] == "=B2*1.075"
        assert by_coord["C6"] == "=SUM(C2:C5)"

    def test_values_are_not_mistaken_for_formulas(self, simple_workbook: Path):
        with open_workbook(simple_workbook) as wb:
            texts = [f.formula for f in wb.formulas()]
        assert all(t.startswith("=") for t in texts)
        assert "Item" not in texts


class TestCoordinate:
    @pytest.mark.parametrize(
        ("column", "expected"),
        [(1, "A1"), (26, "Z1"), (27, "AA1"), (52, "AZ1"), (16384, "XFD1")],
    )
    def test_column_letters(self, column: int, expected: str):
        assert Cell("S", 1, column, "=1").coordinate == expected


class TestInventorySignals:
    def test_hidden_sheets(self, messy_workbook: Path):
        with open_workbook(messy_workbook) as wb:
            hidden = {s.name for s in wb.sheets if s.is_hidden}
        assert hidden == {"Hidden", "VeryHidden"}

    def test_very_hidden_is_distinguished(self, messy_workbook: Path):
        with open_workbook(messy_workbook) as wb:
            very = {s.name for s in wb.sheets if s.is_very_hidden}
        # The Excel UI cannot unhide these, so they deserve their own mention.
        assert very == {"VeryHidden"}

    def test_hidden_rows_and_columns(self, messy_workbook: Path):
        with open_workbook(messy_workbook) as wb:
            data = wb.sheet("Data")
            assert 3 in data.hidden_rows
            assert "D" in data.hidden_columns

    def test_defined_names(self, messy_workbook: Path):
        with open_workbook(messy_workbook) as wb:
            names = dict(wb.defined_names)
        assert "TaxRate" in names

    def test_macro_presence_is_false_for_xlsx(self, simple_workbook: Path):
        with open_workbook(simple_workbook) as wb:
            assert wb.has_macros is False


class TestRejections:
    def test_xls_extension_on_a_non_ole_file_is_refused(self, fake_xls: Path):
        # .xls is read now, but only when the bytes really are a BIFF container.
        # An HTML or CSV export wearing the extension is common enough that the
        # message has to say what is actually wrong rather than "corrupt".
        with pytest.raises(UnsupportedFormatError) as excinfo:
            open_workbook(fake_xls)
        assert "not a real .xls" in str(excinfo.value)

    def test_xlsb_is_still_refused(self, tmp_path: Path):
        # .xlsb is a third format again, neither OOXML nor BIFF8: still out.
        path = tmp_path / "book.xlsb"
        path.write_bytes(b"not a workbook")
        with pytest.raises(UnsupportedFormatError) as excinfo:
            open_workbook(path)
        assert "re-save as .xlsx" in str(excinfo.value)

    def test_non_workbook_is_refused(self, not_a_workbook: Path):
        with pytest.raises(WorkbookError):
            open_workbook(not_a_workbook)

    def test_missing_file_is_refused(self, tmp_path: Path):
        with pytest.raises(WorkbookError):
            open_workbook(tmp_path / "nope.xlsx")

    def test_errors_carry_the_path_for_the_failure_report(self, fake_xls: Path):
        # These land in the "could not read" report, so both fields must survive.
        with pytest.raises(WorkbookError) as excinfo:
            open_workbook(fake_xls)
        assert excinfo.value.path == fake_xls
        assert excinfo.value.reason


class TestReadOnlyGuarantee:
    def test_source_file_is_not_modified(self, simple_workbook: Path):
        before = simple_workbook.read_bytes()
        with open_workbook(simple_workbook) as wb:
            list(wb.formulas())
            list(wb.sheets)
            _ = wb.defined_names
            for sheet in wb.sheets:
                list(sheet.error_values())
        assert simple_workbook.read_bytes() == before

    def test_wrapper_exposes_no_save(self, simple_workbook: Path):
        with open_workbook(simple_workbook) as wb:
            assert not hasattr(wb, "save")
            assert all(not hasattr(s, "save") for s in wb.sheets)
