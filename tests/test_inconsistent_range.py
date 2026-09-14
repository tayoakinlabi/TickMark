"""Check 1 — the finding the product exists to make."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from tickmark.checks.inconsistent_range import InconsistentRangeCheck
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


def findings_for(path: Path, **kwargs):
    check = InconsistentRangeCheck(**kwargs)
    with open_workbook(path) as wb:
        return check.run(wb.sheets[0])


class TestTheBugItHunts:
    def test_constant_overwriting_a_formula_is_found(self, tmp_path: Path):
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
        cells.update({f"A{r}": r for r in range(1, 9)})
        cells["B4"] = 4200  # typed over the formula
        path = build(tmp_path, cells)

        found = findings_for(path)
        assert [f.coordinate for f in found] == ["B4"]
        assert found[0].severity is Severity.HIGH
        assert "typed over" in found[0].explanation

    def test_differently_shaped_formula_is_found(self, tmp_path: Path):
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
        cells.update({f"A{r}": r for r in range(1, 9)})
        cells["B6"] = "=A6*3"  # different multiplier
        path = build(tmp_path, cells)

        found = findings_for(path)
        assert [f.coordinate for f in found] == ["B6"]
        assert found[0].severity is Severity.MEDIUM

    def test_broken_fill_down_is_found(self, tmp_path: Path):
        # B7 points at A1 instead of A7 — invisible in A1 notation, obvious in R1C1.
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
        cells.update({f"A{r}": r for r in range(1, 9)})
        cells["B7"] = "=A1*2"
        path = build(tmp_path, cells)

        assert [f.coordinate for f in findings_for(path)] == ["B7"]

    def test_reports_the_expected_shape(self, tmp_path: Path):
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
        cells["B4"] = 4200
        path = build(tmp_path, cells)

        finding = findings_for(path)[0]
        # Same row, one column left, times two — the R1C1 form every correctly
        # filled cell in the column shares.
        assert finding.context["expected_shape"] == "RC[-1]*2"


class TestFalsePositiveResistance:
    def test_a_correctly_filled_column_is_silent(self, tmp_path: Path):
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 21)}
        cells.update({f"A{r}": r for r in range(1, 21)})
        assert findings_for(build(tmp_path, cells)) == []

    def test_a_column_of_plain_data_is_silent(self, tmp_path: Path):
        cells = {f"A{r}": r * 3 for r in range(1, 21)}
        assert findings_for(build(tmp_path, cells)) == []

    def test_a_short_run_is_not_evidence(self, tmp_path: Path):
        # Two against one proves nothing.
        cells = {"B1": "=A1*2", "B2": "=A2*2", "B3": 99}
        assert findings_for(build(tmp_path, cells)) == []

    def test_a_genuine_mixture_is_silent(self, tmp_path: Path):
        # No majority to depart from, so every cell would otherwise be reported.
        cells = {
            "B1": "=A1*2",
            "B2": "=A2*3",
            "B3": "=A3*4",
            "B4": "=A4*5",
            "B5": "=A5*6",
        }
        assert findings_for(build(tmp_path, cells)) == []

    def test_a_blank_row_breaks_the_run(self, tmp_path: Path):
        # A total under a gap must not be compared against the rows above it.
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
        cells["B10"] = "=SUM(B1:B8)"
        assert findings_for(build(tmp_path, cells)) == []

    def test_a_total_row_directly_beneath_is_reported(self, tmp_path: Path):
        # With no gap there is nothing to distinguish a total from a mistake, so
        # this is a known and accepted false positive. Recorded so that a future
        # totals-detection change is a deliberate decision, not a surprise.
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
        cells["B9"] = "=SUM(B1:B8)"
        assert [f.coordinate for f in findings_for(build(tmp_path, cells))] == ["B9"]


class TestRowsAndColumns:
    def test_departure_in_a_row_is_found(self, tmp_path: Path):
        cells = {f"{c}2": f"={c}1*2" for c in "BCDEFGH"}
        cells["E2"] = 17
        path = build(tmp_path, cells)
        assert [f.coordinate for f in findings_for(path)] == ["E2"]

    def test_a_cell_is_reported_once_not_twice(self, tmp_path: Path):
        # The odd cell sits in both a column run and a row run.
        cells = {}
        for col in "BCDEF":
            for row in range(1, 7):
                cells[f"{col}{row}"] = f"=A{row}*2"
        cells["D4"] = 0
        found = findings_for(build(tmp_path, cells))
        assert [f.coordinate for f in found].count("D4") == 1


class TestTuning:
    def test_min_run_gates_the_finding(self, tmp_path: Path):
        # Three formulas and one constant: 75% agreement, so dominance is
        # satisfied and only run length decides.
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 4)}
        cells["B4"] = 99
        path = build(tmp_path, cells)

        assert [f.coordinate for f in findings_for(path, min_run=4)] == ["B4"]
        assert findings_for(path, min_run=5) == []

    def test_the_two_gates_are_independent(self, tmp_path: Path):
        # Two formulas against one constant clears a min_run of 3 but is only
        # 67% agreement, so dominance still suppresses it. Lowering that gate is
        # what lets it through. Pinned because the interaction is easy to lose:
        # loosening min_run alone looks like it should be enough, and is not.
        cells = {"B1": "=A1*2", "B2": "=A2*2", "B3": 99}
        path = build(tmp_path, cells)

        assert findings_for(path, min_run=3) == []
        assert [f.coordinate for f in findings_for(path, min_run=3, dominance=0.6)] == ["B3"]

    def test_unparseable_formula_does_not_crash_the_check(self, tmp_path: Path):
        cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
        cells["B5"] = '=IF(A5="unterminated'  # will not tokenize
        path = build(tmp_path, cells)

        # The bad cell is skipped; the surrounding run is still audited, and
        # nothing raises. One malformed cell must never cost a whole sheet.
        found = findings_for(path)
        assert all(f.coordinate != "B5" for f in found)


@pytest.mark.parametrize("dominance", [0.5, 0.75, 0.9])
def test_dominance_threshold_does_not_crash(tmp_path: Path, dominance: float):
    cells = {f"B{r}": f"=A{r}*2" for r in range(1, 9)}
    cells["B4"] = 1
    assert isinstance(findings_for(build(tmp_path, cells), dominance=dominance), list)
