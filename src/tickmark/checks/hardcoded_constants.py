"""Check 2 — a business number buried inside a formula.

``=B2*1.075`` is the shape of the problem: the tax rate lives in one cell's
formula instead of a named cell, so when the rate changes someone has to find
every formula that mentions it, and they will miss one.

The difficulty is not finding numbers — it is *not* reporting the harmless ones.
``=A1*100``, ``=B2/12`` and ``=VLOOKUP(A1,Data,3,FALSE)`` all contain literals
that mean nothing on their own. A check that flags those gets switched off, and a
switched-off check finds nothing at all. Three suppressions carry that weight:

1. **An ignore list** of numbers that are structurally meaningless (T4, tunable).
2. **Structural argument positions** — the ``3`` in VLOOKUP is a column index,
   not a rate.
3. **Exponents** — the ``2`` in ``=A1^2`` is part of the operation.

What survives is mostly non-integer literals in arithmetic, which is exactly the
population worth a human's time. Integers that get past the ignore list are
reported only when ``report_integer_constants`` is on.
"""

from __future__ import annotations

from collections.abc import Iterator

from tickmark.config.rules import DEFAULT_RULES, Rules
from tickmark.findings.model import Finding, Severity
from tickmark.formula.ast import (
    ArrayLit,
    BinaryOp,
    FuncCall,
    Node,
    NumberLit,
    ParseError,
    PostfixOp,
    UnaryOp,
    parse,
)
from tickmark.workbook.loader import Cell, Sheet

__all__ = ["CHECK_NAME", "HardcodedConstantsCheck"]

CHECK_NAME = "hardcoded-constant"


def _literals(node: Node, rules: Rules) -> Iterator[NumberLit]:
    """Yield numeric literals that survive the structural suppressions."""
    match node:
        case NumberLit():
            yield node
        case FuncCall(name=name, args=args):
            for index, arg in enumerate(args):
                if isinstance(arg, NumberLit) and rules.is_structural_arg(name, index):
                    continue
                yield from _literals(arg, rules)
        case BinaryOp(op="^", left=left):
            # The exponent is part of the operation, not a buried value.
            yield from _literals(left, rules)
        case BinaryOp(left=left, right=right):
            yield from _literals(left, rules)
            yield from _literals(right, rules)
        case UnaryOp(operand=operand) | PostfixOp(operand=operand):
            yield from _literals(operand, rules)
        case ArrayLit():
            # An array constant is a table written inline. Its numbers are data,
            # and reporting each one would bury everything else in the report.
            return
        case _:
            return


class HardcodedConstantsCheck:
    """Check 2."""

    name = CHECK_NAME

    def __init__(self, rules: Rules = DEFAULT_RULES) -> None:
        self.rules = rules

    def run(self, sheet: Sheet) -> list[Finding]:
        findings: list[Finding] = []
        for cell in sheet.formulas():
            findings.extend(self._check_cell(cell))
        return findings

    def _check_cell(self, cell: Cell) -> Iterator[Finding]:
        try:
            tree = parse(cell.formula or "")
        except ParseError:
            return

        seen: set[str] = set()
        for literal in _literals(tree, self.rules):
            try:
                value = literal.value
            except ValueError:  # pragma: no cover - tokenizer guarantees numeric
                continue
            if self.rules.is_ignored_number(value):
                continue

            is_integer = float(value).is_integer()
            if is_integer and not self.rules.report_integer_constants:
                continue
            if literal.text in seen:
                continue
            seen.add(literal.text)

            yield Finding(
                check=CHECK_NAME,
                severity=Severity.LOW if is_integer else Severity.MEDIUM,
                sheet=cell.sheet,
                row=cell.row,
                column=cell.column,
                summary=f"Hardcoded value {literal.text} inside a formula",
                explanation=(
                    f"This formula contains the fixed number {literal.text}. If that is a "
                    "rate, price or threshold, it should live in its own labelled cell "
                    "that formulas point at — otherwise changing it means finding every "
                    "formula that mentions it, and one will be missed."
                ),
                formula=cell.formula,
                context={"value": literal.text},
            )
