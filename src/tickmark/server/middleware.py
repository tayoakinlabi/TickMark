"""Every request is checked here before it reaches a route (G10.1).

Three gates, in order, because each is cheap and each covers a different attack:

1. **Host allowlist.** The ``Host`` header must name 127.0.0.1 or localhost on
   this launch's port. This is what stops DNS rebinding: the attacker's page
   resolves their domain to 127.0.0.1 and the browser then sends ``Host:
   evil.example``, which does not match. A source-address check cannot see this
   at all, because by then the packet really does come from 127.0.0.1.

2. **Origin allowlist.** Present on cross-origin requests the browser makes on a
   page's behalf. Absent is allowed — a same-origin ``GET`` typed into the bar
   sends none — but a *wrong* one is refused outright.

3. **Token.** Cookie or header, constant-time. The cookie is ``SameSite=Strict``
   so another site cannot cause the browser to attach it.

Static assets and the health endpoint sit behind the first two gates but not the
third, so the page can load and then authenticate itself. Nothing behind them
reads a workbook or discloses a finding.

**Failures say nothing useful.** A refusal is a bare 403 with a fixed body: a
message distinguishing "wrong token" from "wrong host" would tell a probing page
which gate it had cleared.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from tickmark.server.session import COOKIE_NAME, HEADER_NAME, Session

__all__ = ["CSP", "REPORT_CSP", "install_security"]

# Forbids every external origin, which is the requirement G10.1 names. No
# 'unsafe-inline' anywhere: the page's script and styles are served as files
# from this origin precisely so this can stay strict.
CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

# The rendered report is a self-contained document with its own <style> block —
# that is what makes it emailable, and it is the product's actual deliverable.
# Viewing one in a tab therefore needs inline styles allowed. Scoped to that one
# route and no further, and script-src stays 'self' even here: the report's text
# comes from a workbook, every field of it is HTML-escaped by the renderer, and
# if an escape ever failed this is the line that keeps it inert.
REPORT_CSP = CSP.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")

# Reachable before the token is presented: the page itself, the assets it needs
# to run, and a liveness probe that discloses nothing.
_UNAUTHENTICATED = frozenset({"/", "/app.js", "/style.css", "/favicon.ico", "/api/health"})


def _refuse() -> JSONResponse:
    """One response for every kind of refusal. See the module docstring."""
    return JSONResponse({"error": "refused"}, status_code=403)


def install_security(app, session: Session) -> None:
    """Attach the request gate and the response headers to an app."""

    @app.middleware("http")
    async def _guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        host = request.headers.get("host", "")
        if host not in session.allowed_hosts:
            return _refuse()

        origin = request.headers.get("origin")
        if origin is not None and origin not in session.allowed_origins:
            return _refuse()

        path = request.url.path
        if path not in _UNAUTHENTICATED:
            supplied = request.headers.get(HEADER_NAME) or request.cookies.get(COOKIE_NAME)
            if not session.matches(supplied):
                return _refuse()

        response = await call_next(request)
        # setdefault, not assignment: a route that needs a different policy sets
        # its own, and this must not silently overwrite it.
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # This UI is never legitimately framed, and the report it renders comes
        # from a file the user chose.
        response.headers["X-Frame-Options"] = "DENY"
        # Nothing here is cacheable: a findings payload sitting in a disk cache
        # would outlive the launch that was allowed to produce it.
        response.headers["Cache-Control"] = "no-store"
        return response
