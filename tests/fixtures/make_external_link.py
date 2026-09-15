"""Generate the external-link fixture set. Needs Excel; not run by the suite.

    uv run --with pywin32 python tests/fixtures/make_external_link.py <output-dir>

Produces three files:

* ``source_book.xls`` — the workbook that gets linked *to*
* ``external_link.xls`` and ``external_link.xlsx`` — two saves of one workbook
  that reads from it

Only Excel can write these. A reference into another workbook is not stored as
text in the formula; it is an index into a table of files (``SUPBOOK`` and
``EXTERNSHEET`` in BIFF), and ``xlwt`` has no notion of that table at all.

The link is deliberately written to a file in the *same directory*, because that
is what makes Excel store a relative path — which is in turn what lets a test
copy the pair into a scratch directory and have the link still resolve, or copy
only the linking workbook and have it correctly resolve to nothing.
"""

from __future__ import annotations

import os
import sys

import win32com.client as win32

_XL_EXCEL_8 = 56  # .xls, BIFF8
_XL_OPEN_XML_WORKBOOK = 51  # .xlsx


def _windows_path(directory: str, name: str) -> str:
    """Excel rejects forward slashes in SaveAs, whatever Python thinks of them."""
    return os.path.abspath(os.path.join(directory, name)).replace("/", "\\")


def main(directory: str) -> int:
    excel = win32.gencache.EnsureDispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        source = _windows_path(directory, "source_book.xls")
        if os.path.exists(source):
            os.remove(source)

        book = excel.Workbooks.Add()
        sheet = book.Worksheets(1)
        sheet.Name = "Rates"
        sheet.Range("A1").Value = 0.2
        sheet.Range("A2").Value = 100
        book.SaveAs(source, FileFormat=_XL_EXCEL_8)
        print(f"wrote {source}")

        link = f"'{os.path.dirname(source)}\\[source_book.xls]Rates'!"
        for suffix, file_format in ((".xls", _XL_EXCEL_8), (".xlsx", _XL_OPEN_XML_WORKBOOK)):
            target = _windows_path(directory, "external_link" + suffix)
            if os.path.exists(target):
                os.remove(target)

            linking = excel.Workbooks.Add()
            main_sheet = linking.Worksheets(1)
            main_sheet.Name = "Main"
            main_sheet.Range("A1").Value = 5
            main_sheet.Range("B1").Formula = f"={link}A1*A1"
            main_sheet.Range("B2").Formula = f"={link}A2+1"
            excel.Calculate()
            linking.SaveAs(target, FileFormat=file_format)
            linking.Close(SaveChanges=False)
            print(f"wrote {target}")

        book.Close(SaveChanges=False)
    finally:
        excel.Quit()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
