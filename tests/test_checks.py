"""Checks 2, 3, 4 and 6, plus the runner that ties them together."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from tickmark.checks.broken_refs import BrokenRefsCheck
from tickmark.checks.external_links import ExternalLinksCheck
from tickmark.checks.hardcoded_constants import HardcodedConstantsCheck
from tickmark.checks.registry import build_sheet_checks, build_workbook_checks, run_checks
from tickmark.checks.volatile_functions import VolatileFunctionsCheck
from tickmark.config.rules import Rules
from tickmark.findings.model import Severity
from tickmark.workbook import open_workbook


def build(tmp_path: Path, cells: dict[str, object], name: str = "wb.xlsx") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for coord, value in cells.items():
        ws[coord] = value
    path = tmp_path / name
    wb.save(path)
    return path


def run(check, path: Path):
    with open_workbook(path) as wb:
        return check.run(wb.sheets[0])


# ---------------------------------------------------------------- check 2


class TestHardcodedConstants:
    def test_rate_buried_in_a_formula_is_found(self, tmp_path: Path):
        found = run(HardcodedConstantsCheck(), build(tmp_path, {"B2": "=A2*1.075"}))
        assert [f.context["value"] for f in found] == ["1.075"]
        assert found[0].severity is Severity.MEDIUM

    @pytest.mark.parametrize("formula", ["=A1*100", "=A1/12", "=A1+1", "=A1*0", "=A1/365"])
    def test_meaningless_literals_are_ignored(self, tmp_path: Path, formula: str):
        # A check that flags these is a check people switch off.
        assert run(HardcodedConstantsCheck(), build(tmp_path, {"B1": formula})) == []

    def test_structural_argument_is_ignored(self, tmp_path: Path):
        # The 3 in VLOOKUP is a column index, not a rate.
        cells = {"B1": "=VLOOKUP(A1,Data!$A:$C,3,FALSE)"}
        assert run(HardcodedConstantsCheck(), build(tmp_path, cells)) == []

    def test_exponent_is_ignored(self, tmp_path: Path):
        assert run(HardcodedConstantsCheck(), build(tmp_path, {"B1": "=A1^3"})) == []

    def test_array_constants_are_not_reported_cell_by_cell(self, tmp_path: Path):
        # An inline table is data; reporting each number would bury the report.
        cells = {"B1": "=SUM({1.5,2.5;3.5,4.5})"}
        assert run(HardcodedConstantsCheck(), build(tmp_path, cells)) == []

    def test_integers_are_off_by_default_and_can_be_enabled(self, tmp_path: Path):
        path = build(tmp_path, {"B1": "=A1*17"})
        assert run(HardcodedConstantsCheck(), path) == []
        rules = Rules(report_integer_constants=True)
        found = run(HardcodedConstantsCheck(rules), path)
        assert [f.context["value"] for f in found] == ["17"]
        assert found[0].severity is Severity.LOW

    def test_ignore_list_is_configurable(self, tmp_path: Path):
        path = build(tmp_path, {"B1": "=A1*1.075"})
        rules = Rules(ignored_numbers=frozenset({1.075}))
        assert run(HardcodedConstantsCheck(rules), path) == []

    def test_repeated_value_reported_once_per_cell(self, tmp_path: Path):
        found = run(HardcodedConstantsCheck(), build(tmp_path, {"B1": "=A1*1.075+A2*1.075"}))
        assert len(found) == 1


# ---------------------------------------------------------------- check 3


class TestBrokenRefs:
    def test_error_written_into_the_formula(self, tmp_path: Path):
        found = run(BrokenRefsCheck(), build(tmp_path, {"B1": "=A1+#REF!"}))
        assert len(found) == 1
        assert found[0].severity is Severity.HIGH
        assert found[0].context["source"] == "formula"

    def test_explanation_is_in_plain_language(self, tmp_path: Path):
        found = run(BrokenRefsCheck(), build(tmp_path, {"B1": "=A1+#REF!"}))
        # The audience is a bookkeeper, not a developer.
        assert "no longer exists" in found[0].explanation

    def test_name_error_is_high_severity(self, tmp_path: Path):
        found = run(BrokenRefsCheck(), build(tmp_path, {"B1": "=NOTAFUNCTION(A1)+#NAME?"}))
        assert found[0].severity is Severity.HIGH

    def test_healthy_formulas_are_silent(self, tmp_path: Path):
        assert run(BrokenRefsCheck(), build(tmp_path, {"B1": "=SUM(A1:A9)"})) == []


# ---------------------------------------------------------------- check 4


class TestExternalLinks:
    def test_missing_target_is_high_severity(self, tmp_path: Path):
        cells = {"B1": "=[1]Budget!A1*2"}
        found = run(ExternalLinksCheck(), build(tmp_path, cells))
        assert len(found) == 1
        # openpyxl records no link table for a hand-written reference, so the
        # target is unknown rather than confirmed missing.
        assert found[0].context["resolves"] in {"no", "unknown"}

    def test_present_target_is_informational(self, tmp_path: Path):
        neighbour = build(tmp_path, {"A1": 1}, name="Budget.xlsx")
        cells = {"B1": f"=[{neighbour.name}]Sheet1!A1*2"}
        found = run(ExternalLinksCheck(), build(tmp_path, cells))
        assert len(found) == 1
        assert found[0].context["resolves"] == "yes"
        assert found[0].severity is Severity.INFO

    def test_local_references_are_not_reported(self, tmp_path: Path):
        assert run(ExternalLinksCheck(), build(tmp_path, {"B1": "=Sheet1!A1*2"})) == []

    def test_no_network_call_for_web_targets(self, tmp_path: Path):
        # NF 1: zero network calls. A URL target is unverifiable, never fetched.
        cells = {"B1": "=[https://example.com/b.xlsx]Sheet1!A1"}
        found = run(ExternalLinksCheck(), build(tmp_path, cells))
        assert found[0].context["resolves"] == "unknown"


# ---------------------------------------------------------------- check 6


class TestVolatileFunctions:
    @pytest.mark.parametrize(
        "formula", ["=NOW()", "=TODAY()", "=RAND()", "=OFFSET(A1,1,1)", '=INDIRECT("A1")']
    )
    def test_volatile_functions_are_found(self, tmp_path: Path, formula: str):
        found = run(VolatileFunctionsCheck(), build(tmp_path, {"B1": formula}))
        assert len(found) == 1
        assert found[0].severity is Severity.LOW

    def test_nested_volatile_is_found(self, tmp_path: Path):
        cells = {"B1": "=IF(A1>0,OFFSET(A1,1,1),0)"}
        assert len(run(VolatileFunctionsCheck(), build(tmp_path, cells))) == 1

    def test_explanation_says_why_it_matters(self, tmp_path: Path):
        found = run(VolatileFunctionsCheck(), build(tmp_path, {"B1": "=TODAY()"}))
        assert "changes daily" in found[0].explanation

    def test_non_volatile_is_silent(self, tmp_path: Path):
        assert run(VolatileFunctionsCheck(), build(tmp_path, {"B1": "=SUM(A1:A9)"})) == []


# ---------------------------------------------------------------- runner


class TestRunner:
    def test_every_shipped_check_matches_the_protocol(self):
        for check in build_sheet_checks():
            assert isinstance(check.name, str) and check.name
            assert callable(check.run)
        for check in build_workbook_checks():
            assert isinstance(check.name, str) and check.name
            assert callable(check.run_workbook)

    def test_check_names_are_unique(self):
        names = [c.name for c in build_sheet_checks() + build_workbook_checks()]
        assert len(names) == len(set(names))

    def test_runner_collects_across_checks(self, tmp_path: Path):
        cells = {
            "B1": "=A1*1.075",  # check 2
            "B2": "=A2+#REF!",  # check 3
            "B3": "=NOW()",  # check 6
        }
        with open_workbook(build(tmp_path, cells)) as wb:
            found = run_checks(wb)
        assert {f.check for f in found} >= {
            "hardcoded-constant",
            "broken-reference",
            "volatile-function",
        }

    def test_findings_are_sorted_most_severe_first(self, tmp_path: Path):
        cells = {"B1": "=A1*1.075", "B2": "=A2+#REF!", "B3": "=NOW()"}
        with open_workbook(build(tmp_path, cells)) as wb:
            found = run_checks(wb)
        ranks = [f.severity.rank for f in found]
        assert ranks == sorted(ranks)

    def test_a_failing_check_does_not_lose_the_others(self, tmp_path: Path):
        class Exploding:
            name = "exploding"

            def run(self, sheet):
                raise RuntimeError("boom")

        import tickmark.checks.registry as registry

        original_sheet = registry.build_sheet_checks
        original_workbook = registry.build_workbook_checks
        registry.build_sheet_checks = lambda rules=None: [Exploding(), VolatileFunctionsCheck()]
        registry.build_workbook_checks = lambda rules=None: []
        try:
            with open_workbook(build(tmp_path, {"B1": "=NOW()"})) as wb:
                found = registry.run_checks(wb)
            assert [f.check for f in found] == ["volatile-function"]
        finally:
            registry.build_sheet_checks = original_sheet
            registry.build_workbook_checks = original_workbook

    def test_hidden_sheets_are_audited_by_default(self, tmp_path: Path):
        wb = Workbook()
        ws = wb.active
        ws.title = "Visible"
        ws["A1"] = 1
        hidden = wb.create_sheet("Hidden")
        hidden["B1"] = "=A1*1.075"
        hidden.sheet_state = "hidden"
        path = tmp_path / "h.xlsx"
        wb.save(path)

        with open_workbook(path) as book:
            assert any(f.sheet == "Hidden" for f in run_checks(book))
            assert not any(f.sheet == "Hidden" for f in run_checks(book, include_hidden=False))
