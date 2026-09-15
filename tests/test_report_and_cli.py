"""The deliverable: the HTML report and the CLI that produces it."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from tickmark.checks.registry import run_checks
from tickmark.cli.main import main
from tickmark.findings.model import Finding, Severity
from tickmark.report.html_report import render_report, render_summary_index
from tickmark.workbook import open_workbook
from tickmark.workbook.inventory import take_inventory


def workbook_with_problems(tmp_path: Path, name: str = "books.xlsx") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(["Client", "Net", "VAT", "Gross"])
    for row in range(2, 12):
        ws[f"A{row}"] = f"Client {row - 1}"
        ws[f"B{row}"] = 1000 + row
        ws[f"C{row}"] = f"=B{row}*0.2"
        ws[f"D{row}"] = f"=B{row}+C{row}"
    ws["C7"] = 148.0
    ws["D8"] = "=B8+C7"
    path = tmp_path / name
    wb.save(path)
    return path


def audit(path: Path):
    with open_workbook(path) as wb:
        return take_inventory(wb), run_checks(wb)


class TestHtmlReport:
    def test_is_a_complete_document(self, tmp_path: Path):
        html = render_report(*audit(workbook_with_problems(tmp_path)))
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")

    def test_is_self_contained(self, tmp_path: Path):
        # It gets emailed. Nothing may be fetched when it is opened.
        html = render_report(*audit(workbook_with_problems(tmp_path)))
        for marker in ("http://", "https://", "<script", "src="):
            assert marker not in html

    def test_formulas_are_escaped(self, tmp_path: Path):
        # The rate makes check 2 fire, which is what carries the formula text
        # into the report — where its <, > and & must not become markup.
        wb = Workbook()
        ws = wb.active
        for row in range(1, 9):
            ws[f"B{row}"] = f'=IF(A{row}<1,"<b>x</b>",A{row}&"&")*1.075'
        path = tmp_path / "escape.xlsx"
        wb.save(path)

        _, findings = audit(path)
        assert any(f.formula and "<b>" in f.formula for f in findings), (
            "fixture must produce a finding carrying the formula"
        )

        html = render_report(*audit(path))
        # The workbook's contents are untrusted input to this document.
        assert "<b>x</b>" not in html
        assert "&lt;b&gt;x&lt;/b&gt;" in html
        assert "&amp;" in html

    def test_states_what_it_does_not_check(self, tmp_path: Path):
        # A clean report must not be mistaken for proof the workbook is correct.
        html = render_report(*audit(workbook_with_problems(tmp_path)))
        assert "does not cover" in html
        # Tier B means the honest claim is now "most arithmetic is unchecked"
        # rather than "none of it is calculated". The report has to say which,
        # because a reader who assumes the stronger limit will discount findings
        # the evaluator genuinely made.
        assert "Most arithmetic" in html
        assert "treat the rest as unchecked" in html

    def test_clean_workbook_says_so_without_overclaiming(self, tmp_path: Path):
        wb = Workbook()
        ws = wb.active
        ws["A1"] = 1
        path = tmp_path / "clean.xlsx"
        wb.save(path)

        html = render_report(*audit(path))
        assert "No findings" in html
        assert "clean bill of health" in html

    def test_inventory_is_present(self, tmp_path: Path):
        html = render_report(*audit(workbook_with_problems(tmp_path)))
        assert "What is in this workbook" in html
        assert "Invoices" in html

    def test_macro_presence_is_called_out(self, tmp_path: Path):
        inventory, findings = audit(workbook_with_problems(tmp_path))
        from dataclasses import replace

        html = render_report(replace(inventory, has_macros=True), findings)
        assert "contains macros" in html

    def test_summary_index_links_each_report(self):
        counts = {Severity.HIGH: 1, Severity.MEDIUM: 0, Severity.LOW: 2, Severity.INFO: 0}
        html = render_summary_index([("a.xlsx", "a.tickmark.html", counts)])
        assert 'href="a.tickmark.html"' in html
        assert "a.xlsx" in html

    def test_severity_headings_appear_only_when_used(self, tmp_path: Path):
        inventory, _ = audit(workbook_with_problems(tmp_path))
        only_high = [
            Finding(
                check="x",
                severity=Severity.HIGH,
                sheet="S",
                row=1,
                column=1,
                summary="bad",
            )
        ]
        html = render_report(inventory, only_high)
        assert "Needs attention" in html
        assert "Worth checking</h2>" not in html


class TestCli:
    def test_audits_one_file_and_writes_a_report(self, tmp_path: Path, capsys):
        path = workbook_with_problems(tmp_path)
        code = main([str(path)])
        captured = capsys.readouterr()

        assert code == 1  # findings at or above 'high'
        report = tmp_path / "books.tickmark.html"
        assert report.exists()
        assert "Tickmark" in report.read_text(encoding="utf-8")
        assert "books.xlsx" in captured.out

    def test_clean_workbook_exits_zero(self, tmp_path: Path):
        wb = Workbook()
        wb.active["A1"] = 1
        path = tmp_path / "clean.xlsx"
        wb.save(path)
        assert main([str(path), "--no-report"]) == 0

    def test_fail_on_never_always_exits_zero(self, tmp_path: Path):
        path = workbook_with_problems(tmp_path)
        assert main([str(path), "--no-report", "--fail-on", "never"]) == 0

    def test_missing_target_exits_two(self, tmp_path: Path, capsys):
        assert main([str(tmp_path / "nope.xlsx")]) == 2
        assert "nothing to audit" in capsys.readouterr().err

    def test_folder_audit_writes_a_summary(self, tmp_path: Path):
        workbook_with_problems(tmp_path, "one.xlsx")
        workbook_with_problems(tmp_path, "two.xlsx")
        main([str(tmp_path)])

        assert (tmp_path / "one.tickmark.html").exists()
        assert (tmp_path / "two.tickmark.html").exists()
        assert (tmp_path / "tickmark-summary.html").exists()

    def test_unreadable_file_is_reported_and_skipped(self, tmp_path: Path, capsys):
        workbook_with_problems(tmp_path, "good.xlsx")
        (tmp_path / "broken.xlsx").write_bytes(b"not a workbook")

        code = main([str(tmp_path), "--no-report"])
        captured = capsys.readouterr()

        # One bad file must not cost the other its audit.
        assert "could not read" in captured.err
        assert "good.xlsx" in captured.out
        assert code == 1

    def test_no_report_writes_nothing(self, tmp_path: Path):
        path = workbook_with_problems(tmp_path)
        main([str(path), "--no-report"])
        assert not (tmp_path / "books.tickmark.html").exists()

    def test_explicit_output_path_is_honoured(self, tmp_path: Path):
        path = workbook_with_problems(tmp_path)
        destination = tmp_path / "out" / "audit.html"
        main([str(path), "-o", str(destination)])
        assert destination.exists()

    def test_excel_lock_files_are_ignored(self, tmp_path: Path):
        workbook_with_problems(tmp_path, "real.xlsx")
        (tmp_path / "~$real.xlsx").write_bytes(b"lock")
        code = main([str(tmp_path), "--no-report"])
        assert code == 1  # the lock file was skipped, not treated as unreadable

    def test_source_workbook_is_never_modified(self, tmp_path: Path):
        path = workbook_with_problems(tmp_path)
        before = path.read_bytes()
        main([str(path)])
        assert path.read_bytes() == before
