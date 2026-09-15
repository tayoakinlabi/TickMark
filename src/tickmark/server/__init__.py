"""The local-server GUI — item 12's second consumer of the audit engine.

Nothing under here is imported by the engine. The CLI must run with no server,
which is what keeps a browser audit and a terminal audit the same audit; see
``tests/test_layering.py``, which fails the build if that ever stops being true.
"""

from __future__ import annotations

__all__ = ["create_app", "serve"]


def __getattr__(name: str):
    # Imported lazily so that `import tickmark.server` costs nothing until
    # something actually starts a server — FastAPI and uvicorn are not cheap to
    # import, and the CLI should never pay for them.
    if name == "create_app":
        from tickmark.server.app import create_app

        return create_app
    if name == "serve":
        from tickmark.server.launch import serve

        return serve
    raise AttributeError(name)
