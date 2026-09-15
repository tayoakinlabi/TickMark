"""Check 35 — a cached value that disagrees with the formula above it.

The finding: Excel's stored result for a cell is not what the formula computes.
In practice that means one of three things, and all three are worth a human's
attention:

1. The workbook was edited and never recalculated, so a number on screen is
   stale — the classic "I sent the wrong figures" incident.
2. Automatic calculation was switched off, often years ago, and nobody noticed.
3. Something outside Excel wrote the file and got the arithmetic wrong.

This is the deliberate reversal of T6. It is confined to
:mod:`tickmark.formula.evaluate`'s closed subset, and the honesty requirement
from section 10.1 is enforced structurally: the check reports coverage — how
many formulas it verified out of how many it saw — alongside its findings, so
"no findings" can never be mistaken for "everything checks out". A silent 8%
coverage would be worse than no check at all.

**Only disagreements are reported, never refusals.** A formula outside the
subset produces no finding; it decrements the verified count and nothing else.
Reporting "could not verify ``=VLOOKUP(...)``" on every lookup in a workbook
would bury the real findings under wallpaper, which is the failure mode check 7
is bounded to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass

from tickmark.findings.model import Finding, Severity
from tickmark.formula.evaluate import evaluate_formula, values_agree
from tickmark.workbook.loader import Sheet

__all__ = ["CHECK_NAME", "Coverage", "StaleValuesCheck"]

CHECK_NAME = "stale-value"


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of a workbook the evaluator could actually vouch for.

    ``verified`` and ``total`` are the numbers section 10.1 requires be
    published. ``compared`` is smaller than ``verified`` when a formula
    evaluated cleanly but Excel had cached no number to compare against — a
    workbook saved without values, which is a different situation from a formula
    the subset refused, and the report says so.
    """

    total: int = 0
    verified: int = 0
    compared: int = 0
    disagreed: int = 0

    @property
    def percentage(self) -> float:
        return 100.0 * self.verified / self.total if self.total else 0.0

    def __add__(self, other: Coverage) -> Coverage:
        return Coverage(
            self.total + other.total,
            self.verified + other.verified,
            self.compared + other.compared,
            self.disagreed + other.disagreed,
        )


def _format(value: float) -> str:
    """Render a number the way a spreadsheet reader expects to see it.

    Fifteen significant figures is where Excel itself stops, and trailing zeros
    are stripped so a whole number reads as one.
    """
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.15g}"


class StaleValuesCheck:
    """Compare each formula's computed value against the one Excel cached.

    Accumulates coverage across every sheet it is run over, so one instance
    reused for a whole workbook ends holding that workbook's totals. The
    registry relies on this: evaluating twice to count separately would double
    the cost of the most expensive check in the product.
    """

    name = CHECK_NAME

    def __init__(self) -> None:
        self.coverage = Coverage()

    def run(self, sheet: Sheet) -> list[Finding]:
        workbook = sheet.workbook
        findings: list[Finding] = []
        total = verified = compared = disagreed = 0

        def lookup(target: str, row: int, column: int) -> object:
            return workbook.cached_value(target, row, column)

        for cell in sheet.formulas():
            total += 1
            result = evaluate_formula(cell.formula or "", sheet=sheet.name, lookup=lookup)
            if not result.verified:
                continue
            verified += 1

            cached = workbook.cached_value(sheet.name, cell.row, cell.column)
            agrees = values_agree(result.value or 0.0, cached)
            if agrees is None:
                # Evaluated fine, but there is no cached number to compare
                # against. Counted as verified arithmetic, not as a comparison.
                continue
            compared += 1
            if agrees:
                continue

            disagreed += 1
            findings.append(
                Finding(
                    check=CHECK_NAME,
                    severity=Severity.HIGH,
                    sheet=sheet.name,
                    row=cell.row,
                    column=cell.column,
                    summary=(
                        f"Stored value {_format(float(cached))} does not match "
                        f"the formula, which gives {_format(result.value or 0.0)}"
                    ),
                    explanation=(
                        "The number saved in this cell is not what its formula "
                        "computes. Usually the workbook was changed and never "
                        "recalculated, so the figure on screen is out of date. "
                        "Press F9 in Excel and check whether this cell moves."
                    ),
                    formula=cell.formula,
                    context={"cached": _format(float(cached))},
                )
            )

        self.coverage = self.coverage + Coverage(total, verified, compared, disagreed)
        return findings
