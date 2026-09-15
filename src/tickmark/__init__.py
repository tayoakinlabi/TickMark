"""Tickmark — spreadsheet auditor.

Point it at a folder of Excel files; get a report of every inconsistent formula,
hardcoded constant, broken link and silent error.

Two hard rules hold across the whole package:

1. **Read-only against source files.** Tickmark never writes to an audited
   workbook. Reports are written where the user asks, and nowhere else.
2. **Parse first; evaluate only a closed subset.** Formulas are read as
   structure, not computed, with one bounded exception:
   :mod:`tickmark.formula.evaluate` recomputes simple arithmetic and nine named
   functions so that check 35 can spot a stored value contradicting its own
   formula. Everything outside that subset is refused by name and counted as
   unverified rather than approximated. The subset staying *closed* is what
   keeps the project finishable — see that module, and 04-tickmark.md section
   10, for why growing it function by function is the thing to refuse.
"""

__version__ = "0.1.0"
