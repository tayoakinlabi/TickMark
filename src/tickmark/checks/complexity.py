"""Check 7 — the formulas that will hurt to maintain.

Nothing here is wrong. That is the point: this is the list of places where the
*next* mistake will be made, because nobody inheriting the workbook can read
them well enough to change them safely.

Reported as a bounded ranking rather than a threshold sweep. A model built by
someone who likes nested IFs could have four hundred qualifying formulas, and
four hundred findings saying "this is complicated" is not advice — it is wallpaper.
The top few are actionable; the rest are the same lesson.
"""

from __future__ import annotations

from tickmark.findings.model import Finding, Severity
from tickmark.graph.complexity import score_formula
from tickmark.workbook.loader import LoadedWorkbook

__all__ = ["CHECK_NAME", "ComplexityCheck"]

CHECK_NAME = "formula-complexity"

_DEFAULT_LIMIT = 10


class ComplexityCheck:
    """Check 7."""

    name = CHECK_NAME

    def __init__(self, limit: int = _DEFAULT_LIMIT) -> None:
        self.limit = limit

    def run_workbook(self, workbook: LoadedWorkbook) -> list[Finding]:
        scores = []
        for sheet in workbook.sheets:
            for cell in sheet.formulas():
                score = score_formula(cell)
                if score is not None and score.is_notable:
                    scores.append(score)

        scores.sort(key=lambda s: s.rank_key)
        total = len(scores)

        findings: list[Finding] = []
        for position, score in enumerate(scores[: self.limit], start=1):
            cell = score.cell
            tail = ""
            if position == 1 and total > self.limit:
                tail = (
                    f" {total} formulas in this workbook are this hard to follow; the "
                    f"{self.limit} worst are listed."
                )
            findings.append(
                Finding(
                    check=CHECK_NAME,
                    severity=Severity.INFO,
                    sheet=cell.sheet,
                    row=cell.row,
                    column=cell.column,
                    summary=f"Complex formula ({score.reason})",
                    explanation=(
                        f"This formula is {score.reason}. Nothing is necessarily wrong "
                        "with it, but whoever has to change it next will struggle to be "
                        "sure they have not broken it. Splitting it across helper cells "
                        "usually costs nothing and makes it checkable." + tail
                    ),
                    formula=cell.formula,
                    context={
                        "rank": str(position),
                        "depth": str(score.depth),
                        "length": str(score.length),
                        "references": str(score.references),
                    },
                )
            )

        return findings
