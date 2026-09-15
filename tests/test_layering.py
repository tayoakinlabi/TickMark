"""The engine must not know the server exists.

05-architecture.md is explicit about this: "because the CLI must work with no
server running, every module under workbook/, formula/, graph/, checks/ and
report/ has to be importable and callable without touching FastAPI. Treat the
server as one of two consumers of the engine, never as its host."

It is worth a test rather than a note, because the drift is so easy and so
quiet. One `from fastapi import HTTPException` inside a check — to report a bad
path nicely — and the CLI has a web framework in its import graph, the frozen
binary grows, and the two entry points have started to diverge in a way nobody
notices until they disagree about a workbook.

The audit is static: the source is parsed and its imports read, rather than
imported and inspected. A runtime check would pass simply because another test
had already imported FastAPI into the process.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "tickmark"

# The engine: everything the CLI needs and the server merely calls.
ENGINE = ("workbook", "formula", "graph", "checks", "report", "config", "findings")

# Anything web-shaped. Not exhaustive of the world, but exhaustive of what this
# project could plausibly reach for.
FORBIDDEN = {
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
    "requests",
    "urllib3",
    "aiohttp",
    "pydantic",
    "websockets",
}


def _engine_modules() -> list[Path]:
    return sorted(
        path for package in ENGINE for path in (SRC / package).rglob("*.py") if path.is_file()
    )


def _imported_roots(path: Path) -> set[str]:
    """Top-level package names imported by one module, at any nesting."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        # level > 0 is a relative import, which cannot reach a third party.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_there_are_engine_modules_to_check():
    # A guard on the guard: if the layout moves and this silently finds nothing,
    # every test below would pass by vacuity.
    assert len(_engine_modules()) > 15


@pytest.mark.parametrize("module", _engine_modules(), ids=lambda p: p.stem)
def test_no_engine_module_imports_anything_web(module: Path):
    offending = _imported_roots(module) & FORBIDDEN
    assert not offending, (
        f"{module.relative_to(SRC)} imports {sorted(offending)}. "
        "The engine has to stay importable with no web dependency: the CLI must "
        "run with no server, and the frozen binary should not carry one twice."
    )


def test_the_cli_does_not_import_the_server_at_module_scope():
    """--serve may reach for it, but importing the CLI must not.

    Checked as "not at module scope" rather than "never", because the flag has
    to start a server somehow. The import lives inside the branch that uses it.
    """
    source = (SRC / "cli" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:  # module level only
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tickmark.server"):
            raise AssertionError("cli/main.py imports the server at module scope")
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("tickmark.server") for a in node.names)


def test_the_server_is_allowed_to_import_the_engine():
    """The dependency is one-directional, and this is the direction that is fine."""
    roots = _imported_roots(SRC / "server" / "app.py")
    assert "fastapi" in roots
    assert "tickmark" in roots
