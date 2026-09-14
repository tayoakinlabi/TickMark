"""Check 3 — errors, from both places they hide.

An error reaches a workbook by two routes, and a check that watches only one of
them misses half the problem:

1. **Written into the formula.** ``=A1+#REF!`` — a deleted row left a reference
   with nowhere to point, and Excel baked the error into the text. Visible in the
   formula itself.
2. **Produced when it last calculated.** ``=1/0``, or a ``VLOOKUP`` whose key
   went missing. The formula text looks perfectly healthy; only the value Excel
   cached reveals it.

The second kind is the more dangerous one in an audit, because the formula reads
correctly and the sheet looks fine until someone scrolls to the cell.

A caveat stated in the finding rather than hidden: cached values are what Excel
last wrote, so a workbook edited and never recalculated can carry stale errors —
or hide fresh ones. Tickmark does not evaluate formulas, so it reports what the
file says and says where that came from.
"""

from __future__ import annotations

from tickmark.findings.model import Finding, Severity
from tickmark.formula.ast import ErrorLit, ParseError, parse, walk
from tickmark.workbook.loader import Sheet

__all__ = ["CHECK_NAME", "BrokenRefsCheck", "ERROR_MEANINGS"]

CHECK_NAME = "broken-reference"

# Plain-language meanings. The audience is a bookkeeper, and "#NAME?" alone
# tells them nothing they can act on.
ERROR_MEANINGS: dict[str, str] = {
    "#REF!": (
        "points at a cell that no longer exists, usually because a row, column or sheet was deleted"
    ),
    "#NAME?": (
        "uses a name Excel does not recognise, often a typo in a function name "
        "or a deleted defined name"
    ),
    "#VALUE!": "was given the wrong kind of value, such as text where a number was expected",
    "#DIV/0!": "divides by zero or by an empty cell",
    "#N/A": "looked something up and did not find it",
    "#NULL!": "refers to an intersection of two ranges that do not overlap",
    "#NUM!": "produced a number too large, too small, or otherwise invalid",
    "#SPILL!": "cannot spill its results because something is in the way",
    "#CALC!": "describes a calculation Excel cannot complete",
}

_SEVERITY = {
    "#REF!": Severity.HIGH,
    "#NAME?": Severity.HIGH,
    "#DIV/0!": Severity.MEDIUM,
    "#VALUE!": Severity.MEDIUM,
    "#NUM!": Severity.MEDIUM,
    "#NULL!": Severity.MEDIUM,
    "#SPILL!": Severity.MEDIUM,
    "#CALC!": Severity.MEDIUM,
    # Often deliberate — an unmatched lookup is frequently expected — so this
    # one is reported without crying wolf.
    "#N/A": Severity.LOW,
}


def _describe(error: str) -> str:
    meaning = ERROR_MEANINGS.get(error)
    return f"This cell {meaning}." if meaning else f"This cell holds the error {error}."


class BrokenRefsCheck:
    """Check 3."""

    name = CHECK_NAME

    def run(self, sheet: Sheet) -> list[Finding]:
        findings: dict[tuple[int, int], Finding] = {}
        formulas = {(c.row, c.column): c for c in sheet.formulas()}

        # Route 1: error literals written into the formula text.
        for (row, column), cell in formulas.items():
            try:
                tree = parse(cell.formula or "")
            except ParseError:
                continue
            for node in walk(tree):
                if isinstance(node, ErrorLit):
                    findings[(row, column)] = Finding(
                        check=CHECK_NAME,
                        severity=_SEVERITY.get(node.text, Severity.MEDIUM),
                        sheet=cell.sheet,
                        row=cell.row,
                        column=cell.column,
                        summary=f"{node.text} written into the formula",
                        explanation=(
                            f"{_describe(node.text)} The error is part of the formula text, "
                            "so it will not go away on recalculation — the formula itself "
                            "needs repairing."
                        ),
                        formula=cell.formula,
                        context={"error": node.text, "source": "formula"},
                    )
                    break

        # Route 2: errors in the values Excel last cached.
        for row, column, error in sheet.error_values():
            if (row, column) in findings:
                continue
            cell = formulas.get((row, column))
            findings[(row, column)] = Finding(
                check=CHECK_NAME,
                severity=_SEVERITY.get(error, Severity.MEDIUM),
                sheet=sheet.name,
                row=row,
                column=column,
                summary=f"{error} in the last calculated value",
                explanation=(
                    f"{_describe(error)} The formula text looks normal, so this is only "
                    "visible in the value Excel last saved — which is exactly why it "
                    "tends to go unnoticed."
                ),
                formula=cell.formula if cell else None,
                context={"error": error, "source": "cached-value"},
            )

        return sorted(findings.values(), key=lambda f: (f.sheet, f.column, f.row))
