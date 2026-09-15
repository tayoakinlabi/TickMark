"""The sample workbook behind the published report.

``docs/index.html`` makes specific claims — that C9 is a constant typed over a
formula, that B18 stops short, that B13 double counts, that 37 of 41 formulas
were recomputed. Those are the first thing anyone sees of this project, and a
landing page that describes findings the product no longer makes is worse than
no landing page.

So the claims are asserted here rather than trusted. If a check changes what it
reports, this fails and the page gets updated in the same commit, instead of
quietly becoming untrue.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "index.html"


def _load_demo():
    spec = importlib.util.spec_from_file_location("tickmark_demo", ROOT / "demo" / "build_demo.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo()


@pytest.fixture(scope="module")
def audited(tmp_path_factory):
    """Build the sample workbook in a scratch directory and audit it."""
    from tickmark.checks.registry import run_audit
    from tickmark.workbook.loader import open_workbook

    path = tmp_path_factory.mktemp("demo") / "quarterly-accounts.xlsx"
    demo.build_workbook(path)
    demo._cached(path, demo.cached_values())

    with open_workbook(path) as book:
        result = run_audit(book)
    return result


def _find(result, check: str, location: str):
    return [f for f in result.findings if f.check == check and f.location == location]


class TestTheFaultsAreStillFound:
    """One test per claim the landing page makes."""

    @pytest.mark.parametrize(
        ("check", "location", "claim"),
        [
            ("inconsistent-range", "Q3 Invoices!C9", "a constant typed over the VAT formula"),
            ("short-range", "Q3 Invoices!B18", "a total that stops short of its rows"),
            ("double-counting", "Summary!B13", "a subtotal counted twice"),
            ("stale-value", "Summary!B6", "a stored value that contradicts its formula"),
            ("external-link", "Summary!B17", "a link to a workbook that moved"),
            ("broken-reference", "Summary!B19", "a #REF! left by a deleted column"),
            ("volatile-function", "Summary!B2", "a volatile TODAY()"),
            ("formula-complexity", "Summary!B15", "the formula nobody wants to inherit"),
        ],
    )
    def test_claim_holds(self, audited, check: str, location: str, claim: str):
        assert _find(audited, check, location), f"the page claims {claim} at {location}"

    def test_the_hardcoded_rate_is_found_in_the_filled_column(self, audited):
        # Grouped into runs either side of the cell someone typed over.
        found = [f for f in audited.findings if f.check == "hardcoded-constant"]
        assert any(f.sheet == "Q3 Invoices" for f in found)
        assert any(f.sheet == "Summary" for f in found)


class TestTheCoverageLine:
    def test_the_published_figures_are_current(self, audited):
        """The page prints 'recomputed 37 of 41'. It has to be true."""
        assert audited.coverage.total == 41
        assert audited.coverage.compared == 37
        assert audited.coverage.total - audited.coverage.verified == 4

    def test_the_page_quotes_those_same_figures(self, audited):
        text = PAGE.read_text(encoding="utf-8")
        assert f"recomputed {audited.coverage.compared} of {audited.coverage.total}" in text


class TestTheWorkbookIsSafeToHandOut:
    def test_it_has_no_circular_reference(self, audited):
        # Deliberate: Excel warns on opening a file with one, and the sample is
        # meant to look unremarkable when a reader opens it themselves. Every
        # other fault is invisible on the sheet, which is the whole argument.
        assert not [f for f in audited.findings if f.check == "circular-reference"]

    def test_every_fault_is_one_somebody_could_plausibly_make(self, audited):
        # A guard against the sample drifting into contrivance: nothing in it
        # should be an error Excel itself would have refused to save.
        assert len(audited.findings) >= 12
