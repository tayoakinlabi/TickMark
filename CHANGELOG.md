# Changelog

Notable changes to Tickmark. Versions follow [semantic versioning](https://semver.org);
while the major version is `0`, the command-line interface may still change between
minor releases.

## 0.2.0

A browser interface, house rules you can write yourself, and a fix for something
0.1.0 got quietly wrong on legacy files.

### External links are found in `.xls` files

**0.1.0 reported none at all.** This was recorded as a known limitation in
weaker terms — "reported but not resolved" — and that description was wrong. A
reference into another workbook was not recognised as external in the first
place, so check 4 stayed silent on every `.xls`. A link to a workbook that has
since moved is exactly the defect a twenty-year-old file carries, and it was
invisible in the format most likely to carry it.

If you have audited `.xls` files with 0.1.0, they were not checked for external
links. Worth re-running them.

One difference between the formats is worth knowing, and it is Excel's doing
rather than Tickmark's: a `.xls` stores the link as a path *relative* to the
workbook holding it, a `.xlsx` stores an absolute one. So the same workbook saved
both ways and then moved to another machine can report the link as fine from one
and as pointing at a missing file from the other. Tickmark reports what the file
stores; what the file stores differs.

### A browser interface

```
tickmark --serve
```

Opens a page at `127.0.0.1` on a port chosen at startup. Paste a path, read the
findings, open the full report. The same engine as the command line — a workbook
audited in the browser and in the terminal cannot disagree.

It is not reachable by anything but you, which is a stronger claim than "it only
listens on localhost": any site you visit can make requests to `127.0.0.1`, and
through DNS rebinding can do so believing it is same-origin. So there is a fresh
token every launch, carried once in the link Tickmark opens and then dropped from
the address bar; a `Host` allowlist, which is what actually stops rebinding; an
`Origin` allowlist; and a content-security policy that forbids every external
origin. Nothing is uploaded, nothing is written to your workbooks, and closing
the window stops the server.

### House rules in `tickmark.toml`

Settings and your own checks, found beside the workbook or in any folder above it
— so one file at the top of a shared drive covers everything under it.

```toml
[rules]
ignored_numbers = [0, 1, 2, 12, 100]

[[rule]]
name = "no-hardcoded-vat"
severity = "high"
pattern = '\*\s*1\.075'
summary = "Hardcoded VAT rate — use the Rates sheet"
```

A rule is a regular expression plus the wording the report should use. Rules can
match a cell's value as well as its formula, so "flag anything still saying TODO"
is one line.

A mistake in this file costs you that line and nothing else: a rule with no
pattern, an unknown severity, or a regex that does not compile is reported by
name and skipped, and the audit runs anyway. Only a file that is not valid TOML
stops the run.

### Also

- `--config` and `--no-config` for pointing at a specific settings file or
  ignoring one. Command-line flags always win over the file.
- Reporting a problem now has somewhere to go — see the issue templates. The
  most valuable reports are the ones where a finding is wrong.

### Known limits, unchanged

Still no `.xlsb`, Google Sheets or Numbers; VBA is detected but never parsed;
Tickmark never writes to an audited workbook; and most arithmetic is still not
recomputed. Read the coverage line before treating a clean report as a clean
workbook — an unverified formula has not been checked, it is not a formula that
passed.

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
