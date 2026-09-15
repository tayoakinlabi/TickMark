"""The command-line entry point.

One of two consumers of the audit engine (04-tickmark.md item 12); the local
server is the other. Nothing below ``tickmark.cli`` imports anything web-related,
which is what keeps that promise honest rather than aspirational.

Exit codes, chosen so this can sit in a pre-commit hook or a nightly job:

    0  audit completed, nothing above the failure threshold
    1  audit completed, findings at or above the threshold
    2  nothing could be audited (bad path, unreadable file)

A file that cannot be read is reported and skipped, never fatal — auditing 199
of 200 workbooks and saying which one failed beats auditing none of them.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from tickmark import __version__
from tickmark.checks.registry import run_audit
from tickmark.checks.stale_values import Coverage
from tickmark.config.file import CONFIG_NAME, ConfigError, find_config, load_config
from tickmark.config.rules import DEFAULT_RULES, Rules
from tickmark.findings.model import Finding, Severity
from tickmark.report.html_report import render_report, render_summary_index
from tickmark.workbook.inventory import take_inventory
from tickmark.workbook.loader import WorkbookError, open_workbook

__all__ = ["main"]

_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlt")

_EXIT_OK = 0
_EXIT_FINDINGS = 1
_EXIT_NOTHING_AUDITED = 2


def _discover(target: Path, *, recursive: bool) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    return sorted(
        p
        for p in target.glob(pattern)
        # '~$' files are Excel's lock files for open workbooks, not workbooks.
        if p.is_file() and p.suffix.lower() in _SUFFIXES and not p.name.startswith("~$")
    )


def _print_findings(findings: Sequence[Finding], *, path: Path) -> None:
    if not findings:
        print(f"  {path.name}: no findings")
        return

    print(f"  {path.name}: {len(findings)} finding{'s' if len(findings) != 1 else ''}")
    current: Severity | None = None
    for finding in findings:
        if finding.severity is not current:
            current = finding.severity
            print(f"    [{current.value}]")
        print(f"      {finding.location:<22} {finding.summary}")


def _audit_one(path: Path, rules: Rules, *, quiet: bool) -> tuple[list[Finding], str] | None:
    """Audit one workbook. Returns ``(findings, html)`` or ``None`` if unreadable."""
    try:
        with open_workbook(path) as workbook:
            inventory = take_inventory(workbook)
            result = run_audit(workbook, rules=rules)
            html = render_report(inventory, result.findings, coverage=result.coverage)
    except WorkbookError as exc:
        print(f"  {path.name}: could not read — {exc.reason}", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
        print(f"  {path.name}: failed — {exc}", file=sys.stderr)
        return None

    if not quiet:
        _print_findings(result.findings, path=path)
        _print_coverage(result.coverage)
    return result.findings, html


def _print_coverage(coverage: Coverage) -> None:
    """Say what was verified, so silence is never mistaken for a clean bill.

    Printed on every run rather than only when something was found: the whole
    point of the number is that it qualifies the *absence* of findings.
    """
    if not coverage.total:
        return
    refused = coverage.total - coverage.verified
    line = f"    verified {coverage.compared} of {coverage.total} formulas arithmetically"
    if refused:
        line += f" ({refused} outside the evaluated subset)"
    print(line)
    if coverage.verified and not coverage.compared:
        # Distinct from "nothing could be evaluated": the arithmetic was fine,
        # there was simply no stored value to check it against. Without this the
        # line above reads as a failure of the checker rather than a property of
        # the file, and the user has no idea the remedy is to open and save it.
        print("      (this workbook stores no calculated values to compare against)")


def _rules_from(args: argparse.Namespace) -> Rules:
    """Settings from the config file, with command-line flags layered on top.

    Precedence is deliberate and one-directional: the file holds what a team
    agrees on, the flags hold what one person wants for one run. A flag that
    could be silently overridden by a file checked in beside the workbooks would
    be worse than no flag at all.
    """
    rules = DEFAULT_RULES
    path: Path | None = None

    if not args.no_config:
        path = args.config or find_config(args.target)
        if args.config is not None and not args.config.is_file():
            raise ConfigError(f"no config file at {args.config}")

    if path is not None:
        rules, warnings = load_config(path)
        print(f"using {path}")
        for warning in warnings:
            # Printed, never fatal: a broken rule costs the user that rule.
            print(f"  config: {warning}", file=sys.stderr)

    replacements: dict[str, object] = {}
    if args.include_integers:
        replacements["report_integer_constants"] = True
    if args.no_evaluate:
        replacements["evaluate_formulas"] = False
    return replace(rules, **replacements) if replacements else rules


def _threshold(name: str) -> int:
    """Severity rank at or above which the exit code becomes non-zero.

    Ranks count *down* in severity (high is 0), and the test is ``worst <=
    limit`` — so "never" must be below every real rank, not above it. Using a
    large number here would make every finding fail the build, which is the exact
    opposite of what the flag says.
    """
    return {"high": 0, "medium": 1, "low": 2, "info": 3, "never": -1}[name]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tickmark",
        description=(
            "Audit Excel workbooks for inconsistent formulas, hardcoded constants, "
            "broken links and silent errors. Runs entirely on your machine."
        ),
        epilog="Tickmark never writes to an audited workbook.",
    )
    parser.add_argument("target", type=Path, help="a .xlsx file, or a folder of them")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="where to write the HTML report (default: alongside the workbook)",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="search subfolders too")
    parser.add_argument(
        "--no-report", action="store_true", help="print findings only, write no HTML"
    )
    parser.add_argument(
        "--fail-on",
        choices=("high", "medium", "low", "info", "never"),
        default="high",
        help="severity that makes the exit code non-zero (default: high)",
    )
    parser.add_argument(
        "--include-integers",
        action="store_true",
        help="also report whole-number constants inside formulas",
    )
    parser.add_argument(
        "--no-evaluate",
        action="store_true",
        help="skip check 10, which recomputes simple formulas (the slowest check)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=f"settings and custom rules (default: nearest {CONFIG_NAME}, searching upwards)",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help=f"ignore any {CONFIG_NAME} that would otherwise be found",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress per-finding output")
    parser.add_argument("--version", action="version", version=f"tickmark {__version__}")
    return parser


def _make_output_safe() -> None:
    """Stop a legacy console codepage from mangling or crashing on output.

    Windows-first (G2) means the default console is often still cp1252, and
    everything printed here can contain arbitrary text: sheet names, defined
    names, formula contents. Without this, auditing a workbook with a sheet named
    in Greek or Polish raises UnicodeEncodeError and the run dies — having read
    the file successfully.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # An exotic or already-wrapped stream may refuse; printing is not
            # worth failing the audit over.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _make_output_safe()
    args = build_parser().parse_args(argv)

    paths = _discover(args.target, recursive=args.recursive)
    if not paths:
        print(f"nothing to audit at {args.target}", file=sys.stderr)
        return _EXIT_NOTHING_AUDITED

    try:
        rules = _rules_from(args)
    except ConfigError as exc:
        print(f"{exc}", file=sys.stderr)
        return _EXIT_NOTHING_AUDITED

    output_dir = args.output if args.output and args.output.is_dir() else None
    if args.output and not args.output.suffix and not args.output.exists():
        args.output.mkdir(parents=True, exist_ok=True)
        output_dir = args.output

    print(f"auditing {len(paths)} workbook{'s' if len(paths) != 1 else ''}")

    audited = 0
    all_findings: list[Finding] = []
    index: list[tuple[str, str, dict[Severity, int]]] = []

    for path in paths:
        result = _audit_one(path, rules, quiet=args.quiet)
        if result is None:
            continue
        findings, html = result
        audited += 1
        all_findings.extend(findings)

        if args.no_report:
            continue

        if len(paths) == 1 and args.output and args.output.suffix:
            destination = args.output
        else:
            base = output_dir or path.parent
            destination = base / f"{path.stem}.tickmark.html"

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8")
        print(f"    report: {destination}")

        counts = {s: sum(1 for f in findings if f.severity is s) for s in Severity}
        index.append((path.name, destination.name, counts))

    if audited == 0:
        print("no workbooks could be read", file=sys.stderr)
        return _EXIT_NOTHING_AUDITED

    if len(index) > 1 and not args.no_report:
        base = output_dir or args.target if args.target.is_dir() else paths[0].parent
        summary = Path(base) / "tickmark-summary.html"
        summary.write_text(render_summary_index(index), encoding="utf-8")
        print(f"summary: {summary}")

    limit = _threshold(args.fail_on)
    worst = min((f.severity.rank for f in all_findings), default=99)
    return _EXIT_FINDINGS if worst <= limit else _EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
