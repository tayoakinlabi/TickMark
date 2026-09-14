"""Collapse repeated findings into one.

A VAT rate written into every row of a 10,000-row invoice sheet is *one* problem,
not ten thousand. A report that lists it ten thousand times is worse than one
that lists it once, because the reader stops reading — and everything else in the
report dies with their attention.

So findings that say the same thing about a contiguous run of cells in the same
column (or row) collapse into a single finding covering the range, carrying the
count. Nothing is discarded: the range and count are in the finding, so the
report can still say exactly which cells are affected.

Deliberately conservative about what counts as "the same thing": the check, the
sheet, the summary text, and the check's own context keys must all match. Two
different hardcoded values in the same column stay separate, because they are
two different facts a reader might act on differently.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from tickmark.findings.model import Finding

__all__ = ["group_findings"]


def _identity(finding: Finding) -> tuple:
    """What must match for two findings to be the same fact.

    The formula text is deliberately excluded: ``=B2*0.2`` and ``=B3*0.2`` are
    the same finding filled down, and including it would defeat the whole
    purpose.
    """
    return (
        finding.check,
        finding.sheet,
        finding.severity,
        finding.summary,
        tuple(sorted(finding.context.items())),
    )


def _collapse_axis(group: Sequence[Finding], *, by_column: bool) -> list[Finding] | None:
    """Collapse a same-fact group into runs along one axis, if it lies on one.

    Returns ``None`` when the group does not lie in a single column (or row),
    so the caller can try the other axis.
    """
    fixed = {f.column if by_column else f.row for f in group}
    if len(fixed) != 1:
        return None

    ordered = sorted(group, key=lambda f: f.row if by_column else f.column)
    runs: list[list[Finding]] = []
    for finding in ordered:
        position = finding.row if by_column else finding.column
        if runs:
            last = runs[-1][-1]
            last_position = last.row if by_column else last.column
            if position == last_position + 1:
                runs[-1].append(finding)
                continue
        runs.append([finding])

    return [_merge(run, by_column=by_column) for run in runs]


def _merge(run: Sequence[Finding], *, by_column: bool) -> Finding:
    first = run[0]
    if len(run) == 1:
        return first

    last = run[-1]
    context = dict(first.context)
    context["cell_count"] = str(len(run))

    return Finding(
        check=first.check,
        severity=first.severity,
        sheet=first.sheet,
        row=first.row,
        column=first.column,
        summary=f"{first.summary} ({len(run)} cells)",
        explanation=first.explanation,
        formula=first.formula,
        context=context,
        span=len(run),
        end_row=last.row if by_column else first.row,
        end_column=first.column if by_column else last.column,
    )


def group_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Collapse contiguous repeats, preserving order by severity then location."""
    buckets: dict[tuple, list[Finding]] = {}
    for finding in findings:
        buckets.setdefault(_identity(finding), []).append(finding)

    out: list[Finding] = []
    for group in buckets.values():
        if len(group) == 1:
            out.extend(group)
            continue
        collapsed = _collapse_axis(group, by_column=True)
        if collapsed is None:
            collapsed = _collapse_axis(group, by_column=False)
        out.extend(collapsed if collapsed is not None else group)

    out.sort(key=lambda f: (f.severity.rank, f.sheet, f.column, f.row, f.check))
    return out
