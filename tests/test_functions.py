from __future__ import annotations

import pytest

from tickmark.formula.functions import (
    is_conditionally_volatile,
    is_lookup,
    is_volatile,
    normalize,
)


class TestVolatileDetection:
    @pytest.mark.parametrize("name", ["NOW", "TODAY", "RAND", "OFFSET", "INDIRECT", "CELL", "INFO"])
    def test_check_six_names_are_volatile(self, name):
        assert is_volatile(name)

    def test_case_insensitive(self):
        assert is_volatile("now")
        assert is_volatile("Offset")

    def test_non_volatile(self):
        assert not is_volatile("SUM")
        assert not is_volatile("VLOOKUP")

    def test_conditionally_volatile_is_separate(self):
        assert is_conditionally_volatile("INDEX")
        assert not is_volatile("INDEX")


class TestNormalization:
    def test_strips_xlfn_prefix(self):
        # Without this, every modern workbook's XLOOKUP would be missed.
        assert normalize("_xlfn.XLOOKUP") == "XLOOKUP"
        assert is_lookup("_xlfn.XLOOKUP")

    def test_strips_implicit_intersection_marker(self):
        assert normalize("@SUM") == "SUM"

    def test_strips_worksheet_prefix(self):
        assert normalize("_xlws.FILTER") == "FILTER"

    def test_uppercases(self):
        assert normalize("  vlookup ") == "VLOOKUP"
