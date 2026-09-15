"""Tier B evaluation, and — more importantly — where it refuses.

Two properties matter more than any individual computation here:

1. **Nothing outside the subset is ever silently evaluated.** Every refusal test
   asserts a reason, because a refusal that does not say why becomes invisible
   in the coverage figure.
2. **A correct workbook produces no findings.** Section 10.1 names a false
   discrepancy on correct work as the failure that destroys trust fastest, so
   the float-noise and rounding tests below are not edge-case pedantry — they
   are the ones guarding the product's credibility.
"""

from __future__ import annotations

import pytest

from tickmark.formula.evaluate import (
    EVALUABLE_FUNCTIONS,
    evaluate_formula,
    values_agree,
)


def _grid(cells: dict[str, object]):
    """A lookup over a fake sheet, addressed the way the evaluator asks."""

    def column_index(letters: str) -> int:
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - ord("A") + 1)
        return index

    table: dict[tuple[str, int, int], object] = {}
    for address, value in cells.items():
        letters = "".join(c for c in address if c.isalpha())
        row = int("".join(c for c in address if c.isdigit()))
        table[("Sheet1", row, column_index(letters))] = value

    def lookup(sheet: str, row: int, column: int) -> object:
        return table.get((sheet, row, column))

    return lookup


def _value(formula: str, cells: dict[str, object] | None = None) -> float:
    result = evaluate_formula(formula, sheet="Sheet1", lookup=_grid(cells or {}))
    assert result.verified, f"expected a value, got refusal: {result.reason}"
    assert result.value is not None
    return result.value


def _refusal(formula: str, cells: dict[str, object] | None = None) -> str:
    result = evaluate_formula(formula, sheet="Sheet1", lookup=_grid(cells or {}))
    assert not result.verified, f"expected a refusal, got {result.value}"
    assert result.reason
    return result.reason


class TestArithmetic:
    @pytest.mark.parametrize(
        ("formula", "expected"),
        [
            ("=1+2", 3.0),
            ("=10-4", 6.0),
            ("=6*7", 42.0),
            ("=9/2", 4.5),
            ("=2^10", 1024.0),
            ("=-5", -5.0),
            ("=+5", 5.0),
            ("=50%", 0.5),
            ("=(1+2)*3", 9.0),
        ],
    )
    def test_operators(self, formula: str, expected: float):
        assert _value(formula) == pytest.approx(expected)

    def test_excel_precedence_traps_are_honoured(self):
        # Both are pinned in the parser; evaluating them proves the tree the
        # evaluator walks is the same one check 7 measures.
        assert _value("=-2^2") == 4.0
        assert _value("=2^3^2") == 64.0

    def test_cell_references_resolve(self):
        assert _value("=A1*B1", {"A1": 6.0, "B1": 7.0}) == 42.0

    def test_division_by_zero_is_refused_not_crashed(self):
        assert "division by zero" in _refusal("=1/0")


class TestFunctions:
    def test_the_subset_is_exactly_what_section_10_2_sanctioned(self):
        assert {
            "SUM",
            "AVERAGE",
            "MIN",
            "MAX",
            "COUNT",
            "COUNTA",
            "ROUND",
            "ROUNDUP",
            "ROUNDDOWN",
            "ABS",
        } == EVALUABLE_FUNCTIONS

    def test_aggregates_over_a_range(self):
        cells = {"A1": 1.0, "A2": 2.0, "A3": 3.0}
        assert _value("=SUM(A1:A3)", cells) == 6.0
        assert _value("=AVERAGE(A1:A3)", cells) == 2.0
        assert _value("=MIN(A1:A3)", cells) == 1.0
        assert _value("=MAX(A1:A3)", cells) == 3.0
        assert _value("=COUNT(A1:A3)", cells) == 3.0

    def test_a_range_ignores_text_the_way_excel_does(self):
        # Documented Excel behaviour, not a coercion guess: SUM over a range
        # skips text. Refusing here would throw away most real totals.
        cells = {"A1": 1.0, "A2": "n/a", "A3": 3.0}
        assert _value("=SUM(A1:A3)", cells) == 4.0
        assert _value("=COUNT(A1:A3)", cells) == 2.0
        assert _value("=COUNTA(A1:A3)", cells) == 3.0

    def test_text_as_a_direct_argument_is_refused(self):
        # The scalar coercion rule differs from the range rule, so it is
        # declined rather than implemented. See the module docstring.
        assert _refusal('=SUM(1,"2")')

    def test_rounding_goes_away_from_zero_not_bankers(self):
        # Python's round(2.5) is 2; Excel's ROUND(2.5,0) is 3. Getting this
        # wrong would report a discrepancy on a correct workbook every time a
        # value landed on a half.
        assert _value("=ROUND(2.5,0)") == 3.0
        assert _value("=ROUND(3.5,0)") == 4.0
        assert _value("=ROUND(-2.5,0)") == -3.0
        assert _value("=ROUND(2.345,2)") == pytest.approx(2.35)

    def test_roundup_and_rounddown(self):
        assert _value("=ROUNDUP(2.01,0)") == 3.0
        assert _value("=ROUNDDOWN(2.99,0)") == 2.0
        assert _value("=ROUNDUP(-2.01,0)") == -3.0
        assert _value("=ABS(-7)") == 7.0

    def test_average_of_no_numbers_is_refused_rather_than_zero(self):
        assert "no numeric cells" in _refusal("=AVERAGE(A1:A3)", {"A1": "x"})

    def test_nested_supported_calls(self):
        assert _value("=ROUND(AVERAGE(A1:A2),1)", {"A1": 1.0, "A2": 2.0}) == 1.5


class TestRefusals:
    @pytest.mark.parametrize(
        ("formula", "fragment"),
        [
            ("=VLOOKUP(A1,B:C,2,0)", "outside the evaluated subset"),
            ("=IF(A1>1,2,3)", "outside the evaluated subset"),
            ("=TODAY()", "outside the evaluated subset"),
            ('="a"&"b"', "outside the subset"),
            ("=A1=B1", "outside the subset"),
            ('="text"', "text literal"),
            ("=TRUE", "logical literal"),
            ("=#REF!", "error literal"),
            ("=SUM(A:A)", "whole-column reference"),
            ("=SUM(Sales!A1:A3)", None),
            ("=[1]Other!A1", "external workbook reference"),
            ("=TaxRate*2", "defined name"),
            ("=Table1[Amount]", "table reference"),
        ],
    )
    def test_everything_else_is_declined_with_a_reason(self, formula: str, fragment: str | None):
        reason = _refusal(formula)
        if fragment:
            assert fragment in reason

    def test_a_nonnumeric_cell_as_a_scalar_is_refused(self):
        assert "non-numeric" in _refusal("=A1*2", {"A1": "twelve"})

    def test_an_empty_cell_as_a_scalar_is_refused(self):
        # Excel treats an empty cell as 0 in arithmetic, but a blank precedent
        # is so often the symptom of the bug being hunted that claiming the
        # result as verified would be the wrong kind of confident.
        assert _refusal("=A1*2", {})

    def test_unparseable_formula_is_refused_not_raised(self):
        assert _refusal("=SUM(((")

    def test_a_single_cell_range_may_stand_in_for_a_scalar(self):
        assert _value("=A1:A1*2", {"A1": 3.0}) == 6.0


class TestAgreement:
    def test_exact_and_near_values_agree(self):
        assert values_agree(1.0, 1.0) is True
        assert values_agree(0.1 + 0.2, 0.3) is True

    def test_real_differences_do_not(self):
        assert values_agree(100.0, 600.0) is False

    def test_large_magnitudes_use_a_relative_comparison(self):
        # Float noise scales with magnitude; an absolute tolerance would report
        # a discrepancy on every large correct total.
        assert values_agree(1e12, 1e12 + 1e-3) is True
        assert values_agree(1e12, 1.1e12) is False

    def test_a_noncomparable_cached_value_is_neither_agreement_nor_conflict(self):
        assert values_agree(1.0, "#DIV/0!") is None
        assert values_agree(1.0, None) is None
        assert values_agree(1.0, True) is None
