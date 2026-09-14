"""Check discovery and the runner.

One place that knows which checks exist, so adding one means adding it here
rather than editing the CLI, the server and the report renderer. This is also
where user-defined rules (item 19) will register once they exist — the lists are
built at call time rather than at import for exactly that reason.
"""

from __future__ import annotations

from tickmark.checks.base import SheetCheck, WorkbookCheck
from tickmark.checks.broken_refs import BrokenRefsCheck
from tickmark.checks.circular_refs import CircularRefsCheck
from tickmark.checks.complexity import ComplexityCheck
from tickmark.checks.double_counting import DoubleCountingCheck
from tickmark.checks.external_links import ExternalLinksCheck
from tickmark.checks.hardcoded_constants import HardcodedConstantsCheck
from tickmark.checks.inconsistent_range import InconsistentRangeCheck
from tickmark.checks.short_range import ShortRangeCheck
from tickmark.checks.volatile_functions import VolatileFunctionsCheck
from tickmark.config.rules import DEFAULT_RULES, Rules
from tickmark.findings.grouping import group_findings
from tickmark.findings.model import Finding
from tickmark.workbook.loader import LoadedWorkbook

__all__ = ["build_sheet_checks", "build_workbook_checks", "run_checks"]


def build_sheet_checks(rules: Rules = DEFAULT_RULES) -> list[SheetCheck]:
    """Checks 1, 2, 3, 4, 6, 33 and 34 — one pass per sheet."""
    return [
        InconsistentRangeCheck(min_run=rules.min_run_length, dominance=rules.dominance),
        HardcodedConstantsCheck(rules),
        BrokenRefsCheck(),
        ExternalLinksCheck(),
        VolatileFunctionsCheck(),
        ShortRangeCheck(),
        DoubleCountingCheck(),
    ]


def build_workbook_checks(rules: Rules = DEFAULT_RULES) -> list[WorkbookCheck]:
    """Checks 5 and 7 — one pass per workbook. See ``base.py`` for why."""
    return [CircularRefsCheck(), ComplexityCheck(rules.complexity_limit)]


def run_checks(
    workbook: LoadedWorkbook,
    *,
    rules: Rules = DEFAULT_RULES,
    include_hidden: bool = True,
    group: bool = True,
) -> list[Finding]:
    """Run every check over a workbook.

    A failing check is skipped rather than fatal. One check raising on one exotic
    sheet must not cost the user the other six checks and the other forty sheets —
    the report is the deliverable, and a partial report beats a stack trace.

    Hidden sheets are audited by default: a hidden sheet feeding visible numbers
    is precisely where an unreviewed formula survives.

    Findings are grouped by default, so a rate written into 500 rows reads as one
    finding covering a range rather than 500 lines of the same sentence. Pass
    ``group=False`` for the raw per-cell list.
    """
    findings: list[Finding] = []

    for sheet in workbook.sheets:
        if sheet.is_hidden and not include_hidden:
            continue
        for check in build_sheet_checks(rules):
            try:
                findings.extend(check.run(sheet))
            except Exception:  # noqa: BLE001 - deliberately broad, see docstring
                continue

    for workbook_check in build_workbook_checks(rules):
        try:
            findings.extend(workbook_check.run_workbook(workbook))
        except Exception:  # noqa: BLE001 - deliberately broad, see docstring
            continue

    if group:
        return group_findings(findings)

    findings.sort(key=lambda f: (f.severity.rank, f.sheet, f.column, f.row, f.check))
    return findings
