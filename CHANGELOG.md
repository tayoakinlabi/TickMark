# Changelog

Notable changes to Tickmark. Versions follow [semantic versioning](https://semver.org);
while the major version is `0`, the command-line interface may still change between
minor releases.

## 0.1.0 — first release

The first public build. Tickmark audits Excel workbooks for the mistakes that
survive review because the sheet still looks right, and writes a single
self-contained HTML report you can email.

It runs entirely on your machine: no model, no network, no API key, no account.

### Checks

1. **Inconsistent formula in a range** — a column of formulas where one cell was
   overwritten with a constant or a differently-shaped formula. The reason this
   tool exists.
2. **Hardcoded constants inside formulas** — `=B2*1.075` where the rate should be
   a named cell.
3. **Broken references** — `#REF!`, `#NAME?`, `#VALUE!` and the rest.
4. **External links**, with whether the target still resolves.
5. **Circular references.**
6. **Volatile functions** — `NOW`, `TODAY`, `RAND`, `OFFSET`, `INDIRECT`, `CELL`,
   `INFO`.
7. **Formula complexity ranking.**
8. **Ranges that stop short of their data** — `=SUM(B2:B12)` when the column runs
   to B13.
9. **Double counting** — a total whose range already contains a subtotal of the
   same rows.
10. **Stored values that contradict their formula** — usually a workbook edited
    with calculation switched off, so the figures on screen are stale.

### Formats

Reads `.xlsx`, `.xlsm`, `.xltx`, `.xltm`, and the legacy binary `.xls` (and
`.xlt`) — natively, with no conversion step and no Excel installation required.
The two paths are held to a parity test: the same workbook saved in either
format must produce identical findings.

### What it recomputes, and what it admits it cannot

Check 10 recomputes a deliberately **small closed subset** — the arithmetic
operators plus `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, `COUNTA`, `ROUND`,
`ROUNDUP`, `ROUNDDOWN` and `ABS`, over numeric cells. Anything else is refused
rather than approximated.

Every audit therefore reports its own coverage:

```
verified 847 of 1,203 formulas arithmetically (356 outside the evaluated subset)
```

**An unverified formula has not been checked; it is not a formula that passed.**
A tool that silently checks two thirds of a workbook lets you believe it checked
all of it.

### Output

A single self-contained HTML report, plus a workbook inventory (sheets, used
ranges, hidden sheets/rows/columns, defined names, macro presence). Batch mode
audits a folder and adds a summary index. Exit codes suit a scheduled job or a
pre-commit hook.

### Limits worth knowing before you start

- **It never writes to your workbooks.** Reports only, no auto-fixing.
- **It is not a spreadsheet engine** and will not become one — see the coverage
  note above.
- **VBA is detected, never parsed.** Macro presence is reported.
- **No Google Sheets, Numbers, or `.xlsb`.**
- **External links in `.xls` files are reported but not resolved** to a file on
  disk. They are resolved in `.xlsx`.
- **No graphical interface yet.** The command line is the only way in.

### Installing

Unsigned. Windows SmartScreen will say the publisher is unknown, and that
warning is accurate — verify the published SHA-256 before running anything.
Code signing is planned once a public release history exists, which this release
begins. See the README for the three installation routes.

### Performance

50 sheets and 100,700 formulas — 1.3 MB on disk — audited in about 45 seconds on
a mid-range Windows laptop, peak memory 277 MB. Time scales linearly with formula
count. Recomputation accounts for roughly 6 of those seconds and can be switched
off with `--no-evaluate`.
