"""The changelog-to-release-notes extractor.

Tested because it sits on the release path. If it silently returns the wrong
section, the notes on a published release describe a different build from the
one attached to it — and that is the kind of mistake nobody catches until
somebody is trying to work out which version broke them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"


def _load_extract():
    """Load the script by path rather than importing ``packaging.release_notes``.

    ``packaging/`` holds build scripts, not an importable package, and the name
    collides with the PyPI ``packaging`` library that is installed here as a
    transitive dependency. Importing by path sidesteps both facts without
    renaming a directory that build tooling already refers to.
    """
    spec = importlib.util.spec_from_file_location(
        "tickmark_release_notes", ROOT / "packaging" / "release_notes.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract


extract = _load_extract()

SAMPLE = """# Changelog

Preamble that belongs to no version.

## 0.2.0 — second release

Newer things.

- a bullet

## 0.1.0 — first release

Older things.

## 0.0.1

The very first.
"""


class TestExtract:
    def test_takes_the_named_section_only(self):
        assert extract(SAMPLE, "0.2.0") == "Newer things.\n\n- a bullet\n"

    def test_a_middle_section_stops_at_the_next_heading(self):
        assert extract(SAMPLE, "0.1.0") == "Older things.\n"

    def test_the_last_section_runs_to_the_end(self):
        assert extract(SAMPLE, "0.0.1") == "The very first.\n"

    def test_the_leading_v_is_optional(self):
        assert extract(SAMPLE, "v0.2.0") == extract(SAMPLE, "0.2.0")

    def test_the_preamble_is_never_included(self):
        for version in ("0.2.0", "0.1.0", "0.0.1"):
            assert "belongs to no version" not in extract(SAMPLE, version)

    def test_a_heading_without_a_label_still_matches(self):
        assert extract("## 1.2.3\n\nbody\n", "1.2.3") == "body\n"

    def test_an_unknown_version_fails_loudly(self):
        # Must not return empty: a release published with a silently empty body
        # is worse than a release that failed to publish.
        with pytest.raises(SystemExit):
            extract(SAMPLE, "9.9.9")

    def test_an_empty_section_fails_loudly(self):
        with pytest.raises(SystemExit):
            extract("## 1.0.0 — nothing here\n\n## 0.9.0\n\nbody\n", "1.0.0")


class TestAgainstTheRealChangelog:
    def test_the_current_version_has_notes(self):
        """The shipped version must be extractable, or the release workflow fails.

        This is the test that catches the common mistake: bumping the version in
        pyproject.toml and forgetting to add the matching changelog section.
        """
        from tickmark import __version__

        body = extract(CHANGELOG.read_text(encoding="utf-8"), __version__)
        assert len(body) > 200
        # The honesty line is the one thing these notes must never lose.
        #
        # Whitespace is collapsed first. The changelog is hard-wrapped prose, so
        # a phrase straddling a line break is invisible to a plain substring
        # test — which is a property of the paragraph's width, not of whether
        # the sentence is there. Asserting on the raw text made reflowing a
        # paragraph a test failure.
        flattened = " ".join(body.split())
        assert "not a formula that passed" in flattened
