"""Item 19 — checks the user wrote themselves.

04-tickmark.md calls this "what attracts contributors", and the shape is chosen
with that in mind: a rule is a regular expression plus the wording the report
should use, declared in ``tickmark.toml``. No expression language, for the same
reason item 30 rejects one — a declarative rule is a spec to parse, an expression
language is a language to maintain, and only one of those is finishable.

Every house rule a bookkeeper actually has is reachable this way. "Nobody should
be hardcoding the VAT rate again", "no formula may reach into the archive
workbook", "flag anything still saying TODO before this goes to the auditor" are
all one pattern each.

**A user's rule can never break the built-in checks.** A pattern that does not
compile is dropped with a message and the audit continues; a rule that throws
while running is skipped for that cell. The nine checks the product is judged on
do not become less reliable because someone typo'd a regex at four in the
afternoon.
"""

from __future__ import annotations

import re
from functools import lru_cache

from tickmark.config.rules import DEFAULT_RULES, CustomRule, Rules
from tickmark.findings.model import Finding, Severity
from tickmark.workbook.loader import Sheet

__all__ = ["CHECK_NAME", "CustomRulesCheck", "compile_rule"]

CHECK_NAME = "custom-rule"

# Long formulas exist, but a pattern run against one of several thousand
# characters is where a careless regex turns an audit into a hang. Truncating the
# subject bounds the worst case without changing any realistic match.
_MAX_SUBJECT = 8192


@lru_cache(maxsize=256)
def compile_rule(pattern: str) -> re.Pattern[str] | None:
    """Compile a user pattern, or ``None`` if it is not a valid regex.

    Cached because the same handful of patterns is applied to every cell in
    every sheet, and recompiling per cell would make custom rules the slowest
    thing in the product by a wide margin.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def _severity(name: str) -> Severity:
    try:
        return Severity(name)
    except ValueError:  # pragma: no cover - the loader validates this first
        return Severity.MEDIUM


class CustomRulesCheck:
    """Run every user-defined rule over a sheet.

    Reports which rule matched by name, because a report that says "custom rule"
    with no indication of *which* one sends the reader back to their config file
    to guess.
    """

    name = CHECK_NAME

    def __init__(self, rules: Rules = DEFAULT_RULES) -> None:
        self._rules = tuple(rules.custom_rules)

    def run(self, sheet: Sheet) -> list[Finding]:
        if not self._rules:
            return []

        compiled: list[tuple[CustomRule, re.Pattern[str]]] = []
        for rule in self._rules:
            pattern = compile_rule(rule.pattern)
            if pattern is not None:
                compiled.append((rule, pattern))
        if not compiled:
            return []

        findings: list[Finding] = []
        for cell in sheet.cells():
            formula = cell.formula or ""
            value = "" if cell.value is None else str(cell.value)
            for rule, pattern in compiled:
                subject = formula if rule.target == "formula" else value
                if not subject:
                    continue
                try:
                    if not pattern.search(subject[:_MAX_SUBJECT]):
                        continue
                except Exception:  # noqa: BLE001 - a user's pattern is not trusted
                    continue
                findings.append(
                    Finding(
                        check=CHECK_NAME,
                        severity=_severity(rule.severity),
                        sheet=cell.sheet,
                        row=cell.row,
                        column=cell.column,
                        summary=rule.summary,
                        explanation=(
                            rule.explanation
                            or f"This cell matched your rule '{rule.name}' from tickmark.toml."
                        ),
                        formula=cell.formula,
                        context={"rule": rule.name},
                    )
                )
        return findings
