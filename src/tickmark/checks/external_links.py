"""Check 4 — references into other workbooks.

An external link is not a defect by itself. It becomes one when the other file
moves, gets renamed, or lives on a share the next person cannot reach — and then
the formula keeps showing the last value it saw, which is worse than an error
because nothing looks wrong.

Two things make this finding actionable rather than merely true:

* **Naming the target.** Excel writes ``[1]Budget!A1``; a report that repeats
  that tells the reader nothing. The workbook-level link table resolves ``1`` to
  a path — see :meth:`~tickmark.workbook.loader.LoadedWorkbook.external_links`.
* **Saying whether it still resolves.** A link to a file that is present today is
  a note; a link to one that is gone is a problem. Paths that are relative, or on
  a share this machine cannot see, are reported as *unverifiable* rather than
  broken — claiming a link is dead because the auditor's laptop is off the VPN
  would be worse than saying nothing.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from tickmark.findings.model import Finding, Severity
from tickmark.formula.ast import ParseError, RefNode, parse, walk
from tickmark.formula.references import RangeRef
from tickmark.workbook.loader import Sheet

__all__ = ["CHECK_NAME", "ExternalLinksCheck"]

CHECK_NAME = "external-link"


def _target_status(target: str | None, base: Path) -> tuple[str, bool | None]:
    """Return ``(display, resolves)``. ``resolves`` is ``None`` when unverifiable."""
    if not target:
        return "unknown workbook", None

    parsed = urlparse(target)
    if parsed.scheme in ("http", "https"):
        # A web target cannot be checked without a network call, and Tickmark
        # makes none. NF 1 is not negotiable for a convenience check.
        return target, None
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path).lstrip("/"))
    else:
        candidate = Path(unquote(target))

    if not candidate.is_absolute():
        candidate = (base / candidate).resolve()

    try:
        return str(candidate), candidate.exists()
    except OSError:
        # An unreachable UNC share raises rather than returning False. That is
        # "cannot tell", not "missing".
        return str(candidate), None


class ExternalLinksCheck:
    """Check 4."""

    name = CHECK_NAME

    def run(self, sheet: Sheet) -> list[Finding]:
        workbook = sheet.workbook
        base = workbook.path.parent
        findings: list[Finding] = []
        seen: set[tuple[str, str]] = set()

        for cell in sheet.formulas():
            try:
                tree = parse(cell.formula or "")
            except ParseError:
                continue

            for node in walk(tree):
                if not isinstance(node, RefNode):
                    continue
                ref = node.reference
                if not isinstance(ref, RangeRef) or not ref.is_external:
                    continue

                workbook_key = ref.workbook or ""
                key = (cell.coordinate, workbook_key)
                if key in seen:
                    continue
                seen.add(key)

                target = workbook.resolve_external(workbook_key)
                display, resolves = _target_status(target, base)
                findings.append(self._finding(cell, node.text, display, resolves))

        return findings

    def _finding(self, cell, reference: str, display: str, resolves: bool | None) -> Finding:
        if resolves is False:
            severity = Severity.HIGH
            summary = "External link to a file that is not there"
            explanation = (
                f"This formula reads from {display}, which does not exist at that "
                "location. The cell will keep showing the value it last saw, so the "
                "number can look current while being stale."
            )
        elif resolves is None:
            severity = Severity.LOW
            summary = "External link that could not be verified"
            explanation = (
                f"This formula reads from {display}. Tickmark could not check whether "
                "that is reachable — it may be on a network share or the web, and no "
                "network calls are made. Confirm it by hand if the number matters."
            )
        else:
            severity = Severity.INFO
            summary = "External link"
            explanation = (
                f"This formula reads from {display}, which is present. Worth knowing "
                "about: if that file moves or is renamed, this number silently stops "
                "updating."
            )

        return Finding(
            check=CHECK_NAME,
            severity=severity,
            sheet=cell.sheet,
            row=cell.row,
            column=cell.column,
            summary=summary,
            explanation=explanation,
            formula=cell.formula,
            context={
                "reference": reference,
                "target": display,
                "resolves": {True: "yes", False: "no", None: "unknown"}[resolves],
            },
        )
