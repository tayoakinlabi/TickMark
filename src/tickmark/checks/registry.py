"""Check discovery and the runner.

One place that knows which checks exist, so adding one means adding it here
rather than editing the CLI, the server and the report renderer. This is also
where user-defined rules (item 19) will register once they exist — the lists are
built at call time rather than at import for exactly that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from tickmark.checks.base import SheetCheck, WorkbookCheck
from tickmark.checks.broken_refs import BrokenRefsCheck
from tickmark.checks.circular_refs import CircularRefsCheck
from tickmark.checks.complexity import ComplexityCheck
from tickmark.checks.double_counting import DoubleCountingCheck
from tickmark.checks.external_links import ExternalLinksCheck
from tickmark.checks.hardcoded_constants import HardcodedConstantsCheck
from tickmark.checks.inconsistent_range import InconsistentRangeCheck
from tickmark.checks.short_range import ShortRangeCheck
from tickmark.checks.stale_values import Coverage, StaleValuesCheck
from tickmark.checks.volatile_functions import VolatileFunctionsCheck
from tickmark.config.rules import DEFAULT_RULES, Rules
from tickmark.findings.grouping import group_findings
from tickmark.findings.model import Finding
from tickmark.workbook.loader import LoadedWorkbook

__all__ = ["AuditResult", "build_sheet_checks", "build_workbook_checks", "run_audit", "run_checks"]


def build_sheet_checks(rules: Rules = DEFAULT_RULES) -> list[SheetCheck]:
    """Checks 1, 2, 3, 4, 6, 33, 34 and 35 — one pass per sheet.

    Check 35 is stateful in a way the others are not: it accumulates coverage
    across the sheets it sees. One list must therefore be built per *workbook*
    and reused across its sheets, which is what :func:`run_checks` does.
    """
    return [
        InconsistentRangeCheck(min_run=rules.min_run_length, dominance=rules.dominance),
        HardcodedConstantsCheck(rules),
        BrokenRefsCheck(),
        ExternalLinksCheck(),
        VolatileFunctionsCheck(),
        ShortRangeCheck(),
        DoubleCountingCheck(),
        *([StaleValuesCheck()] if rules.evaluate_formulas else []),
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
    return run_audit(workbook, rules=rules, include_hidden=include_hidden, group=group).findings


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Everything one audit produced: the findings, and what was verified.

    Coverage travels with the findings rather than being fetched separately
    because the two must agree. A report showing findings from one run and a
    coverage figure from another would be making a claim nobody checked — and
    the claim is the entire point of publishing coverage (section 10.1).
    """

    findings: list[Finding]
    coverage: Coverage = Coverage()


def run_audit(
    workbook: LoadedWorkbook,
    *,
    rules: Rules = DEFAULT_RULES,
    include_hidden: bool = True,
    group: bool = True,
) -> AuditResult:
    """Run every check and report coverage alongside the findings."""
    findings: list[Finding] = []

    # Built once per workbook, not once per sheet: check 35 accumulates its
    # coverage counts across sheets, and a fresh instance per sheet would throw
    # away everything but the last one.
    sheet_checks = build_sheet_checks(rules)

    for sheet in workbook.sheets:
        if sheet.is_hidden and not include_hidden:
            continue
        for check in sheet_checks:
            try:
                findings.extend(check.run(sheet))
            except Exception:  # noqa: BLE001 - deliberately broad, see docstring
                continue

    for workbook_check in build_workbook_checks(rules):
        try:
            findings.extend(workbook_check.run_workbook(workbook))
        except Exception:  # noqa: BLE001 - deliberately broad, see docstring
            continue

    coverage = next(
        (c.coverage for c in sheet_checks if isinstance(c, StaleValuesCheck)),
        Coverage(),
    )

    if group:
        return AuditResult(group_findings(findings), coverage)

    findings.sort(key=lambda f: (f.severity.rank, f.sheet, f.column, f.row, f.check))
    return AuditResult(findings, coverage)
