"""Starting the server: a port, a lockfile, a browser, and one running copy.

Three decisions here, all from 05-architecture.md §1.2 and G10:

**The port is chosen at startup, not hardcoded.** A fixed port is one a hostile
page can guess and probe without the user ever noticing; it is also the thing
that collides with whatever else the user runs. The OS picks a free one, and the
token — not the port — is what makes the server reachable.

**The lockfile lives under %LOCALAPPDATA%.** G10.2 requires the data directory to
default outside cloud-sync roots, because on a default Windows 11 install
``Documents`` is a OneDrive sync root. Tickmark has no data directory to speak of
(NF 5: reports go where the user asks, there is no database), but this file is
still runtime state with a token in it, and it has no business syncing to
anybody's cloud. It holds the port and token so a second launch can hand the
user back to the window already running instead of starting a rival server.

**Binding is to 127.0.0.1, never 0.0.0.0.** The difference is whether the machine
next to this one can reach an audit of your finance folder.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from tickmark.server.session import Session

__all__ = ["LOCK_NAME", "already_running", "lock_path", "reserve_port", "serve"]

LOCK_NAME = "server.json"

HOST = "127.0.0.1"


def state_dir() -> Path:
    """Where runtime state goes: ``%LOCALAPPDATA%\\Tickmark``.

    Falls back to the platform temp directory if LOCALAPPDATA is unset, which on
    Windows means something is unusual about the environment rather than that we
    are elsewhere — but a fallback beats a crash at startup.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if not base:
        import tempfile

        base = tempfile.gettempdir()
    return Path(base) / "Tickmark"


def lock_path() -> Path:
    return state_dir() / LOCK_NAME


@dataclass(frozen=True, slots=True)
class Running:
    """Details of a server already listening, read back from the lockfile."""

    port: int
    token: str

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/?t={self.token}"


def _port_is_live(port: int) -> bool:
    """True if something is listening. Not proof it is *us* — see `already_running`."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        return probe.connect_ex((HOST, port)) == 0


def already_running() -> Running | None:
    """Read the lockfile and confirm the server it names is still there.

    A stale lockfile is the normal case, not an error: the previous run was
    closed with Ctrl-C or the machine was restarted, and nothing cleans up. So
    the port is probed before the file is believed, and an unreadable or
    malformed file is treated as absent rather than fatal.
    """
    path = lock_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        port, token = int(data["port"]), str(data["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not token or not _port_is_live(port):
        return None
    return Running(port, token)


def write_lock(session: Session) -> Path:
    """Record this launch so a second one can find it.

    The token is in here, which is why it lives in a per-user directory. Anything
    able to read it can already read the workbooks being audited.
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": os.getpid(), "port": session.port, "token": session.token}),
        encoding="utf-8",
    )
    # Best effort on Windows, meaningful on POSIX: this file is not for other
    # accounts to read.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return path


def clear_lock() -> None:
    with contextlib.suppress(OSError):
        lock_path().unlink(missing_ok=True)


def reserve_port() -> int:
    """Ask the OS for a free port and hand back the number.

    Bound and released rather than held: uvicorn wants to do its own binding.
    The gap between release and rebind is a race nobody on a single desktop will
    lose, and the alternative — passing a live socket through — buys nothing here.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def serve(*, open_browser: bool = True, port: int | None = None) -> int:
    """Run the server until interrupted. Returns a process exit code."""
    import uvicorn

    from tickmark.server.app import create_app

    existing = already_running()
    if existing is not None and port is None:
        print(f"Tickmark is already running at http://{HOST}:{existing.port}")
        print("Opening that window rather than starting a second one.")
        if open_browser:
            webbrowser.open(existing.url)
        return 0

    session = Session(port=port or reserve_port())
    # Written from the startup hook, not here: see create_app for why a lockfile
    # that names a port before anything is listening on it is a race.
    app = create_app(session, on_ready=lambda: write_lock(session))

    print(f"Tickmark is running at http://{HOST}:{session.port}")
    print("This window has to stay open. Press Ctrl+C to stop.")
    if open_browser:
        # The one URL that carries the token; the server swaps it for a cookie
        # and redirects, so it does not linger in history.
        webbrowser.open(session.entry_url())

    try:
        uvicorn.run(
            app,
            host=HOST,
            port=session.port,
            log_level="warning",
            access_log=False,
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    finally:
        clear_lock()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(serve())
