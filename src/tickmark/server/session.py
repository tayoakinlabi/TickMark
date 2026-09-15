"""The per-launch token, and why a local server needs one at all (G10.1).

"It only listens on 127.0.0.1, so only this machine can reach it" is the
intuition this module exists to correct. The threat is not another user on the
machine. It is **any website the user happens to visit.** JavaScript on
evil.example can issue requests to ``http://127.0.0.1:8765`` directly, and
through DNS rebinding it can do so with the browser believing the response came
from its own origin. Both attacks arrive *from* 127.0.0.1, so checking the
source address proves nothing whatsoever — the request genuinely is local.

What a visiting page cannot do is guess a 256-bit secret minted seconds ago. So:

* A fresh token per launch, from :mod:`secrets`. Never persisted beyond the
  lockfile that lets a second launch find the running one, never reused.
* The browser is opened at a URL carrying it once. The server exchanges it for a
  ``SameSite=Strict`` cookie and redirects to a clean URL, so the token stops
  appearing in history, in the address bar, or in a ``Referer`` header on any
  link the user later follows.
* Every comparison is constant-time. A token checked with ``==`` leaks its
  prefix to anything that can time the response, and a local attacker has
  excellent timing.

``SameSite=Strict`` is doing real work here: it means the cookie is not attached
to requests initiated by any other site, so even a page that knows the port
cannot ride the user's session.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

__all__ = ["COOKIE_NAME", "HEADER_NAME", "QUERY_NAME", "Session"]

COOKIE_NAME = "tickmark_session"
HEADER_NAME = "x-tickmark-token"
QUERY_NAME = "t"

# 32 bytes of urandom, URL-safe. Long enough that guessing is not a strategy and
# short enough to survive being pasted into an address bar.
_TOKEN_BYTES = 32


def _mint() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


@dataclass(frozen=True, slots=True)
class Session:
    """One launch's identity: a token, and the origins allowed to use it."""

    token: str = field(default_factory=_mint)
    port: int = 0

    def matches(self, candidate: str | None) -> bool:
        """Constant-time comparison. ``None`` and empty are never valid."""
        if not candidate:
            return False
        return secrets.compare_digest(self.token, candidate)

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Host header values this server will answer to.

        The port is part of each entry on purpose. A ``Host`` of
        ``evil.example:8765`` is what a DNS-rebinding attack looks like from
        inside the server, and it is rejected here by name rather than by any
        inference about where the packet came from.
        """
        return frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(f"http://{host}" for host in self.allowed_hosts)

    def entry_url(self) -> str:
        """The one URL that carries the token — what the browser is opened with."""
        return f"http://127.0.0.1:{self.port}/?{QUERY_NAME}={self.token}"
