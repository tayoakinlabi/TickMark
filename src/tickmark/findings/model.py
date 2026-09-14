"""What a check produces.

A finding has to survive the trip to a self-contained HTML report that someone
emails to a colleague, so every field here is something that report needs to be
actionable on its own: where it is, what is wrong, and the formula text that
shows it.

Findings carry ``row`` and ``column`` as numbers, not only as an ``A1`` string,
because :mod:`tickmark.findings.grouping` has to know which findings are
neighbours in order to collapse them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tickmark.coordinates import coordinate

__all__ = ["Finding", "Severity"]


class Severity(StrEnum):
    """How much attention a finding deserves.

    Deliberately coarse. A finer scale invites arguing about whether something is
    a 6 or a 7 instead of fixing it, and the report groups by these anyway.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Sort order, most severe first."""
        return {"high": 0, "medium": 1, "low": 2, "info": 3}[self.value]


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth a human's attention.

    ``explanation`` says why it matters in plain language — the audience is a
    bookkeeper, not a developer, and a finding nobody understands is a finding
    nobody acts on.

    ``span`` is 1 for a single cell and higher after grouping, when one finding
    stands for a run of identical ones.
    """

    check: str
    severity: Severity
    sheet: str
    row: int
    column: int
    summary: str
    explanation: str = ""
    formula: str | None = None
    context: dict[str, str] = field(default_factory=dict)
    span: int = 1
    end_row: int | None = None
    end_column: int | None = None

    @property
    def coordinate(self) -> str:
        """Excel address, or an ``A1:A9`` range for a grouped finding."""
        start = coordinate(self.row, self.column)
        if self.end_row is None and self.end_column is None:
            return start
        end = coordinate(self.end_row or self.row, self.end_column or self.column)
        return start if end == start else f"{start}:{end}"

    @property
    def location(self) -> str:
        """Human-readable location, e.g. ``Sales!C4`` or ``Sales!C2:C13``."""
        return f"{self.sheet}!{self.coordinate}"
