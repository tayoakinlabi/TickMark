"""Workbook access.

Everything that touches a file on disk lives here, and one rule governs all of it:

    Tickmark never writes to an audited workbook.

See ``loader.py`` for how that is enforced rather than merely intended.
"""

from tickmark.workbook.loader import (
    Cell,
    LoadedWorkbook,
    PasswordProtectedError,
    Sheet,
    UnsupportedFormatError,
    WorkbookError,
    open_workbook,
)

__all__ = [
    "Cell",
    "LoadedWorkbook",
    "PasswordProtectedError",
    "Sheet",
    "UnsupportedFormatError",
    "WorkbookError",
    "open_workbook",
]
