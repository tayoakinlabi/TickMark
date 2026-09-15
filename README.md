# Tickmark

Point it at a folder of Excel files. Get a report of every inconsistent formula,
hardcoded constant, broken link and silent error — before they cost you.

Tickmark runs entirely on your machine. No model, no network, no API key, no
account. It is the only tool of its kind that is free and open source; the
commercial equivalents are enterprise-priced.

> **Status: pre-alpha.** The audit engine is finished — ten checks, the HTML
> report, batch mode and the CLI, under 354 passing tests — and it now builds to
> a single Windows executable and an installer. Both `.xlsx` and legacy `.xls`
> are read. No local-server GUI yet, so the command line is the only way in.

---

## What it checks

1. **Inconsistent formula in a range** — a column of formulas where one cell was
   overwritten with a constant or a differently-shaped formula. This is where
   money actually goes missing, and it is the reason this tool exists.
2. **Hardcoded constants inside formulas** — `=B2*1.075` where the rate should be
   a named cell. Literals that carry no business meaning are ignored — `*100`,
   `/12`, `365` — and so are positions where a bare number is simply how the
   formula is written, like the column index in `VLOOKUP` or the digits in
   `ROUND`. Whole numbers are skipped entirely unless you ask for them, because
   the finding that pays for itself is the non-integer rate.
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
10. **Stored values that contradict their formula** — the number saved in a cell
    is not what its formula computes. Usually the workbook was edited with
    calculation switched off, so the figures on screen are stale and the file
    was sent anyway. See [What gets recomputed](#what-gets-recomputed) — this
    check is deliberately narrow, and it reports how much it could vouch for.

Output is a single self-contained HTML report you can email, plus a workbook
inventory (sheets, used ranges, hidden sheets/rows/columns, defined names, macro
presence). Batch mode audits a folder and adds a summary index.

## What it will not do

These are deliberate, permanent limits — not a roadmap.

- **It will not become a spreadsheet engine.** It recomputes simple arithmetic
  and nine named functions, and refuses everything else by name rather than
  guessing — see [What gets recomputed](#what-gets-recomputed). Implementing
  Excel's 500-odd functions, their coercion rules and their error propagation is
  an unbounded commitment, and declining it is what keeps Tickmark finishable.
- **It never writes to your workbooks.** Reports only. No auto-fixing. Writing to
  someone's financial workbook is a support burden with no upside.
- **No VBA analysis** — macro *presence* is detected and reported, never parsed.
- **No Google Sheets, Numbers, or `.xlsb`.**
- **No expression language** for transformations.

## What gets recomputed

Most auditors either compute nothing or claim to compute everything. Tickmark
does a third thing: it recomputes a **small closed subset** and tells you exactly
how much of your workbook that covered.

The subset is the arithmetic operators (`+ - * / ^ %`) plus `SUM`, `AVERAGE`,
`MIN`, `MAX`, `COUNT`, `COUNTA`, `ROUND`, `ROUNDUP`, `ROUNDDOWN` and `ABS`, over
cells holding numbers. A formula that reaches outside it — a `VLOOKUP`, an `IF`,
a date, a cell holding text — is **refused, not approximated**, and counted as
unverified.

Every report therefore carries a line like:

```
verified 847 of 1,203 formulas arithmetically (356 outside the evaluated subset)
```

That number is the point. A tool that silently checks two thirds of a workbook
lets you believe it checked all of it, and the 356 it skipped are exactly where a
reader would otherwise assume "no findings" meant "correct". **An unverified
formula has not been checked. It is not a formula that passed.**

The subset stays closed on purpose. Growing it function by function is how a
project like this becomes a spreadsheet engine that is wrong in ways nobody can
predict — and a tool that reports a discrepancy on a *correct* workbook loses
trust faster than one that quietly misses things.

Switch it off with `--no-evaluate` if you only want the structural checks.

## Speed

Measured, not promised. A generated workbook of **50 sheets and 100,700
formulas** (1.3 MB) on a mid-range Windows laptop:

| Stage | Seconds |
|---|---|
| Open workbook | 4.3 |
| All ten checks | 41.2 |
| Render report | <0.1 |
| **Total** | **45.4** |

Peak memory 277 MB. Time scales linearly with formula count, so a 10,000-formula
workbook takes about four and a half seconds. Reproduce with
`uv run python benchmarks/audit_bench.py --sheets 50 --rows 670`.

Recomputation (check 10) accounts for about 6 of those seconds. Measured on the
same workbook in the same process, switching it off takes the audit from 15.0s
to 12.9s warm — so `--no-evaluate` is worth having on a very large batch, and
not worth thinking about otherwise.

## Use

```
tickmark accounts.xlsx                 # audit one workbook, write accounts.tickmark.html
tickmark C:\Shared\Finance -r          # audit a folder and its subfolders, plus a summary index
tickmark accounts.xlsx --no-report     # print findings only
tickmark accounts.xlsx -o audit.html   # choose where the report goes
tickmark C:\Shared\Finance -o reports  # or point -o at a folder for all of them
tickmark accounts.xlsx --include-integers  # also flag whole-number constants
tickmark C:\Shared\Finance -r -q       # totals only, no per-finding output
```

`--version` prints the version. Auditing a folder of more than one workbook also
writes `tickmark-summary.html` next to the reports.

Tickmark reads `.xlsx`, `.xlsm`, `.xltx`, `.xltm` and the legacy binary `.xls`
(and `.xlt`). Excel's `~$` lock files for open workbooks are skipped rather than
reported as failures.

**Legacy `.xls` files are read natively** — no conversion step, no Excel
required. The two formats are held to a parity test: the same workbook saved in
both must produce identical findings. Two things are weaker on the old format:
external links are reported but not resolved to a file on disk, and a `.xls`
that is really an HTML or CSV export wearing the extension is refused with a
message saying so rather than a parse error.

Exit codes suit a scheduled job or a pre-commit hook: `0` nothing at or above the
threshold, `1` findings at or above it (`--fail-on high|medium|low|info|never`,
default `high`), `2` nothing could be audited.

A file that cannot be read is reported and skipped. Auditing 199 of 200 workbooks
and naming the one that failed beats auditing none of them. The same holds one
level down: if a check raises on one exotic sheet it is skipped for that sheet
alone — the other nine checks still run on it, and that check still runs
everywhere else.

## Install

Three ways in, depending on whether you have Python and whether you want
Tickmark on your PATH.

### 1. The Windows installer — no Python needed

Download `tickmark-<version>-setup.exe` from the
[releases page](../../releases) and run it.

It installs for the current user by default, so it does **not** ask for
administrator rights — a command-line tool that only ever reads files has no
business being elevated, and an unsigned installer demanding admin is exactly
the shape of thing you should refuse. Tick "Add Tickmark to PATH" during setup
and `tickmark` works in any terminal:

```
tickmark C:\Shared\Finance -r
```

Uninstall through Settings › Apps like anything else. There is no auto-updater
and no telemetry: the product claim is that nothing leaves your machine, and an
update check would quietly make that false.

### 2. The single executable — no installer, no Python

Download `tickmark.exe` from the same page. It is self-contained (~9 MB); put it
anywhere and run it. Nothing is written to the registry and nothing is installed.

**Verify it before you run it.** Each release publishes `tickmark.exe.sha256`:

```powershell
Get-FileHash .\tickmark.exe -Algorithm SHA256
```

Compare that against the published value — they should match exactly.

### 3. From source, or as a Python tool

With [uv](https://docs.astral.sh/uv/) (installs it on your PATH, isolated):

```
uv tool install git+https://github.com/tayoakinlabi/tickmark
```

or with pipx:

```
pipx install git+https://github.com/tayoakinlabi/tickmark
```

To work on it:

```
git clone https://github.com/tayoakinlabi/tickmark
cd tickmark
uv sync
uv run pytest
uv run tickmark path/to/workbook.xlsx
```

Requires Python 3.12 or newer. Three runtime dependencies, all small and all
doing one job: `openpyxl` reads OOXML and supplies the formula tokenizer, `xlrd`
decodes legacy BIFF, and `olefile` opens the OLE container a `.xls` lives in.
`pytest`, `ruff` and `xlwt` are the dev group.

### Building the release artefacts yourself

```
uv run --with pyinstaller python packaging/build.py
```

That produces `dist/tickmark.exe`, its `.sha256`, and — if
[Inno Setup 6](https://jrsoftware.org/isdl.php) is installed — the installer. The
build smoke-tests the frozen binary against a real workbook before packaging it,
because a PyInstaller build can succeed and still fail at startup on a missing
import, and finding that out after release is expensive.

### A note on the SmartScreen warning

Released builds are unsigned to begin with. Windows SmartScreen will warn you
that the publisher is unknown; **that warning is accurate**, and you should treat
any unsigned installer with the caution it deserves. Verify the SHA-256 checksum
published with each release before running it. Signing is planned once a public
release history exists — that history is a prerequisite for the free
open-source certificate programme, which is why it comes in this order rather
than the other way round.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
