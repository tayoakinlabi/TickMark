"""Check 5 — circular references.

Excel says a workbook has one. It does not say *which cells*, and on a large
model people stop looking — which is how a file ends up shipped with iterative
calculation switched on and nobody able to explain what it is iterating, or
whether the number on the front page has converged to anything meaningful.

So the finding names the loop. For a short cycle it lists the whole path; for a
long one it names the first few and says how many there are, because a finding
that prints 400 cell addresses is not a finding anyone reads.
"""

from __future__ import annotations

from tickmark.findings.model import Finding, Severity
from tickmark.graph.cycles import find_cycles
from tickmark.graph.dependency import CellKey, DependencyGraph
from tickmark.workbook.loader import LoadedWorkbook

__all__ = ["CHECK_NAME", "CircularRefsCheck"]

CHECK_NAME = "circular-reference"

# Beyond this, the path is summarised rather than listed in full.
_MAX_LISTED = 6


def _describe_path(cycle: list[CellKey]) -> str:
    if len(cycle) <= _MAX_LISTED:
        return " → ".join(str(key) for key in cycle)
    shown = " → ".join(str(key) for key in cycle[:_MAX_LISTED])
    return f"{shown} → … ({len(cycle)} cells in total)"


class CircularRefsCheck:
    """Check 5."""

    name = CHECK_NAME

    def run_workbook(self, workbook: LoadedWorkbook) -> list[Finding]:
        graph = DependencyGraph(workbook)
        findings: list[Finding] = []

        for cycle in find_cycles(graph):
            anchor = cycle[0]
            path = _describe_path(cycle)

            if len(cycle) == 1:
                summary = "Formula includes its own cell"
                explanation = (
                    f"{anchor} reads from a range that contains {anchor} itself. Excel "
                    "cannot resolve this, so the cell shows 0 or whatever the last "
                    "iteration produced — usually it means a total was typed one row "
                    "short, or one row too far."
                )
            else:
                summary = f"Circular reference through {len(cycle)} cells"
                explanation = (
                    f"These cells depend on each other in a loop: {path}. Excel cannot "
                    "settle on a value for any of them, so every number downstream is "
                    "suspect until the loop is broken."
                )

            findings.append(
                Finding(
                    check=CHECK_NAME,
                    severity=Severity.HIGH,
                    sheet=anchor.sheet,
                    row=anchor.row,
                    column=anchor.column,
                    summary=summary,
                    explanation=explanation,
                    formula=graph.formula(anchor),
                    context={"cycle_length": str(len(cycle)), "path": path},
                )
            )

        return findings
