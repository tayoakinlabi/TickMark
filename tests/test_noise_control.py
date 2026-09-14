"""The two things that decide whether a report gets read.

Both of these were found by running the checks over a realistic workbook rather
than a fixture, and both would have made the product unusable in the field while
every unit test stayed green:

1. Header rows and input columns reported as broken formulas — one false positive
   per column, in every workbook ever written.
2. The same fact repeated once per row — a rate written down a 10,000-row sheet
   produced 10,000 identical findings.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from tickmark.checks.inconsistent_range import InconsistentRangeCheck
from tickmark.checks.registry import run_checks
from tickmark.findings.grouping import group_findings
from tickmark.findings.model import Finding, Severity
from tickmark.workbook import open_workbook


def invoice_sheet(tmp_path: Path, *, rows: int = 12, planted: dict[str, object] | None = None):
    """A plausible table: headers, a label column, an input column, formulas."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(["Client", "Net", "VAT", "Gross"])
    for row in range(2, 2 + rows):
        ws[f"A{row}"] = f"Client {row - 1}"
        ws[f"B{row}"] = 1000 + row
        ws[f"C{row}"] = f"=B{row}*0.2"
        ws[f"D{row}"] = f"=B{row}+C{row}"
    for coord, value in (planted or {}).items():
        ws[coord] = value
    path = tmp_path / "invoices.xlsx"
    wb.save(path)
    return path


class TestHeaderRowsAndInputColumns:
    def test_header_row_is_not_a_finding(self, tmp_path: Path):
        path = invoice_sheet(tmp_path)
        with open_workbook(path) as wb:
            found = InconsistentRangeCheck().run(wb.sheets[0])
        assert [f.coordinate for f in found if f.row == 1] == []

    def test_input_column_is_not_a_finding(self, tmp_path: Path):
        path = invoice_sheet(tmp_path)
        with open_workbook(path) as wb:
            found = InconsistentRangeCheck().run(wb.sheets[0])
        # Columns A (labels) and B (inputs) are data, not broken formulas.
        assert [f.coordinate for f in found if f.column in (1, 2)] == []

    def test_a_clean_table_produces_nothing(self, tmp_path: Path):
        path = invoice_sheet(tmp_path)
        with open_workbook(path) as wb:
            assert InconsistentRangeCheck().run(wb.sheets[0]) == []

    def test_the_real_bug_still_surfaces(self, tmp_path: Path):
        # C7 typed over. Its row is only 50% formulas, so a rule that vetoed on
        # "mostly constants" would have hidden it. Asymmetry is what saves it.
        path = invoice_sheet(tmp_path, planted={"C7": 148.0})
        with open_workbook(path) as wb:
            found = InconsistentRangeCheck().run(wb.sheets[0])
        assert [f.coordinate for f in found] == ["C7"]
        assert found[0].severity is Severity.HIGH

    def test_multi_row_headers_are_handled(self, tmp_path: Path):
        wb = Workbook()
        ws = wb.active
        ws.append(["Quarterly report", None, None])
        ws.append(["Client", "Net", "VAT"])
        for row in range(3, 12):
            ws[f"A{row}"] = f"C{row}"
            ws[f"B{row}"] = row
            ws[f"C{row}"] = f"=B{row}*0.2"
        path = tmp_path / "two_headers.xlsx"
        wb.save(path)

        with open_workbook(path) as book:
            found = InconsistentRangeCheck().run(book.sheets[0])
        assert [f.coordinate for f in found if f.row in (1, 2)] == []

    def test_an_isolated_formula_column_still_reports(self, tmp_path: Path):
        # No neighbouring data at all, so there is no perpendicular evidence
        # either way. The check must not fall silent just because the sheet is
        # narrow.
        wb = Workbook()
        ws = wb.active
        for row in range(1, 9):
            ws[f"B{row}"] = f"=A{row}*2"
        ws["B4"] = 4200
        path = tmp_path / "narrow.xlsx"
        wb.save(path)

        with open_workbook(path) as book:
            found = InconsistentRangeCheck().run(book.sheets[0])
        assert [f.coordinate for f in found] == ["B4"]


class TestGrouping:
    def _finding(self, row: int, column: int = 3, value: str = "0.2") -> Finding:
        return Finding(
            check="hardcoded-constant",
            severity=Severity.MEDIUM,
            sheet="Invoices",
            row=row,
            column=column,
            summary=f"Hardcoded value {value} inside a formula",
            context={"value": value},
        )

    def test_a_filled_down_run_collapses_to_one(self):
        grouped = group_findings([self._finding(r) for r in range(2, 14)])
        assert len(grouped) == 1
        assert grouped[0].coordinate == "C2:C13"
        assert grouped[0].span == 12
        assert grouped[0].context["cell_count"] == "12"

    def test_a_gap_splits_the_run(self):
        rows = [2, 3, 4, 9, 10, 11]
        grouped = group_findings([self._finding(r) for r in rows])
        assert sorted(f.coordinate for f in grouped) == ["C2:C4", "C9:C11"]

    def test_different_values_stay_separate(self):
        # Two different rates in one column are two facts, not one.
        findings = [self._finding(r, value="0.2") for r in range(2, 6)]
        findings += [self._finding(r, value="0.175") for r in range(6, 10)]
        grouped = group_findings(findings)
        assert len(grouped) == 2

    def test_a_single_finding_is_untouched(self):
        grouped = group_findings([self._finding(4)])
        assert grouped[0].coordinate == "C4"
        assert grouped[0].span == 1
        assert "cell_count" not in grouped[0].context

    def test_a_run_across_a_row_collapses(self):
        findings = [self._finding(row=2, column=c) for c in range(3, 9)]
        grouped = group_findings(findings)
        assert len(grouped) == 1
        assert grouped[0].coordinate == "C2:H2"

    def test_grouping_is_on_by_default_in_the_runner(self, tmp_path: Path):
        path = invoice_sheet(tmp_path, rows=12)
        with open_workbook(path) as wb:
            grouped = run_checks(wb)
            raw = run_checks(wb, group=False)

        rates = [f for f in grouped if f.check == "hardcoded-constant"]
        assert len(rates) == 1
        assert rates[0].span == 12
        assert len([f for f in raw if f.check == "hardcoded-constant"]) == 12
