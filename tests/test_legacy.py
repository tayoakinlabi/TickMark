"""The ``.xls`` backend.

The tests that matter most here are not the ones proving a formula can be read.
They are:

* **Parity** — identical content in ``.xls`` and ``.xlsx`` must produce identical
  findings. A second backend that quietly disagrees with the first is worse than
  no second backend, because two numbers and no way to tell which is right is a
  worse position than one number.
* **Shared formulas** — a filled-down column is stored once in BIFF and stubbed
  everywhere else. Expanded wrongly, every cell in the run looks identical and
  check 1 goes blind exactly where it earns its keep.

Most fixtures are written at test time with ``xlwt`` so they stay readable in the
diff, matching the rest of the suite. The two that Excel alone can produce are
committed; see ``tests/fixtures/README.md``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tickmark.checks.registry import run_audit
from tickmark.workbook.biff import normalize_formula
from tickmark.workbook.loader import UnsupportedFormatError, open_workbook

xlwt = pytest.importorskip("xlwt")

FIXTURES = Path(__file__).parent / "fixtures"
EXCEL_XLS = FIXTURES / "excel_authored.xls"
EXCEL_XLSX = FIXTURES / "excel_authored.xlsx"
LINK_XLS = FIXTURES / "external_link.xls"
LINK_XLSX = FIXTURES / "external_link.xlsx"
LINK_TARGET = FIXTURES / "source_book.xls"

needs_excel_fixture = pytest.mark.skipif(
    not EXCEL_XLS.exists() or not EXCEL_XLSX.exists(),
    reason="Excel-authored fixture pair is not present",
)

needs_link_fixture = pytest.mark.skipif(
    not LINK_XLS.exists() or not LINK_XLSX.exists() or not LINK_TARGET.exists(),
    reason="external-link fixture set is not present",
)


def _copy(tmp_path, *names: str):
    """Put fixtures in a scratch directory, since link resolution is relative."""
    for name in names:
        shutil.copy(FIXTURES / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def legacy_workbook(tmp_path: Path) -> Path:
    """A small ``.xls`` written at test time, so its contents are in the diff."""
    book = xlwt.Workbook()
    sheet = book.add_sheet("Ledger")
    sheet.write(0, 0, "Item")
    sheet.write(0, 1, "Net")
    sheet.write(0, 2, "VAT")
    for row in range(1, 11):
        sheet.write(row, 0, f"item-{row}")
        sheet.write(row, 1, row * 10)
        sheet.write(row, 2, xlwt.Formula(f"B{row + 1}*1.075"))
    sheet.write(12, 1, xlwt.Formula("SUM(B2:B11)"))
    sheet.write(12, 4, xlwt.Formula("TODAY()"))

    hidden = book.add_sheet("Rates")
    hidden.write(0, 0, 0.075)
    hidden.visibility = 1

    path = tmp_path / "ledger.xls"
    book.save(str(path))
    return path


class TestNormalisation:
    """Each of these is a correctness fix, not a cosmetic one — see biff.py."""

    def test_float_literals_lose_their_trailing_zero(self):
        # Otherwise check 2's ignore list, which is keyed on value, stops
        # matching: 2.0 is not 2.
        assert normalize_formula("1.0/0.0") == "1/0"
        assert normalize_formula("ROUND(B2*1.2,2.0)") == "ROUND(B2*1.2,2)"
        assert normalize_formula("IF(B2>100.0,1.0,0.0)") == "IF(B2>100,1,0)"

    def test_genuine_decimals_survive(self):
        assert normalize_formula("B2*1.075") == "B2*1.075"
        assert normalize_formula("B2*0.5") == "B2*0.5"

    def test_deleted_reference_becomes_the_error_excel_shows(self):
        # Check 3 matches on '#REF!'; '(?)' would be invisible to it.
        assert normalize_formula("(?)+A1") == "#REF!+A1"

    def test_degenerate_single_cell_range_collapses(self):
        assert normalize_formula("Invoices!C2:C2+B1") == "Invoices!C2+B1"
        assert normalize_formula("SUM(A1:A5)") == "SUM(A1:A5)"

    def test_string_literals_are_left_alone(self):
        # The normalisation must not reach inside quoted text.
        assert normalize_formula('CONCATENATE("2.0 each",A1)') == 'CONCATENATE("2.0 each",A1)'


class TestLegacyReading:
    def test_formulas_are_recovered_as_text(self, legacy_workbook: Path):
        with open_workbook(legacy_workbook) as wb:
            formulas = {f"{c.sheet}!{c.coordinate}": c.formula for c in wb.formulas()}
        assert formulas["Ledger!C2"] == "=B2*1.075"
        assert formulas["Ledger!B13"] == "=SUM(B2:B11)"

    def test_literal_cells_still_arrive_as_values(self, legacy_workbook: Path):
        with open_workbook(legacy_workbook) as wb:
            sheet = wb.sheet("Ledger")
            values = {c.coordinate: c.value for c in sheet.cells() if not c.is_formula}
        assert values["A1"] == "Item"
        assert values["B2"] == 10

    def test_hidden_sheets_are_seen(self, legacy_workbook: Path):
        with open_workbook(legacy_workbook) as wb:
            assert wb.sheet("Rates").is_hidden is True
            assert wb.sheet("Ledger").is_hidden is False

    def test_checks_run_over_a_legacy_workbook(self, legacy_workbook: Path):
        with open_workbook(legacy_workbook) as wb:
            result = run_audit(wb)
        assert any(f.check == "volatile-function" for f in result.findings)

    def test_an_xls_extension_on_something_else_is_refused(self, tmp_path: Path):
        path = tmp_path / "actually_html.xls"
        path.write_text("<html><table><tr><td>1</td></tr></table></html>", encoding="utf-8")
        with pytest.raises(UnsupportedFormatError) as excinfo:
            open_workbook(path)
        assert "not a real .xls" in str(excinfo.value)

    def test_the_source_file_is_never_written_to(self, legacy_workbook: Path):
        before = legacy_workbook.read_bytes()
        with open_workbook(legacy_workbook) as wb:
            run_audit(wb)
        assert legacy_workbook.read_bytes() == before


@needs_excel_fixture
class TestExcelAuthored:
    """Against a file Excel itself wrote — shared formulas, cached values and all."""

    def test_shared_formulas_expand_per_row(self):
        with open_workbook(EXCEL_XLS) as wb:
            formulas = {c.coordinate: c.formula for c in wb.sheet("Invoices").formulas()}
        # One SHRFMLA record in the file; every cell of the run must come back
        # with its own row number, not the anchor's.
        assert formulas["C2"] == "=B2*1.075"
        assert formulas["C10"] == "=B10*1.075"
        assert formulas["C21"] == "=B21*1.075"

    def test_the_overwritten_cell_has_no_formula_at_all(self):
        # C11 is where someone typed a constant over the filled formula. It must
        # not be reconstructed from the shared record — its absence is the bug.
        with open_workbook(EXCEL_XLS) as wb:
            sheet = wb.sheet("Invoices")
            formulas = {c.coordinate: c.formula for c in sheet.formulas()}
            values = {c.coordinate: c.value for c in sheet.cells() if not c.is_formula}
        assert "C11" not in formulas
        assert values["C11"] == 999

    def test_check_one_finds_the_constant_in_the_run(self):
        with open_workbook(EXCEL_XLS) as wb:
            findings = run_audit(wb).findings
        assert any(
            f.check == "inconsistent-range" and f.sheet == "Invoices" and f.row == 11
            for f in findings
        )

    def test_the_text_headers_are_not_reported_as_overwritten_formulas(self):
        # This fixture is what exposed the header_rows bug: it carries =TODAY()
        # in row 1 beside three text headings, which used to disqualify the whole
        # header band and report every heading at HIGH. Anchored here as well as
        # in test_noise_control because an Excel-authored file is what caught it.
        with open_workbook(EXCEL_XLS) as wb:
            findings = run_audit(wb).findings
        assert [
            f.location for f in findings if f.check == "inconsistent-range" and f.row == 1
        ] == []

    def test_broken_reference_survives_decompilation(self):
        with open_workbook(EXCEL_XLS) as wb:
            formulas = {c.coordinate: c.formula for c in wb.sheet("Broken").formulas()}
        assert "#REF!" in formulas["B1"]

    def test_numeric_literals_match_what_excel_shows(self):
        with open_workbook(EXCEL_XLS) as wb:
            formulas = {c.coordinate: c.formula for c in wb.sheet("Invoices").formulas()}
        assert formulas["E2"] == "=IF(B2>100,IF(B3>50,ROUND(B2*1.2,2),B3),0)"

    def test_cross_sheet_reference_is_not_a_degenerate_range(self):
        with open_workbook(EXCEL_XLS) as wb:
            formulas = {c.coordinate: c.formula for c in wb.sheet("Hidden Rates").formulas()}
        assert formulas["B2"] == "=Invoices!C2+B1"

    def test_the_evaluator_verifies_most_of_a_legacy_workbook(self):
        with open_workbook(EXCEL_XLS) as wb:
            coverage = run_audit(wb).coverage
        assert coverage.total > 0
        assert coverage.verified >= coverage.total - 4

    def test_parity_between_the_two_formats(self):
        """The same workbook, both formats, must audit identically.

        This is the guarantee that justifies reading ``.xls`` at all. Findings
        are compared on check, sheet, address and severity — everything a reader
        would act on.
        """

        def fingerprint(path: Path):
            with open_workbook(path) as wb:
                findings = run_audit(wb).findings
            return sorted(
                (f.check, f.sheet, f.row, f.column, f.severity.value, f.summary) for f in findings
            )

        assert fingerprint(EXCEL_XLS) == fingerprint(EXCEL_XLSX)


@needs_link_fixture
class TestExternalLinks:
    """Check 4 over a legacy file — the gap that shipped open in 0.1.0.

    ``xlrd`` renders a reference into another workbook as ``<<external>>``, which
    the reference parser does not recognise as external at all. So this was not
    "the target is unresolved"; the link was invisible. Both halves are tested:
    that the link is now *found*, and that the file it names is the right one.
    """

    def test_the_link_is_found_and_names_its_target(self, tmp_path: Path):
        _copy(tmp_path, "external_link.xls", "source_book.xls")
        with open_workbook(tmp_path / "external_link.xls") as wb:
            assert wb.external_links == {1: "source_book.xls"}
            formulas = {c.coordinate: c.formula for c in wb.sheet("Main").formulas()}
        assert formulas["B1"] == "=[1]Rates!A1*A1"

    def test_a_present_target_is_reported_as_a_note(self, tmp_path: Path):
        _copy(tmp_path, "external_link.xls", "source_book.xls")
        with open_workbook(tmp_path / "external_link.xls") as wb:
            found = [f for f in run_audit(wb).findings if f.check == "external-link"]
        assert found and all(f.severity.value == "info" for f in found)

    def test_a_missing_target_is_high(self, tmp_path: Path):
        # The linked workbook is deliberately not copied across.
        _copy(tmp_path, "external_link.xls")
        with open_workbook(tmp_path / "external_link.xls") as wb:
            found = [f for f in run_audit(wb).findings if f.check == "external-link"]
        assert found and all(f.severity.value == "high" for f in found)

    def _links(self, path: Path):
        with open_workbook(path) as wb:
            return [f for f in run_audit(wb).findings if f.check == "external-link"]

    def test_both_formats_find_the_link_in_the_same_places(self, tmp_path: Path):
        """The parity that the two backends are actually responsible for.

        Severity is deliberately *not* compared here, and the first version of
        this test compared it and was wrong to. Excel stores the target
        differently in the two formats — a relative path in the .xls, an
        absolute file:// URL in the .xlsx — so whether it resolves depends on
        where the file has been since it was written, not on which backend read
        it. Asserting severity parity asserted something untrue, and it passed
        locally only because the committed .xlsx names a directory that happens
        to exist on the machine that generated it. CI is where it showed.

        What must match is that both backends see the same references in the
        same cells. That is the thing that was genuinely broken before: the .xls
        path found none at all.
        """
        _copy(tmp_path, "external_link.xls", "external_link.xlsx", "source_book.xls")

        def places(name: str):
            return sorted((f.check, f.sheet, f.row, f.column) for f in self._links(tmp_path / name))

        assert places("external_link.xls") == places("external_link.xlsx")
        assert len(places("external_link.xls")) == 2

    def test_both_formats_name_the_same_target_file(self, tmp_path: Path):
        # The stored spelling differs; the file being pointed at does not.
        _copy(tmp_path, "external_link.xls", "external_link.xlsx", "source_book.xls")
        for name in ("external_link.xls", "external_link.xlsx"):
            with open_workbook(tmp_path / name) as wb:
                targets = list(wb.external_links.values())
            assert targets, f"{name} found no external link at all"
            assert all(t.lower().endswith("source_book.xls") for t in targets), targets

    def test_the_formats_store_the_path_differently_on_purpose(self, tmp_path: Path):
        """Pins the asymmetry, so it cannot surprise anyone a second time.

        Users meet this: the same workbook saved both ways and then moved
        reports the link as fine from the .xls and as missing from the .xlsx.
        Tickmark reports what is stored, and what is stored differs.
        """
        _copy(tmp_path, "external_link.xls", "external_link.xlsx", "source_book.xls")

        with open_workbook(tmp_path / "external_link.xls") as wb:
            legacy = next(iter(wb.external_links.values()))
        with open_workbook(tmp_path / "external_link.xlsx") as wb:
            modern = next(iter(wb.external_links.values()))

        assert not Path(legacy).is_absolute(), "the .xls used to store a relative path"
        assert modern.startswith("file:///"), "the .xlsx used to store an absolute URL"
