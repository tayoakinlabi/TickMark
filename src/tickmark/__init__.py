"""Tickmark — spreadsheet auditor.

Point it at a folder of Excel files; get a report of every inconsistent formula,
hardcoded constant, broken link and silent error.

Two hard rules hold across the whole package:

1. **Read-only against source files.** Tickmark never writes to an audited
   workbook. Combine mode writes only to a new output file.
2. **Parse, never evaluate.** See ``tickmark.formula`` for why this is what keeps
   the project finishable.
"""

__version__ = "0.1.0"
