"""Extract one version's section from CHANGELOG.md, for the GitHub release body.

Run:

    python packaging/release_notes.py v0.1.0 [--out notes.md]

One source of truth, deliberately. The alternative — release notes written into
the GitHub UI by hand — means the changelog in the repository and the notes on
the release drift apart, and afterwards nobody can tell which described the
build. Pulling both from the same file makes the drift impossible rather than
merely discouraged.

Exits non-zero if the version has no section, so a release cannot be published
with an empty or wrong body by accident.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

__all__ = ["extract"]

# A version heading: '## 0.1.0 — first release', '## 0.1.0', '## v0.1.0 - notes'.
_HEADING = re.compile(r"^##\s+v?(?P<version>\d+\.\d+\.\d+)\s*(?:[—–-]\s*(?P<label>.*))?$")


def extract(text: str, version: str) -> str:
    """Return the body of the section for ``version``, without its heading.

    ``version`` may be given with or without a leading ``v``.
    """
    wanted = version.lstrip("vV")
    lines = text.splitlines()

    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = _HEADING.match(line.strip())
        if match is None:
            continue
        if start is None and match.group("version") == wanted:
            start = index + 1
        elif start is not None:
            # The next version heading ends this section.
            end = index
            break

    if start is None:
        raise SystemExit(
            f"no section for version {wanted} in {CHANGELOG.name}. "
            "Add one before tagging: the release body comes from there."
        )

    body = "\n".join(lines[start:end]).strip()
    if not body:
        raise SystemExit(f"the section for {wanted} in {CHANGELOG.name} is empty")
    return body + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the tag or version, e.g. v0.1.0")
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=CHANGELOG,
        help="changelog to read (default: CHANGELOG.md)",
    )
    args = parser.parse_args(argv)

    if not args.changelog.exists():
        raise SystemExit(f"{args.changelog} does not exist")

    body = extract(args.changelog.read_text(encoding="utf-8"), args.version)
    if args.out:
        args.out.write_text(body, encoding="utf-8")
        print(f"wrote {args.out} ({len(body)} bytes)")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
