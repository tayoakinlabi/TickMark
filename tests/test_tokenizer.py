"""The tokenizer cases that justify T3.

These are the inputs that defeat regex-based formula parsing. They are kept as a
test rather than a one-off probe so that an openpyxl upgrade cannot quietly
regress the decision recorded in 04-tickmark.md section 9.
"""

from __future__ import annotations

import pytest

from tickmark.formula.tokenizer import (
    FormulaSyntaxError,
    TokenSubtype,
    TokenType,
    tokenize,
)


def values(formula: str) -> list[str]:
    return [t.value for t in tokenize(formula)]


class TestHardCases:
    def test_comma_and_parens_inside_string_literal(self):
        toks = tokenize('=IF(A1="a,b(c)",1,0)')
        texts = [t for t in toks if t.subtype is TokenSubtype.TEXT]
        assert [t.value for t in texts] == ['"a,b(c)"']

    def test_escaped_quotes_inside_string(self):
        toks = tokenize('=IF(A1="say ""hi""",1,0)')
        texts = [t for t in toks if t.subtype is TokenSubtype.TEXT]
        assert [t.value for t in texts] == ['"say ""hi"""']

    def test_quoted_sheet_name_with_space(self):
        assert "'Sales Q3'!$B$2" in values("='Sales Q3'!$B$2+C3")

    def test_external_workbook_reference(self):
        assert "[1]Budget!A1" in values("=[1]Budget!A1*2")

    def test_array_constant_separates_rows_from_args(self):
        toks = tokenize("=SUM({1,2;3,4})")
        assert any(t.subtype is TokenSubtype.ROW for t in toks)
        assert any(t.subtype is TokenSubtype.ARG for t in toks)

    def test_error_literal_is_typed(self):
        toks = tokenize("=A1+#REF!")
        assert any(t.is_error and t.value == "#REF!" for t in toks)

    def test_whitespace_is_the_intersection_operator(self):
        kept = tokenize("=SUM(A1:A5 B1:B5,C1)", keep_whitespace=True)
        assert any(t.type is TokenType.WHITESPACE for t in kept)
        dropped = tokenize("=SUM(A1:A5 B1:B5,C1)")
        assert all(t.type is not TokenType.WHITESPACE for t in dropped)

    def test_unary_prefix_and_postfix_operators(self):
        toks = tokenize("=-A1%+3")
        assert any(t.type is TokenType.OPERATOR_PREFIX for t in toks)
        assert any(t.type is TokenType.OPERATOR_POSTFIX for t in toks)

    def test_structured_table_reference(self):
        assert "Table1[Amount]" in values("=SUM(Table1[Amount])")


class TestTokenHelpers:
    def test_function_name_strips_trailing_paren(self):
        toks = tokenize("=SUM(A1)")
        names = [t.function_name for t in toks if t.is_func_open]
        assert names == ["SUM"]

    def test_function_name_is_none_for_non_function_tokens(self):
        toks = tokenize("=A1")
        assert all(t.function_name is None for t in toks)

    def test_number_and_range_predicates(self):
        toks = tokenize("=B2*1.075")
        assert [t.value for t in toks if t.is_number] == ["1.075"]
        assert [t.value for t in toks if t.is_range] == ["B2"]


class TestInputForms:
    def test_leading_equals_is_optional(self):
        assert values("=A1+1") == values("A1+1")

    def test_array_formula_wrapper_is_stripped(self):
        assert values("{=SUM(A1:A5)}") == values("=SUM(A1:A5)")

    def test_empty_formula_yields_no_tokens(self):
        assert tokenize("") == []
        assert tokenize("   ") == []

    def test_malformed_formula_raises_with_context(self):
        with pytest.raises(FormulaSyntaxError) as excinfo:
            tokenize('=IF(A1="unterminated')
        assert excinfo.value.formula == '=IF(A1="unterminated'
