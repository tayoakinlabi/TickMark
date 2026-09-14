"""The self-contained HTML report (item 10).

This file is the product's actual deliverable. Someone runs Tickmark, gets one
HTML file, and emails it to the person who owns the spreadsheet — so it has to
open correctly with no network, no assets folder, and no fonts to download. All
CSS is inline, there is no JavaScript, and nothing is fetched.

Two consequences of that audience worth stating, because they drive the markup:

* **It gets printed.** Accountants print things. A ``@media print`` block keeps
  it readable on paper and stops findings breaking across pages.
* **It gets read by someone who did not run the audit.** So the report explains
  what Tickmark does *not* check, rather than letting a clean report imply the
  workbook is correct. A report that overstates its own coverage is worse than no
  report, because it launders a guess into an assurance.

Every value from the workbook is escaped on the way in. Formulas are full of
``<``, ``>`` and ``&``, and a report is untrusted content the moment it contains
someone else's file contents.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from html import escape

from tickmark import __version__
from tickmark.findings.model import Finding, Severity
from tickmark.workbook.inventory import Inventory

__all__ = ["render_report"]

_SEVERITY_LABEL = {
    Severity.HIGH: "Needs attention",
    Severity.MEDIUM: "Worth checking",
    Severity.LOW: "Minor",
    Severity.INFO: "For information",
}

_SEVERITY_BLURB = {
    Severity.HIGH: "Likely to be producing a wrong number right now.",
    Severity.MEDIUM: "Probably fine, but worth confirming.",
    Severity.LOW: "Not wrong, but untidy or slow.",
    Severity.INFO: "Notes about how the workbook is built.",
}

_CSS = """
:root {
  --ink: #1a1a1a; --muted: #5c5c5c; --rule: #e0ddd6; --paper: #fbfaf7;
  --high: #a8321e; --medium: #a8701e; --low: #5c6b3f; --info: #3f5c6b;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem;
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--ink); background: var(--paper);
}
.wrap { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .2rem; letter-spacing: -.01em; }
.sub { color: var(--muted); margin: 0 0 2rem; font-size: .9rem; }
h2 {
  font-size: 1.05rem; margin: 2.5rem 0 .25rem; padding-bottom: .35rem;
  border-bottom: 2px solid var(--rule); letter-spacing: -.01em;
}
h2 .count { color: var(--muted); font-weight: 400; }
.blurb { color: var(--muted); font-size: .85rem; margin: .4rem 0 1rem; }
.tiles { display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: 1rem; }
.tile {
  flex: 1 1 8rem; border: 1px solid var(--rule); border-radius: 6px;
  padding: .7rem .9rem; background: #fff;
}
.tile .n { font-size: 1.5rem; font-weight: 600; line-height: 1.1; }
.tile .l {
  color: var(--muted); font-size: .78rem;
  text-transform: uppercase; letter-spacing: .04em;
}
.tile.high .n { color: var(--high); } .tile.medium .n { color: var(--medium); }
.tile.low .n { color: var(--low); }  .tile.info .n { color: var(--info); }
.finding {
  border: 1px solid var(--rule); border-left: 4px solid var(--rule);
  border-radius: 5px; background: #fff; padding: .8rem 1rem; margin-bottom: .7rem;
  break-inside: avoid; page-break-inside: avoid;
}
.finding.high { border-left-color: var(--high); }
.finding.medium { border-left-color: var(--medium); }
.finding.low { border-left-color: var(--low); }
.finding.info { border-left-color: var(--info); }
.finding .head { display: flex; flex-wrap: wrap; gap: .6rem; align-items: baseline; }
.loc {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-weight: 600; font-size: .9rem;
}
.summary { font-weight: 600; }
.check {
  margin-left: auto; color: var(--muted); font-size: .72rem;
  text-transform: uppercase; letter-spacing: .05em;
}
pre {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  background: #f4f2ed; border: 1px solid var(--rule); border-radius: 4px;
  padding: .45rem .6rem; margin: .55rem 0 .4rem; font-size: .85rem;
  overflow-x: auto; white-space: pre-wrap; word-break: break-all;
}
.why { color: var(--muted); font-size: .88rem; margin: 0; }
table { border-collapse: collapse; width: 100%; font-size: .88rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--rule); }
th { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.note {
  border: 1px solid var(--rule); border-radius: 6px; background: #fff;
  padding: .9rem 1.1rem; font-size: .88rem; color: var(--muted);
}
.note strong { color: var(--ink); }
.clean { border: 1px solid var(--rule); border-radius: 6px; background: #fff; padding: 1.4rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .78rem; }
@media print {
  body { background: #fff; padding: 0; font-size: 11pt; }
  .tile, .finding, .note, table { break-inside: avoid; }
  h2 { break-after: avoid; }
  footer { position: fixed; bottom: 0; }
}
"""

# Stated plainly rather than buried, so a clean report is not mistaken for proof
# that the workbook is correct.
_NOT_CHECKED = [
    "Whether the numbers are <em>right</em> — Tickmark reads formulas, it never calculates them.",
    "Anything inside VBA macros. Their presence is reported; their contents are never parsed.",
    "Legacy <code>.xls</code> files and Google Sheets.",
    "Whether a formula matches what the business actually intended.",
]


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _tile(severity: Severity, count: int) -> str:
    return (
        f'<div class="tile {severity.value}"><div class="n">{count}</div>'
        f'<div class="l">{_e(_SEVERITY_LABEL[severity])}</div></div>'
    )


def _finding_html(finding: Finding) -> str:
    parts = [
        f'<div class="finding {finding.severity.value}">',
        '<div class="head">',
        f'<span class="loc">{_e(finding.location)}</span>',
        f'<span class="summary">{_e(finding.summary)}</span>',
        f'<span class="check">{_e(finding.check)}</span>',
        "</div>",
    ]
    if finding.formula:
        parts.append(f"<pre>{_e(finding.formula)}</pre>")
    if finding.explanation:
        parts.append(f'<p class="why">{_e(finding.explanation)}</p>')
    parts.append("</div>")
    return "".join(parts)


def _inventory_html(inventory: Inventory) -> str:
    rows = []
    for sheet in inventory.sheets:
        flags = []
        if sheet.is_very_hidden:
            flags.append("very hidden")
        elif sheet.is_hidden:
            flags.append("hidden")
        if sheet.hidden_rows:
            flags.append(f"{len(sheet.hidden_rows)} hidden rows")
        if sheet.hidden_columns:
            flags.append(f"{len(sheet.hidden_columns)} hidden cols")
        rows.append(
            "<tr>"
            f"<td>{_e(sheet.name)}</td>"
            f"<td>{_e(sheet.used_range)}</td>"
            f'<td class="num">{sheet.cell_count:,}</td>'
            f'<td class="num">{sheet.formula_count:,}</td>'
            f"<td>{_e(', '.join(flags))}</td>"
            "</tr>"
        )

    notes = []
    if inventory.has_macros:
        notes.append(
            "<strong>This workbook contains macros.</strong> Their presence is "
            "reported but their contents are never examined, so anything they do "
            "to these numbers is outside this audit."
        )
    very_hidden = [s.name for s in inventory.sheets if s.is_very_hidden]
    if very_hidden:
        notes.append(
            "<strong>Very hidden sheets: </strong>"
            + _e(", ".join(very_hidden))
            + ". These cannot be unhidden from the Excel interface."
        )
    if inventory.external_links:
        links = ", ".join(_e(v) for v in inventory.external_links.values())
        notes.append(f"<strong>Links to other workbooks: </strong>{links}")

    note_html = '<div class="note">' + "<br><br>".join(notes) + "</div>" if notes else ""

    return (
        "<h2>What is in this workbook</h2>"
        "<table><thead><tr><th>Sheet</th><th>Used range</th>"
        '<th class="num">Cells</th><th class="num">Formulas</th><th>Notes</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>" + note_html
    )


def render_report(
    inventory: Inventory,
    findings: Sequence[Finding],
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render one workbook's audit as a complete, standalone HTML document."""
    stamp = (generated_at or datetime.now()).strftime("%d %B %Y at %H:%M")
    counts = {s: sum(1 for f in findings if f.severity is s) for s in Severity}

    body: list[str] = [
        '<div class="wrap">',
        f"<h1>{_e(inventory.name)}</h1>",
        f'<p class="sub">Spreadsheet audit &middot; {_e(stamp)} &middot; '
        f"{inventory.formula_count:,} formulas across {len(inventory.sheets)} sheet"
        f"{'s' if len(inventory.sheets) != 1 else ''}</p>",
        '<div class="tiles">',
        *(_tile(s, counts[s]) for s in Severity),
        "</div>",
    ]

    if not findings:
        body.append(
            '<div class="clean"><strong>No findings.</strong> None of the seven checks '
            "matched anything in this workbook. See the limits below before reading "
            "that as a clean bill of health.</div>"
        )

    for severity in Severity:
        matching = [f for f in findings if f.severity is severity]
        if not matching:
            continue
        body.append(
            f"<h2>{_e(_SEVERITY_LABEL[severity])} "
            f'<span class="count">&middot; {len(matching)}</span></h2>'
            f'<p class="blurb">{_e(_SEVERITY_BLURB[severity])}</p>'
        )
        body.extend(_finding_html(f) for f in matching)

    body.append(_inventory_html(inventory))
    body.append(
        "<h2>What this audit does not cover</h2>"
        '<div class="note"><ul style="margin:0;padding-left:1.1rem">'
        + "".join(f"<li>{item}</li>" for item in _NOT_CHECKED)
        + "</ul></div>"
    )
    body.append(
        f"<footer>Generated by Tickmark {_e(__version__)}. "
        "Runs entirely on your machine — no data left it.</footer>"
    )
    body.append("</div>")

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Tickmark — {_e(inventory.name)}</title>"
        f"<style>{_CSS}</style></head><body>" + "".join(body) + "</body></html>\n"
    )


def render_summary_index(
    reports: Iterable[tuple[str, str, dict[Severity, int]]],
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render the batch index (item 11): one row per workbook, linking to its report."""
    stamp = (generated_at or datetime.now()).strftime("%d %B %Y at %H:%M")
    rows = []
    for name, href, counts in reports:
        rows.append(
            "<tr>"
            f'<td><a href="{_e(href)}">{_e(name)}</a></td>'
            + "".join(f'<td class="num">{counts.get(s, 0)}</td>' for s in Severity)
            + "</tr>"
        )

    headers = "".join(f'<th class="num">{_e(_SEVERITY_LABEL[s])}</th>' for s in Severity)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Tickmark — audit summary</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>"
        "<h1>Audit summary</h1>"
        f'<p class="sub">{_e(stamp)} &middot; {len(rows)} workbooks</p>'
        f"<table><thead><tr><th>Workbook</th>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        f"<footer>Generated by Tickmark {_e(__version__)}.</footer>"
        "</div></body></html>\n"
    )
