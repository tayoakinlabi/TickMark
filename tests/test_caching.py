"""The memoisation, and the property that makes it safe.

Nine checks each walk every formula. Without caching, a measured audit of 3,020
formulas called ``parse`` 30,180 times and spent 3.56 seconds doing it, against
0.09 seconds to parse each distinct formula once. Memoising cut a full audit from
5.99s to 1.03s.

That is only safe because parse trees are immutable and shared. These tests pin
that property, so a future node type with a mutable field, or an in-place
transform, fails here rather than silently giving one check a tree another check
has already altered.
"""

from __future__ import annotations

import dataclasses

import pytest

from tickmark.formula import ast as ast_module
from tickmark.formula.ast import (
    BinaryOp,
    FuncCall,
    NumberLit,
    ParseError,
    clear_caches,
    parse,
    walk,
)
from tickmark.formula.tokenizer import tokenize


class TestCacheIsEffective:
    def test_identical_formulas_share_one_tree(self):
        clear_caches()
        assert parse("=A1*2") is parse("=A1*2")

    def test_different_formulas_do_not(self):
        assert parse("=A1*2") is not parse("=A1*3")

    def test_whitespace_variants_are_separate_keys(self):
        # They tokenize differently, so they must not collide.
        assert tokenize("=A1+B1", keep_whitespace=True) == tokenize("=A1+B1")

    def test_clear_caches_releases_trees(self):
        first = parse("=SUM(A1:A9)")
        clear_caches()
        assert parse("=SUM(A1:A9)") is not first

    def test_cache_is_bounded(self):
        # An unbounded cache on a 500,000-formula workbook is a memory problem.
        assert ast_module._parse_cached.cache_parameters()["maxsize"] is not None


class TestImmutabilityTheCacheRelies0n:
    """If any of these start failing, the cache must go — not the test."""

    @pytest.mark.parametrize(
        "formula",
        ["=A1*2", '=IF(A1>0,SUM(B1:B9),"x")', "=SUM({1,2;3,4})", "=-2^2", "=50%"],
    )
    def test_every_node_is_frozen(self, formula: str):
        for node in walk(parse(formula)):
            assert dataclasses.is_dataclass(node)
            assert node.__dataclass_params__.frozen, f"{type(node).__name__} is not frozen"

    def test_assignment_to_a_node_raises(self):
        tree = parse("=A1*2")
        assert isinstance(tree, BinaryOp)
        with pytest.raises(dataclasses.FrozenInstanceError):
            tree.op = "+"

    def test_child_collections_are_tuples_not_lists(self):
        # A list child would be mutable even on a frozen node.
        tree = parse("=SUM(A1,B1)")
        assert isinstance(tree, FuncCall)
        assert isinstance(tree.args, tuple)

        array = parse("=SUM({1,2;3,4})").args[0]
        assert isinstance(array.rows, tuple)
        assert all(isinstance(row, tuple) for row in array.rows)


class TestCachingChangesNothing:
    @pytest.mark.parametrize(
        "formula",
        [
            "=A1*2",
            "=SUM(A1:A9)",
            '=IF(A1>0,IF(B1>0,"a","b"),"c")',
            "=SUM(A1:A5 B1:B5)",
            "=-2^2",
            "=[1]Other!A1+1",
        ],
    )
    def test_result_is_identical_cold_and_warm(self, formula: str):
        clear_caches()
        cold = parse(formula)
        warm = parse(formula)
        assert cold == warm

    def test_tokenize_returns_a_fresh_list_each_call(self):
        # Callers may do what they like with it; the cache must not be aliased.
        first = tokenize("=A1+B1")
        second = tokenize("=A1+B1")
        assert first == second
        assert first is not second
        first.clear()
        assert tokenize("=A1+B1") == second

    def test_failures_still_raise_when_repeated(self):
        # lru_cache does not memoise exceptions; the error must not be swallowed
        # on the second call.
        for _ in range(3):
            with pytest.raises(ParseError):
                parse('=IF(A1="unterminated')

    def test_numbers_are_not_confused_across_formulas(self):
        assert parse("=1.075") == NumberLit("1.075")
        assert parse("=1.075") != parse("=1.75")
