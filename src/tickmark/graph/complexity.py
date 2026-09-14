"""Formula complexity ranking (check 7).

Not a correctness check — a maintenance one. The output is the "this will hurt"
list: the formulas that whoever inherits this workbook will be unable to reason
about, which is where the next mistake gets made.

Three signals, because no single one is right on its own:

* **Nesting depth** — seven nested ``IF``s is unreadable however short it is.
* **Length** — a 400-character formula is unreadable however flat it is.
* **Reference count** — a formula pulling from twenty places is hard to verify
  even when it is shallow and short.

Only formulas past a threshold are ranked at all, so a tidy workbook reports
nothing rather than dutifully naming its longest trivial formula.
"""

from __future__ import annotations

from dataclasses import dataclass

from tickmark.formula.ast import ParseError, RefNode, depth, parse, walk
from tickmark.workbook.loader import Cell

__all__ = ["ComplexityScore", "score_formula", "DEPTH_THRESHOLD", "LENGTH_THRESHOLD"]

# Below all of these, a formula is ordinary and not worth a reader's attention.
DEPTH_THRESHOLD = 6
LENGTH_THRESHOLD = 120
REFERENCE_THRESHOLD = 12


@dataclass(frozen=True, slots=True)
class ComplexityScore:
    cell: Cell
    depth: int
    length: int
    references: int

    @property
    def is_notable(self) -> bool:
        return (
            self.depth >= DEPTH_THRESHOLD
            or self.length >= LENGTH_THRESHOLD
            or self.references >= REFERENCE_THRESHOLD
        )

    @property
    def rank_key(self) -> tuple[int, int, int]:
        """Sort key, worst first.

        Depth leads: a deeply nested formula is harder to follow than a long flat
        one of the same size, and it is the one people misread.
        """
        return (-self.depth, -self.length, -self.references)

    @property
    def reason(self) -> str:
        """Why this formula was picked, in the reader's terms."""
        parts: list[str] = []
        if self.depth >= DEPTH_THRESHOLD:
            parts.append(f"nested {self.depth} levels deep")
        if self.length >= LENGTH_THRESHOLD:
            parts.append(f"{self.length} characters long")
        if self.references >= REFERENCE_THRESHOLD:
            parts.append(f"reads from {self.references} places")
        return " and ".join(parts) if parts else "unusually complex"


def score_formula(cell: Cell) -> ComplexityScore | None:
    """Score one formula, or ``None`` if it cannot be parsed."""
    text = cell.formula or ""
    try:
        tree = parse(text)
    except ParseError:
        return None

    references = sum(1 for node in walk(tree) if isinstance(node, RefNode))
    return ComplexityScore(cell=cell, depth=depth(tree), length=len(text), references=references)
