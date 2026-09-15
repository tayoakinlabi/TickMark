# Committed fixtures

Every other fixture in this suite is built at test time by `conftest.py`, so its
contents are readable in the diff. These two are the exception, and the reason is
specific: **only Excel itself can write them.**

- `excel_authored.xls` — a BIFF8 workbook whose filled-down column is stored as a
  *shared formula*. Excel writes the tokens once and leaves the other eighteen
  cells pointing at that one record. `xlwt`, which builds the rest of the legacy
  fixtures, always writes each formula out in full, so it cannot reproduce the
  single most important case the BIFF backend has to handle — nor the cached
  values that check 35 compares against.
- `excel_authored.xlsx` — the same workbook, saved by the same Excel session in
  the modern format.

The pair exists to support one test that nothing else can make: **parity.**
Identical content in the two formats must produce identical findings. That test
is the real guarantee behind reading `.xls` at all, because a second backend that
quietly disagrees with the first is worse than no second backend.

`make_excel_authored.py` is what generated them. It needs Excel installed and is
not run by the suite:

```
uv run --with pywin32 python tests/fixtures/make_excel_authored.py <output-dir> .xls
uv run --with pywin32 python tests/fixtures/make_excel_authored.py <output-dir> .xlsx
```

The workbook it builds carries, deliberately: a filled column with a constant
typed over one cell (check 1), a `SUM` that stops one row short (check 33), a
`#REF!` left by a deleted column and a `=1/0` (check 3), a volatile `TODAY`
(check 6), a nested `IF` (check 7), a hidden sheet, and a cross-sheet reference.
