"""Shape normalisation — the primitive behind check 1.

The tests that matter are the two at the top: a correctly filled-down column must
produce identical shapes, and the overwritten cell that check 1 exists to find
must not.
"""

from __future__ import annotations

import pytest

from tickmark.formula.shape import ShapeError, formula_shape, normalize_reference


class TestCheckOnePrimitive:
    def test_filled_down_column_has_one_shape(self):
        # =A1*2 in B1, =A2*2 in B2, =A3*2 in B3 — correctly filled down.
        shapes = {formula_shape(f"=A{row}*2", origin_row=row, origin_column=2) for row in (1, 2, 3)}
        assert len(shapes) == 1

    def test_overwritten_cell_stands_out(self):
        # B3 was overwritten to point back at A1 — the bug check 1 hunts for.
        good = [formula_shape(f"=A{row}*2", origin_row=row, origin_column=2) for row in (1, 2)]
        bad = formula_shape("=A1*2", origin_row=3, origin_column=2)
        assert bad not in good

    def test_constant_replacing_a_formula_stands_out(self):
        good = formula_shape("=A2*2", origin_row=2, origin_column=2)
        hardcoded = formula_shape("=1234", origin_row=3, origin_column=2)
        assert good != hardcoded

    def test_filled_across_row_has_one_shape(self):
        # =A$1*2 in B2, =B$1*2 in C2 — filled across, absolute row.
        shapes = {
            formula_shape(f"={col}$1*2", origin_row=2, origin_column=idx)
            for col, idx in (("A", 2), ("B", 3))
        }
        assert len(shapes) == 1


class TestReferenceNormalization:
    def test_relative_reference_becomes_offset(self):
        assert normalize_reference("A1", origin_row=2, origin_column=2) == "R[-1]C[-1]"

    def test_same_cell_has_no_offset(self):
        assert normalize_reference("B2", origin_row=2, origin_column=2) == "RC"

    def test_absolute_reference_keeps_index(self):
        assert normalize_reference("$A$1", origin_row=2, origin_column=2) == "R1C1"

    def test_mixed_absolute_reference(self):
        assert normalize_reference("$A1", origin_row=2, origin_column=2) == "R[-1]C1"

    def test_range_normalizes_both_corners(self):
        assert normalize_reference("A1:A5", origin_row=6, origin_column=1) == "R[-5]C:R[-1]C"

    def test_sheet_qualification_is_preserved(self):
        assert normalize_reference("Data!$A$1", origin_row=1, origin_column=1) == "Data!R1C1"

    def test_external_workbook_is_preserved(self):
        out = normalize_reference("[1]Budget!$A$1", origin_row=1, origin_column=1)
        assert out == "[1]Budget!R1C1"

    def test_defined_names_are_left_alone(self):
        # A name does not shift when filled down, so it must not be offset.
        assert normalize_reference("TaxRate", origin_row=9, origin_column=4) == "TaxRate"

    def test_table_references_are_left_alone(self):
        assert normalize_reference("Table1[Amount]", origin_row=9, origin_column=4) == (
            "Table1[Amount]"
        )


class TestShapePreservesMeaning:
    def test_string_literals_are_untouched(self):
        shape = formula_shape('=IF(A1="a,b(c)",1,0)', origin_row=1, origin_column=2)
        assert '"a,b(c)"' in shape

    def test_intersection_operator_survives(self):
        shape = formula_shape("=SUM(A1:A5 B1:B5)", origin_row=10, origin_column=1)
        assert " " in shape

    def test_intersection_and_union_are_different_shapes(self):
        intersect = formula_shape("=SUM(A1:A5 B1:B5)", origin_row=10, origin_column=1)
        union = formula_shape("=SUM(A1:A5,B1:B5)", origin_row=10, origin_column=1)
        assert intersect != union

    def test_whitespace_is_collapsed_not_removed(self):
        wide = formula_shape("=SUM(A1:A5   B1:B5)", origin_row=10, origin_column=1)
        narrow = formula_shape("=SUM(A1:A5 B1:B5)", origin_row=10, origin_column=1)
        assert wide == narrow

    def test_function_names_are_preserved(self):
        shape = formula_shape("=VLOOKUP(A1,Data!$A:$C,3,FALSE)", origin_row=1, origin_column=2)
        assert "VLOOKUP" in shape
        assert "Data!" in shape

    def test_malformed_formula_raises(self):
        with pytest.raises(ShapeError):
            formula_shape('=IF(A1="unterminated', origin_row=1, origin_column=1)
