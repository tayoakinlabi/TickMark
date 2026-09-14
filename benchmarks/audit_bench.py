"""Measure an audit against a realistic workbook (NF 6).

The non-functional requirement asks for a *measured* figure in the README rather
than a promise, on something like 50 sheets and 100k formulas. This produces that
figure, and breaks it down per check so a slow one is visible rather than hidden
inside a total.

Generation is the slow part and is deliberately excluded from the timings — users
open workbooks that already exist.

Run:  uv run python benchmarks/audit_bench.py [--sheets 50] [--rows 400]
"""

from __future__ import annotations

import argparse
import time
import tracemalloc
from pathlib import Path

from openpyxl import Workbook

from tickmark.checks.registry import build_sheet_checks, build_workbook_checks, run_checks
from tickmark.report.html_report import render_report
from tickmark.workbook.inventory import take_inventory
from tickmark.workbook.loader import open_workbook


def generate(path: Path, sheets: int, rows: int) -> int:
    """Build a workbook shaped like a real model, and return its formula count.

    Each sheet is a plausible table — labels, inputs, three formula columns and a
    total — rather than one formula repeated, so the checks do real work instead
    of hitting the same cached shape every time.
    """
    wb = Workbook()
    wb.remove(wb.active)
    formulas = 0

    for sheet_index in range(sheets):
        ws = wb.create_sheet(f"Dept{sheet_index + 1:02d}")
        ws.append(["Client", "Net", "VAT", "Gross", "Margin"])
        for row in range(2, rows + 2):
            ws[f"A{row}"] = f"Client {row - 1}"
            ws[f"B{row}"] = 1000 + row
            ws[f"C{row}"] = f"=B{row}*0.2"
            ws[f"D{row}"] = f"=B{row}+C{row}"
            ws[f"E{row}"] = f"=IF(D{row}>2000,D{row}*0.15,D{row}*0.1)"
            formulas += 3
        total = rows + 2
        for column in "BCDE":
            ws[f"{column}{total}"] = f"=SUM({column}2:{column}{rows + 1})"
            formulas += 1

    wb.save(path)
    return formulas


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheets", type=int, default=50)
    parser.add_argument("--rows", type=int, default=400)
    parser.add_argument("--out", type=Path, default=Path("bench.xlsx"))
    args = parser.parse_args()

    print(f"generating {args.sheets} sheets x {args.rows} rows ...", flush=True)
    started = time.perf_counter()
    formulas = generate(args.out, args.sheets, args.rows)
    size_mb = args.out.stat().st_size / 1e6
    print(
        f"  {formulas:,} formulas, {size_mb:.1f} MB on disk, "
        f"generated in {time.perf_counter() - started:.1f}s\n"
    )

    tracemalloc.start()

    started = time.perf_counter()
    workbook = open_workbook(args.out)
    load = time.perf_counter() - started

    timings: list[tuple[str, float]] = []

    for check in build_sheet_checks():
        started = time.perf_counter()
        for sheet in workbook.sheets:
            check.run(sheet)
        timings.append((check.name, time.perf_counter() - started))

    for workbook_check in build_workbook_checks():
        started = time.perf_counter()
        workbook_check.run_workbook(workbook)
        timings.append((workbook_check.name, time.perf_counter() - started))

    started = time.perf_counter()
    inventory = take_inventory(workbook)
    findings = run_checks(workbook)
    full = time.perf_counter() - started

    started = time.perf_counter()
    html = render_report(inventory, findings)
    render = time.perf_counter() - started

    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    workbook.close()

    print(f"{'stage':<24} {'seconds':>9}")
    print("-" * 34)
    print(f"{'open workbook':<24} {load:>9.2f}")
    for name, seconds in sorted(timings, key=lambda t: -t[1]):
        print(f"{name:<24} {seconds:>9.2f}")
    print("-" * 34)
    print(f"{'full audit (all checks)':<24} {full:>9.2f}")
    print(f"{'render report':<24} {render:>9.2f}")
    print(f"{'TOTAL':<24} {load + full + render:>9.2f}")
    print(f"\npeak memory: {peak / 1e6:.0f} MB")
    print(f"findings: {len(findings):,}")
    print(f"report size: {len(html) / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
