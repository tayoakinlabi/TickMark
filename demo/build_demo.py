"""Build the sample workbook and render the report published at docs/.

    uv run python demo/build_demo.py

Needs nothing but the project itself — no Excel — so the sample can be
regenerated on any machine and in CI, and cannot drift from what the current
build actually reports.

**The workbook is deliberately, plausibly broken.** Every fault in it is one
somebody really made: a rate typed over a filled formula, a total that stopped
growing when rows were added, a subtotal counted twice, a link to a file that
moved, a figure left stale after calculation was switched off. None of them look
wrong on the sheet, which is the entire point of the product and the reason a
sample report is worth more than any description of one.

Cached values are written by rewriting the sheet XML after openpyxl has saved,
because openpyxl stores a formula or a value and never both. Without them the
sample would show "this workbook stores no calculated values to compare against"
— turning the check that best distinguishes Tickmark into a blank.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from tickmark.checks.registry import run_audit
from tickmark.report.html_report import render_report
from tickmark.workbook.inventory import take_inventory
from tickmark.workbook.loader import open_workbook

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
DOCS = ROOT / "docs"

WORKBOOK = DEMO / "quarterly-accounts.xlsx"
REPORT = DOCS / "sample-report.html"

VAT = 0.2
NET = [1450, 2300, 875, 4100, 1290, 3675, 950, 2480, 1130, 5200, 780, 3340, 1615, 2090, 940]


def build_workbook(path: Path) -> None:
    book = Workbook()

    invoices = book.active
    invoices.title = "Q3 Invoices"
    invoices.append(["Client", "Net", "VAT", "Gross"])
    for offset, net in enumerate(NET):
        row = offset + 2
        invoices[f"A{row}"] = f"Client {offset + 1:02d}"
        invoices[f"B{row}"] = net
        invoices[f"C{row}"] = f"=B{row}*0.2"
        invoices[f"D{row}"] = f"=B{row}+C{row}"

    last = len(NET) + 1

    # 1. Somebody typed the VAT in by hand on one row. The column still looks
    #    like a column of formulas.
    invoices["C9"] = 246.00

    # 2. Two rows were added later and the total never grew to meet them.
    total = last + 2
    invoices[f"A{total}"] = "Total"
    invoices[f"B{total}"] = f"=SUM(B2:B{last - 2})"
    invoices[f"C{total}"] = f"=SUM(C2:C{last})"
    invoices[f"D{total}"] = f"=SUM(D2:D{last})"

    summary = book.create_sheet("Summary")
    summary["A1"] = "Quarterly summary"
    summary["A2"] = "Prepared"
    summary["B2"] = "=TODAY()"  # 3. volatile: changes every time the file opens

    summary["A4"] = "Net"
    summary["B4"] = f"='Q3 Invoices'!B{total}"
    summary["A5"] = "VAT"
    summary["B5"] = f"='Q3 Invoices'!C{total}"
    summary["A6"] = "Gross"
    summary["B6"] = "=B4+B5"

    # 4. Double counting. The grand total sweeps the whole block, and the block
    #    already contains a subtotal of part of it — so those rows are added
    #    twice. Every formula in sight is correct on its own.
    summary["A8"] = "By region"
    summary["A9"] = "North"
    summary["B9"] = 12400
    summary["A10"] = "South"
    summary["B10"] = 15850
    summary["A11"] = "North + South"
    summary["B11"] = "=SUM(B9:B10)"
    summary["A12"] = "Export"
    summary["B12"] = 9300
    summary["A13"] = "All regions"
    summary["B13"] = "=SUM(B9:B12)"

    summary["A15"] = "Commission"
    # 5. The formula nobody wants to inherit: five levels deep, three rates
    #    buried in it, and a cap bolted on the end.
    summary["B15"] = (
        "=IF(B4>75000,MIN(B4*0.075,12000),"
        "IF(B4>50000,MIN(B4*0.06,9000),"
        "IF(B4>25000,MIN(B4*0.045,6000),"
        "IF(B4>10000,MIN(B4*0.035,3000),"
        "IF(B4>0,B4*0.02,0)))))"
    )

    summary["A17"] = "Last year"
    # 6. A link to a workbook that is not where it used to be.
    summary["B17"] = "='[FY2024 Accounts.xlsx]Summary'!B6"

    summary["A19"] = "Variance"
    # 7. A reference left behind by a deleted column.
    summary["B19"] = "=B6-#REF!"

    rates = book.create_sheet("Rates")
    rates["A1"] = "VAT"
    rates["B1"] = VAT
    rates["A2"] = "Commission (standard)"
    rates["B2"] = 0.05
    # 8. Hidden, and everything above depends on it.
    rates.sheet_state = "hidden"

    book.defined_names.add(DefinedName("VatRate", attr_text="Rates!$B$1"))

    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def _cached(path: Path, values: dict[str, str]) -> None:
    """Write cached results beside the formulas, one of them deliberately stale.

    openpyxl writes a formula or a value, never both, so the file it saves looks
    to any reader like a workbook that has never been calculated. Real ones carry
    the last computed result, and check 10 compares against exactly that.

    **The existing empty value element is replaced, not appended to.** openpyxl
    emits ``<c r="C2"><f>B2*0.2</f><v /></c>``, and inserting another one gave a
    cell two ``<v>`` elements where at most one is allowed. openpyxl and Tickmark
    both read that without complaint — they take the first — so the sample passed
    every check this project has while being a file **Excel refuses to open at
    all**. Validating a workbook with the library that wrote it proves nothing;
    :func:`assert_one_value_per_cell` is the guard that does.
    """
    original = path.with_suffix(".tmp.xlsx")
    shutil.move(path, original)

    with (
        zipfile.ZipFile(original) as source,
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.endswith(".xml"):
                text = data.decode("utf-8")
                for formula, value in values.items():
                    # Swallows whatever value element already follows the
                    # formula, empty or not, so exactly one survives.
                    pattern = re.compile(
                        re.escape(f"<f>{formula}</f>") + r"\s*(?:<v\s*/>|<v>[^<]*</v>)?"
                    )
                    text = pattern.sub(lambda _m, f=formula, v=value: f"<f>{f}</f><v>{v}</v>", text)
                data = text.encode("utf-8")
            target.writestr(item, data)

    original.unlink()
    assert_one_value_per_cell(path)


def assert_one_value_per_cell(path: Path) -> None:
    """Refuse to ship a workbook Excel would reject.

    A cell may hold at most one value element. Two is invalid, and every reader
    this project uses accepts it anyway — which is precisely why the check has to
    be explicit rather than implied by "the tests pass". Costs milliseconds,
    needs no Excel installed, and catches the one way this script can produce a
    file that opens as blank.
    """
    offenders: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/"):
                continue
            text = archive.read(name).decode("utf-8")
            for cell in re.findall(r"<c\b[^>]*>.*?</c>", text):
                if len(re.findall(r"<v[\s>/]", cell)) > 1:
                    offenders.append(f"{name}: {cell[:90]}")
    if offenders:
        raise SystemExit(
            "the sample workbook has cells with more than one value element, "
            "which Excel will refuse to open:\n  " + "\n  ".join(offenders[:5])
        )


def cached_values() -> dict[str, str]:
    """What Excel would have stored, except for the one that went stale."""
    values: dict[str, str] = {}
    vat_total = 0.0
    net_total = 0.0
    gross_total = 0.0

    for offset, net in enumerate(NET):
        row = offset + 2
        vat = 246.00 if row == 9 else net * VAT
        values[f"B{row}*0.2"] = f"{net * VAT:g}"
        values[f"B{row}+C{row}"] = f"{net + vat:g}"
        net_total += net
        vat_total += vat
        gross_total += net + vat

    last = len(NET) + 1
    # The total that stopped short really does hold the smaller figure.
    short = sum(NET[:-2])
    values[f"SUM(B2:B{last - 2})"] = f"{short:g}"
    values[f"SUM(C2:C{last})"] = f"{vat_total:g}"
    values[f"SUM(D2:D{last})"] = f"{gross_total:g}"

    total_row = last + 2
    values[f"'Q3 Invoices'!B{total_row}"] = f"{short:g}"
    values[f"'Q3 Invoices'!C{total_row}"] = f"{vat_total:g}"

    # 9. The stale one. Calculation was set to manual, a figure changed, and
    #    this was never recalculated — so the number on the sheet is not what
    #    its own formula produces.
    values["B4+B5"] = f"{short + vat_total - 1840:g}"

    values["SUM(B9:B10)"] = "28250"
    # 65800, not 37550. Excel computes what is written, and what is written adds
    # the subtotal in B11 on top of the rows it already summarises — so the
    # inflated figure *is* the stored one. Writing the figure a careful person
    # would expect would have produced a stale-value finding on top, which is
    # not what is wrong here and would have muddled the point.
    values["SUM(B9:B12)"] = "65800"
    return values


def main() -> int:
    build_workbook(WORKBOOK)
    _cached(WORKBOOK, cached_values())
    print(f"built {WORKBOOK.relative_to(ROOT)}")

    with open_workbook(WORKBOOK) as book:
        inventory = take_inventory(book)
        result = run_audit(book)
        html = render_report(inventory, result.findings, coverage=result.coverage)

    DOCS.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(html, encoding="utf-8")
    # Published alongside the report so a reader can open the broken workbook
    # themselves and see that none of it looks wrong on the sheet.
    shutil.copy(WORKBOOK, DOCS / WORKBOOK.name)

    counts: dict[str, int] = {}
    for finding in result.findings:
        counts[finding.severity.value] = counts.get(finding.severity.value, 0) + 1

    print(f"wrote  {REPORT.relative_to(ROOT)}")
    print(f"       {len(result.findings)} findings {counts}")
    print(
        f"       coverage: recomputed {result.coverage.compared} of {result.coverage.total}"
        f" ({result.coverage.total - result.coverage.verified} outside the subset)"
    )
    for finding in result.findings:
        print(f"         {finding.severity.value:6} {finding.location:22} {finding.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
