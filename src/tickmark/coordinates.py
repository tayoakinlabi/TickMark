"""Excel cell addressing, in one place.

Small enough to inline and duplicated three times before this module existed,
which is how ``AA``/``AB`` off-by-ones get into a report.
"""

from __future__ import annotations

__all__ = ["column_letter", "coordinate"]


def column_letter(index: int) -> str:
    """1-based column index to Excel letters: 1 -> ``A``, 27 -> ``AA``."""
    if index < 1:
        raise ValueError(f"column index must be 1 or greater, got {index}")
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def coordinate(row: int, column: int) -> str:
    """1-based row and column to an Excel address, e.g. ``B12``."""
    return f"{column_letter(column)}{row}"
