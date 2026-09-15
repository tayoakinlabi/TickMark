import os
import sys

import win32com.client as win32

sp = sys.argv[1]
ext = sys.argv[2] if len(sys.argv) > 2 else ".xls"
out = os.path.abspath(os.path.join(sp, "real_excel" + ext)).replace("/", "\\")
if os.path.exists(out):
    os.remove(out)

xl = win32.gencache.EnsureDispatch("Excel.Application")
xl.Visible = False
xl.DisplayAlerts = False
try:
    wb = xl.Workbooks.Add()
    ws = wb.Worksheets(1)
    ws.Name = "Invoices"
    ws.Range("A1").Value = "Item"
    ws.Range("B1").Value = "Net"
    ws.Range("C1").Value = "VAT"
    for r in range(2, 22):
        ws.Cells(r, 1).Value = f"Item {r - 1}"
        ws.Cells(r, 2).Value = r * 10
    # Fill down -> Excel writes this as a shared formula
    ws.Range("C2").Formula = "=B2*1.075"
    ws.Range("C2").AutoFill(ws.Range("C2:C21"))
    # The classic bug: a constant typed over one formula in the run
    ws.Range("C11").Value = 999
    # A total that stops short of its data
    ws.Range("B23").Formula = "=SUM(B2:B20)"
    ws.Range("C23").Formula = "=SUM(C2:C21)"
    # A volatile function and a deep nest
    ws.Range("E1").Formula = "=TODAY()"
    ws.Range("E2").Formula = "=IF(B2>100,IF(B3>50,ROUND(B2*1.2,2),B3),0)"

    ws2 = wb.Worksheets.Add()
    ws2.Name = "Hidden Rates"
    ws2.Range("A1").Value = "rate"
    ws2.Range("B1").Value = 0.075
    ws2.Range("B2").Formula = "=Invoices!C2+B1"
    ws2.Visible = 0  # xlSheetHidden

    # A broken reference: build it, then delete the column it points at
    ws3 = wb.Worksheets.Add()
    ws3.Name = "Broken"
    ws3.Range("A1").Value = 5
    ws3.Range("B1").Value = 6
    ws3.Range("C1").Formula = "=A1+B1"
    ws3.Range("D1").Formula = "=1/0"
    ws3.Columns("A").Delete()

    wb.SaveAs(out, FileFormat=(51 if ext == ".xlsx" else 56))
    wb.Close(SaveChanges=False)
    print("wrote", out, os.path.getsize(out), "bytes")
finally:
    xl.Quit()
