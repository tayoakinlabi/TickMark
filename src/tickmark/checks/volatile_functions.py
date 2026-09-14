"""Check 6 — volatile functions.

Nothing here is numerically wrong, which is why this check reports at low
severity. But a volatile function recalculates on every edit anywhere in the
workbook, and it drags everything that depends on it along. A few hundred of them
is the usual reason a file that looks small takes ten seconds to respond to a
keystroke — and the usual reason someone concludes "Excel is slow" rather than
"this workbook has 400 OFFSETs in it".

Reported per cell, so the report can point at them, with a count so the summary
can say how bad it is.
"""

from __future__ import annotations

from tickmark.findings.model import Finding, Severity
from tickmark.formula.ast import FuncCall, ParseError, parse, walk
from tickmark.formula.functions import is_volatile, normalize
from tickmark.workbook.loader import Sheet

__all__ = ["CHECK_NAME", "VolatileFunctionsCheck"]

CHECK_NAME = "volatile-function"

_WHY: dict[str, str] = {
    "NOW": "returns the current time, so it changes constantly",
    "TODAY": "returns today's date, so it changes daily",
    "RAND": "returns a new random number on every recalculation",
    "RANDBETWEEN": "returns a new random number on every recalculation",
    "RANDARRAY": "returns new random numbers on every recalculation",
    "OFFSET": "builds a reference on the fly, so Excel cannot tell in advance what it depends on",
    "INDIRECT": "builds a reference from text, so Excel cannot tell in advance what it depends on",
    "CELL": "inspects the state of a cell, which Excel re-checks constantly",
    "INFO": "inspects the state of the environment, which Excel re-checks constantly",
}


class VolatileFunctionsCheck:
    """Check 6."""

    name = CHECK_NAME

    def run(self, sheet: Sheet) -> list[Finding]:
        findings: list[Finding] = []

        for cell in sheet.formulas():
            try:
                tree = parse(cell.formula or "")
            except ParseError:
                continue

            names = sorted(
                {
                    normalize(node.name)
                    for node in walk(tree)
                    if isinstance(node, FuncCall) and is_volatile(node.name)
                }
            )
            if not names:
                continue

            reasons = "; ".join(f"{n} {_WHY.get(n, 'is volatile')}" for n in names)
            findings.append(
                Finding(
                    check=CHECK_NAME,
                    severity=Severity.LOW,
                    sheet=cell.sheet,
                    row=cell.row,
                    column=cell.column,
                    summary=f"Volatile function: {', '.join(names)}",
                    explanation=(
                        f"{reasons}. Volatile functions force Excel to recalculate this "
                        "cell — and everything that depends on it — every time anything "
                        "in the workbook changes. A few are harmless; many are why a "
                        "workbook feels slow."
                    ),
                    formula=cell.formula,
                    context={"functions": ", ".join(names)},
                )
            )

        return findings
