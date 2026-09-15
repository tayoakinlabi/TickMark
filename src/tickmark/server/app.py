"""The local server — item 12's second consumer of the audit engine.

**A consumer, never a host.** Everything here calls the same engine the CLI
calls, through the same public functions, and adds nothing to it. That boundary
is the point of 05-architecture.md's note that the CLI must work with no server:
if an audit behaved differently through the browser than through the terminal,
neither number could be trusted. ``tests/test_layering.py`` fails the build if
the engine ever grows a web import.

The UI is three static files rather than a built single-page app. The other
three products in the portfolio will want a real frontend toolchain; Tickmark's
entire interface is "choose a path, read the findings", and adding a Node build
step to a Python project whose selling point is a small offline installer would
cost more than it returns. The shell this proves — bound port, token handshake,
CSP, lockfile — is what transfers, and none of that depends on the framework.

No file uploads. A browser will not hand a page an absolute path, so the user
types or pastes one, which is how somebody with a folder of workbooks already
works. Accepting an upload instead would mean writing a copy of the file being
audited, and "Tickmark never writes" is a claim worth more than a file picker.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from tickmark import __version__
from tickmark.checks.registry import run_audit
from tickmark.config.file import ConfigError, find_config, load_config
from tickmark.config.rules import DEFAULT_RULES, Rules
from tickmark.findings.model import Severity
from tickmark.report.html_report import render_report
from tickmark.server.middleware import REPORT_CSP, install_security
from tickmark.server.picker import PickerBusy, PickerUnavailable, choose_folder
from tickmark.server.picker import available as picker_available
from tickmark.server.session import COOKIE_NAME, QUERY_NAME, Session
from tickmark.workbook.inventory import take_inventory
from tickmark.workbook.loader import WorkbookError, open_workbook

__all__ = ["SUFFIXES", "create_app"]

STATIC = Path(__file__).parent / "static"

SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlt")

# One audit of a folder can produce a great many reports, and each is a complete
# HTML document held in memory until the page asks for it. Bounded so a careless
# run at a large share cannot exhaust memory; the findings themselves are always
# returned, only the rendered reports are capped.
_MAX_HELD_REPORTS = 200


@dataclass
class _Reports:
    """Rendered reports from the last run, addressable by index."""

    items: dict[int, tuple[str, str]]

    def clear(self) -> None:
        self.items.clear()


def _discover(target: Path, *, recursive: bool) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    return sorted(
        p
        for p in target.glob(pattern)
        if p.is_file() and p.suffix.lower() in SUFFIXES and not p.name.startswith("~$")
    )


def _rules_for(target: Path, *, evaluate: bool, include_integers: bool) -> tuple[Rules, list[str]]:
    """Same config discovery the CLI does, so both entry points agree."""
    from dataclasses import replace

    rules, warnings = DEFAULT_RULES, []
    path = find_config(target)
    if path is not None:
        try:
            rules, warnings = load_config(path)
        except ConfigError as exc:
            warnings = [str(exc)]
    changes: dict[str, Any] = {}
    if include_integers:
        changes["report_integer_constants"] = True
    if not evaluate:
        changes["evaluate_formulas"] = False
    return (replace(rules, **changes) if changes else rules), warnings


def create_app(session: Session, on_ready: Callable[[], None] | None = None) -> FastAPI:
    """Build the app for one launch. The session carries that launch's token.

    ``on_ready`` runs once the server is actually accepting connections. The
    lockfile is written from there rather than before ``uvicorn.run``, because
    advertising a port that nothing is listening on yet is a real race: a second
    launch reads the file, probes the port, finds nothing, concludes no server is
    running, and starts a rival one on a different port — leaving the first
    orphaned and the lockfile pointing at the second.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if on_ready is not None:
            on_ready()
        yield

    app = FastAPI(
        title="Tickmark",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    reports = _Reports({})
    install_security(app, session)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        # Deliberately says nothing about the machine or what is on it.
        return JSONResponse({"ok": True, "version": __version__})

    @app.get("/")
    async def index(request: Request):
        """Serve the UI, exchanging a token in the URL for a strict cookie.

        The redirect is the point: after this, the token is in a cookie the
        browser will not attach to another site's requests, and it is gone from
        the address bar, from history, and from any Referer the user generates.
        """
        supplied = request.query_params.get(QUERY_NAME)
        if supplied is not None:
            if not session.matches(supplied):
                return JSONResponse({"error": "refused"}, status_code=403)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                COOKIE_NAME,
                session.token,
                httponly=True,
                samesite="strict",
                path="/",
                secure=False,  # plain http on loopback; there is no TLS to require
            )
            return response

        if not session.matches(request.cookies.get(COOKIE_NAME)):
            return HTMLResponse(
                "<h1>Tickmark</h1><p>This page needs the link that Tickmark opened. "
                "Start it again from the terminal or the Start menu.</p>",
                status_code=403,
            )
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    async def script() -> FileResponse:
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(STATIC / "style.css", media_type="text/css")

    @app.get("/api/capabilities")
    async def capabilities() -> JSONResponse:
        """What this machine can do, so the page shows only controls that work."""
        return JSONResponse({"browse": picker_available()})

    # Deliberately `def`, not `async def`: the dialog blocks until somebody
    # dismisses it, and Starlette runs a sync route on a worker thread rather
    # than stalling the event loop and every other request with it.
    @app.post("/api/browse")
    def browse() -> JSONResponse:
        """Open a native folder chooser on the machine running the server.

        Not a filesystem API. It lists nothing and reads nothing; it returns the
        one path a person standing at this machine picked. The browser cannot
        learn an absolute path by any other means, which is the whole reason
        this exists.
        """
        try:
            chosen = choose_folder()
        except PickerBusy:
            return JSONResponse(
                {"error": "A folder window is already open. Finish with that one first."},
                status_code=409,
            )
        except PickerUnavailable as exc:
            return JSONResponse({"error": str(exc)}, status_code=501)
        except Exception as exc:  # noqa: BLE001 - a dialog must never take the server down
            return JSONResponse(
                {"error": f"Could not open a folder window: {exc}"}, status_code=500
            )

        if chosen is None:
            return JSONResponse({"cancelled": True})
        return JSONResponse({"path": chosen})

    @app.post("/api/audit")
    async def audit(request: Request) -> JSONResponse:
        body = await request.json()
        raw = str(body.get("path") or "").strip().strip('"')
        if not raw:
            return JSONResponse({"error": "Enter a file or folder to audit."}, status_code=400)

        target = Path(raw).expanduser()
        if not target.exists():
            return JSONResponse({"error": f"Nothing found at {target}"}, status_code=400)

        recursive = bool(body.get("recursive"))
        evaluate = body.get("evaluate", True)
        include_integers = bool(body.get("include_integers"))

        paths = _discover(target, recursive=recursive)
        if not paths:
            return JSONResponse({"error": f"No workbooks to audit at {target}"}, status_code=400)

        rules, warnings = _rules_for(
            target, evaluate=bool(evaluate), include_integers=include_integers
        )

        reports.clear()
        files: list[dict[str, Any]] = []
        for index, path in enumerate(paths):
            try:
                with open_workbook(path) as workbook:
                    inventory = take_inventory(workbook)
                    result = run_audit(workbook, rules=rules)
                    html = render_report(inventory, result.findings, coverage=result.coverage)
            except WorkbookError as exc:
                files.append({"name": path.name, "path": str(path), "error": exc.reason})
                continue
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
                files.append({"name": path.name, "path": str(path), "error": str(exc)})
                continue

            if len(reports.items) < _MAX_HELD_REPORTS:
                reports.items[index] = (path.name, html)

            files.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "report": index if index in reports.items else None,
                    "counts": {
                        s.value: sum(1 for f in result.findings if f.severity is s)
                        for s in Severity
                    },
                    "coverage": {
                        "total": result.coverage.total,
                        "verified": result.coverage.verified,
                        "compared": result.coverage.compared,
                    },
                    "findings": [
                        {
                            "check": f.check,
                            "severity": f.severity.value,
                            "location": f.location,
                            "summary": f.summary,
                            "explanation": f.explanation,
                            "formula": f.formula,
                        }
                        for f in result.findings
                    ],
                }
            )

        return JSONResponse({"files": files, "warnings": warnings})

    @app.get("/api/report/{index}")
    async def report(index: int):
        held = reports.items.get(index)
        if held is None:
            return JSONResponse({"error": "That report is no longer held."}, status_code=404)
        name, html = held
        response = HTMLResponse(html)
        response.headers["Content-Security-Policy"] = REPORT_CSP
        response.headers["Content-Disposition"] = f'inline; filename="{name}.tickmark.html"'
        return response

    return app
