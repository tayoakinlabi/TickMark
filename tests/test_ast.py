from __future__ import annotations

import pytest

from tickmark.formula.ast import (
    ArrayLit,
    BinaryOp,
    ErrorLit,
    FuncCall,
    NumberLit,
    ParseError,
    PostfixOp,
    RefNode,
    TextLit,
    UnaryOp,
    depth,
    parse,
    walk,
)


class TestExcelPrecedenceTraps:
    """The two places Excel disagrees with almost every programming language."""

    def test_negation_binds_tighter_than_exponent(self):
        # =-2^2 is 4 in Excel, i.e. (-2)^2 — not -(2^2).
        tree = parse("=-2^2")
        assert isinstance(tree, BinaryOp)
        assert tree.op == "^"
        assert isinstance(tree.left, UnaryOp)
        assert tree.left.op == "-"

    def test_exponent_is_left_associative(self):
        # =2^3^2 is 64 in Excel, i.e. (2^3)^2 — not 2^(3^2).
        tree = parse("=2^3^2")
        assert isinstance(tree, BinaryOp)
        assert tree.op == "^"
        assert isinstance(tree.left, BinaryOp)
        assert tree.left.op == "^"
        assert tree.right == NumberLit("2")


class TestPrecedence:
    def test_multiplication_binds_tighter_than_addition(self):
        tree = parse("=1+2*3")
        assert tree.op == "+"
        assert tree.right.op == "*"

    def test_parentheses_override_precedence(self):
        tree = parse("=(1+2)*3")
        assert tree.op == "*"
        assert tree.left.op == "+"

    def test_comparison_is_loosest(self):
        tree = parse("=A1+1>B1*2")
        assert tree.op == ">"
        assert tree.left.op == "+"
        assert tree.right.op == "*"

    def test_concatenation_binds_looser_than_arithmetic(self):
        tree = parse("=A1&B1+1")
        assert tree.op == "&"
        assert tree.right.op == "+"

    def test_subtraction_is_left_associative(self):
        tree = parse("=10-3-2")
        assert tree.op == "-"
        assert tree.left.op == "-"

    def test_postfix_percent(self):
        tree = parse("=50%")
        assert isinstance(tree, PostfixOp)
        assert tree.op == "%"


class TestIntersectionOperator:
    def test_space_between_operands_is_an_operator(self):
        tree = parse("=SUM(A1:A5 B1:B5)")
        assert isinstance(tree, FuncCall)
        (arg,) = tree.args
        assert isinstance(arg, BinaryOp)
        assert arg.op == " "

    def test_space_around_separators_is_formatting(self):
        tree = parse("=SUM( A1 , B1 )")
        assert isinstance(tree, FuncCall)
        assert len(tree.args) == 2
        assert all(isinstance(a, RefNode) for a in tree.args)

    def test_space_around_infix_operators_is_formatting(self):
        assert parse("=A1 + B1") == parse("=A1+B1")

    def test_leading_and_trailing_space_is_formatting(self):
        assert parse("= A1 ") == parse("=A1")

    def test_union_operator(self):
        tree = parse("=SUM((A1:A5,B1:B5))")
        (arg,) = tree.args
        assert isinstance(arg, BinaryOp)
        assert arg.op == ","


class TestLiterals:
    def test_number(self):
        assert parse("=1.075") == NumberLit("1.075")
        assert parse("=1.075").value == pytest.approx(1.075)

    def test_text_unescapes_doubled_quotes(self):
        node = parse('="say ""hi"""')
        assert isinstance(node, TextLit)
        assert node.value == 'say "hi"'

    def test_logical(self):
        assert parse("=TRUE").value is True
        assert parse("=FALSE").value is False

    def test_error_literal(self):
        assert parse("=#REF!") == ErrorLit("#REF!")


class TestFunctions:
    def test_no_arguments(self):
        assert parse("=NOW()") == FuncCall("NOW", ())

    def test_nested_calls(self):
        tree = parse('=OFFSET(INDIRECT("A1"),1,1)')
        assert tree.name == "OFFSET"
        assert tree.args[0].name == "INDIRECT"

    def test_omitted_argument_is_kept_positional(self):
        # =IF(A1,,0) is legal Excel; dropping the gap would shift argument
        # indices and misreport which argument a finding refers to.
        tree = parse("=IF(A1,,0)")
        assert len(tree.args) == 3

    def test_commas_inside_arguments_are_separators_not_union(self):
        tree = parse("=VLOOKUP(A1,Data!$A:$C,3,FALSE)")
        assert len(tree.args) == 4

    def test_unterminated_call_raises(self):
        with pytest.raises(ParseError):
            parse("=SUM(A1")


class TestArrays:
    def test_rows_and_columns(self):
        tree = parse("=SUM({1,2;3,4})")
        (arg,) = tree.args
        assert isinstance(arg, ArrayLit)
        assert len(arg.rows) == 2
        assert [n.text for n in arg.rows[0]] == ["1", "2"]
        assert [n.text for n in arg.rows[1]] == ["3", "4"]

    def test_single_row(self):
        tree = parse("={1,2,3}")
        assert len(tree.rows) == 1
        assert len(tree.rows[0]) == 3


class TestReferences:
    def test_reference_is_parsed(self):
        node = parse("='Sales Q3'!$B$2")
        assert isinstance(node, RefNode)
        assert node.reference is not None
        assert node.reference.sheet == "Sales Q3"

    def test_unparseable_reference_keeps_raw_text(self):
        # A whole formula must never be lost over one odd reference.
        node = parse("=SUM(Table1[Amount])").args[0]
        assert isinstance(node, RefNode)
        assert node.text == "Table1[Amount]"


class TestTraversal:
    def test_walk_visits_every_node(self):
        tree = parse("=IF(A1>0,SUM(B1:B9),0)")
        refs = [n.text for n in walk(tree) if isinstance(n, RefNode)]
        assert refs == ["A1", "B1:B9"]

    def test_depth_of_flat_formula(self):
        assert depth(parse("=A1")) == 1

    def test_depth_grows_with_nesting(self):
        shallow = depth(parse("=IF(A1>0,1,0)"))
        deep = depth(parse("=IF(A1>0,IF(B1>0,IF(C1>0,SUM(D1:D9),0),0),0)"))
        assert deep > shallow

    def test_parentheses_contribute_depth_via_structure(self):
        assert depth(parse("=(A1+B1)*C1")) > depth(parse("=A1+B1"))


class TestErrors:
    def test_empty_formula_raises(self):
        with pytest.raises(ParseError):
            parse("=")

    def test_unbalanced_paren_raises(self):
        with pytest.raises(ParseError):
            parse("=(A1+B1")

    def test_trailing_garbage_raises(self):
        with pytest.raises(ParseError):
            parse("=A1 B1)")
