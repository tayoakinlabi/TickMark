# Tickmark

Point it at a folder of Excel files. Get a report of every inconsistent formula,
hardcoded constant, broken link and silent error — before they cost you.

Tickmark runs entirely on your machine. No model, no network, no API key, no
account. It is the only tool of its kind that is free and open source; the
commercial equivalents are enterprise-priced.

> **Status: pre-alpha.** All nine checks work and the HTML report is usable. Not
> yet packaged as a Windows installer, and not yet measured against a large real
> workbook.

---

## What it checks

1. **Inconsistent formula in a range** — a column of formulas where one cell was
   overwritten with a constant or a differently-shaped formula. This is where
   money actually goes missing, and it is the reason this tool exists.
2. **Hardcoded constants inside formulas** — `=B2*1.075` where the rate should be
   a named cell. Innocuous literals (`*100`, `+1`, `/12`) are ignored by default
   and the ignore list is configurable.
3. **Broken references** — `#REF!`, `#NAME?`, `#VALUE!` and the rest, with the
   location and the formula that produced them.
4. **External links** — references to other workbooks, flagged with whether the
   target still resolves.
5. **Circular references.**
6. **Volatile functions** — `NOW`, `TODAY`, `RAND`, `OFFSET`, `INDIRECT`, `CELL`,
   `INFO`. They force full recalculation and make large files feel broken.
7. **Formula complexity ranking** — the longest and most deeply nested formulas,
   as a "this will hurt to maintain" list.
8. **Ranges that stop short of their data** — `=SUM(B2:B12)` when the column runs
   to B13. Rows were added and the total was never extended.
9. **Double counting** — a total whose range already contains a subtotal of the
   same rows, so they are added twice.

Output is a single self-contained HTML report you can email, plus a workbook
inventory (sheets, used ranges, hidden sheets/rows/columns, defined names, macro
presence). Batch mode audits a folder and adds a summary index.

## What it will not do

These are deliberate, permanent limits — not a roadmap.

- **It never evaluates formulas.** It parses them. Evaluating Excel semantics
  means implementing hundreds of functions and their coercion and error rules,
  which is an unbounded commitment. This single limit is what keeps Tickmark
  finishable.
- **It never writes to your workbooks.** Reports only. No auto-fixing. Writing to
  someone's financial workbook is a support burden with no upside.
- **No `.xls`** (the legacy binary format needs an entirely separate parser).
- **No VBA analysis** — macro *presence* is detected and reported, never parsed.
- **No Google Sheets or Numbers.**
- **No expression language** for transformations.

## Use

```
tickmark accounts.xlsx                 # audit one workbook, write accounts.tickmark.html
tickmark C:\Shared\Finance -r          # audit a folder and its subfolders, plus a summary index
tickmark accounts.xlsx --no-report     # print findings only
tickmark accounts.xlsx -o audit.html   # choose where the report goes
```

Exit codes suit a scheduled job or a pre-commit hook: `0` nothing at or above the
threshold, `1` findings at or above it (`--fail-on high|medium|low|info|never`,
default `high`), `2` nothing could be audited.

A file that cannot be read is reported and skipped. Auditing 199 of 200 workbooks
and naming the one that failed beats auditing none of them.

## Install

Not yet packaged. To run from source:

```
git clone <repo-url>
cd tickmark
uv sync
uv run pytest
uv run tickmark path/to/workbook.xlsx
```

Requires Python 3.12 or newer. The only dependency is `openpyxl`.

### A note on the SmartScreen warning

Released builds are unsigned to begin with. Windows SmartScreen will warn you
that the publisher is unknown; that warning is accurate, and you should treat any
unsigned installer with the caution it deserves. Verify the SHA-256 checksum
published with each release before running it. Signing is planned once a release
history exists.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
