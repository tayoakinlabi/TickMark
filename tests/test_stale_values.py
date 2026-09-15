"""Check 35, end to end, and the coverage figure that has to travel with it.

Building the input needs a trick. openpyxl writes either a formula or a value,
never a formula with a *deliberately wrong* cached result beside it — which is
exactly the condition this check exists to find. So the fixture writes an
ordinary workbook, then rewrites one cached value inside the ``.xlsx`` zip,
leaving the formula untouched. That is precisely what a stale workbook looks
like on disk: the formula says one thing, the number saved beside it says
another.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from tickmark.checks.registry import run_audit
from tickmark.checks.stale_values import CHECK_NAME, Coverage
from tickmark.report.html_report import render_report
from tickmark.workbook.inventory import take_inventory
from tickmark.workbook.loader import open_workbook


def _workbook_with_cached_values(path: Path, replacements: dict[str, str]) -> Path:
    """Write a workbook, then edit chosen cached values inside the package.

    ``replacements`` maps an exact XML fragment to its replacement, so each test
    states plainly which stored number it is falsifying.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    for row in range(1, 6):
        ws.cell(row, 1, row * 10)
        ws.cell(row, 2, f"=A{row}*2")
    ws["A7"] = "=SUM(A1:A5)"
    wb.save(path)

    # openpyxl writes no cached values at all, so inject them first.
    source = path.read_bytes()
    edited = path.with_name("edited.xlsx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(edited, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("sheet1.xml"):
                text = data.decode("utf-8")
                for old, new in replacements.items():
                    assert old in text, f"fixture fragment not found: {old}"
                    text = text.replace(old, new)
                data = text.encode("utf-8")
            zout.writestr(item, data)
    assert path.read_bytes() == source  # the original is untouched
    return edited


def _correct_row_values() -> dict[str, str]:
    """Cached values for the B column that agree with =A{n}*2 throughout."""
    return {f"<f>A{row}*2</f>": f"<f>A{row}*2</f><v>{row * 20}</v>" for row in range(1, 6)}


@pytest.fixture
def honest_workbook(tmp_path: Path) -> Path:
    """Every cached value agrees with its formula."""
    return _workbook_with_cached_values(
        tmp_path / "honest.xlsx",
        _correct_row_values() | {"<f>SUM(A1:A5)</f>": "<f>SUM(A1:A5)</f><v>150</v>"},
    )


@pytest.fixture
def stale_workbook(tmp_path: Path) -> Path:
    """The total was never recalculated after the inputs changed."""
    return _workbook_with_cached_values(
        tmp_path / "stale.xlsx",
        _correct_row_values() | {"<f>SUM(A1:A5)</f>": "<f>SUM(A1:A5)</f><v>60</v>"},
    )


class TestFindings:
    def test_a_stale_total_is_reported(self, stale_workbook: Path):
        with open_workbook(stale_workbook) as wb:
            findings = run_audit(wb).findings
        stale = [f for f in findings if f.check == CHECK_NAME]
        assert len(stale) == 1
        assert stale[0].sheet == "Ledger"
        assert stale[0].row == 7
        assert "150" in stale[0].summary and "60" in stale[0].summary

    def test_a_correct_workbook_produces_none(self, honest_workbook: Path):
        # The property that matters most: no false alarms on correct work.
        with open_workbook(honest_workbook) as wb:
            findings = run_audit(wb).findings
        assert [f for f in findings if f.check == CHECK_NAME] == []

    def test_the_finding_names_both_numbers(self, stale_workbook: Path):
        with open_workbook(stale_workbook) as wb:
            finding = next(f for f in run_audit(wb).findings if f.check == CHECK_NAME)
        # A reader has to be able to act without opening the workbook.
        assert finding.formula == "=SUM(A1:A5)"
        assert finding.context["cached"] == "60"

    def test_the_check_can_be_switched_off(self, stale_workbook: Path):
        from tickmark.config.rules import Rules

        with open_workbook(stale_workbook) as wb:
            result = run_audit(wb, rules=Rules(evaluate_formulas=False))
        assert [f for f in result.findings if f.check == CHECK_NAME] == []
        assert result.coverage == Coverage()


class TestCoverage:
    def test_coverage_counts_what_was_actually_compared(self, honest_workbook: Path):
        with open_workbook(honest_workbook) as wb:
            coverage = run_audit(wb).coverage
        assert coverage.total == 6
        assert coverage.verified == 6
        assert coverage.compared == 6
        assert coverage.disagreed == 0

    def test_unverifiable_formulas_are_counted_not_hidden(self, tmp_path: Path):
        wb = Workbook()
        ws = wb.active
        ws["A1"] = 2
        ws["B1"] = "=A1*2"
        ws["C1"] = "=TODAY()"
        ws["D1"] = '=IF(A1>1,"big","small")'
        path = tmp_path / "mixed.xlsx"
        wb.save(path)

        with open_workbook(path) as book:
            coverage = run_audit(book).coverage
        assert coverage.total == 3
        assert coverage.verified == 1  # only =A1*2 is inside the subset

    def test_the_report_publishes_coverage(self, honest_workbook: Path):
        with open_workbook(honest_workbook) as wb:
            inventory = take_inventory(wb)
            result = run_audit(wb)
            html = render_report(inventory, result.findings, coverage=result.coverage)
        assert "How much was arithmetically verified" in html
        assert "6 of 6 formulas" in html

    def test_the_report_names_the_unverified_count(self, tmp_path: Path):
        wb = Workbook()
        ws = wb.active
        ws["A1"] = 2
        ws["B1"] = "=VLOOKUP(A1,C:D,2,0)"
        path = tmp_path / "lookup.xlsx"
        wb.save(path)

        with open_workbook(path) as book:
            inventory = take_inventory(book)
            result = run_audit(book)
            html = render_report(inventory, result.findings, coverage=result.coverage)
        # Silence about the unverified share is the failure mode section 10.1
        # describes; the report must say so in words.
        assert "could not be verified" in html
        assert "not a formula that passed" in html
