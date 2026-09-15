"""Port selection and the lockfile.

The lockfile exists so a second launch hands the user back to the window already
running instead of starting a rival server on a different port. Almost all the
risk in that is in the *stale* case: the previous run was killed, the machine
restarted, and nothing cleaned up. A lockfile believed too readily means
Tickmark refuses to start and points the user at a dead port — which looks
exactly like the program being broken.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from tickmark.server import launch
from tickmark.server.session import Session


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the lockfile at a scratch directory, never the real one."""
    monkeypatch.setattr(launch, "state_dir", lambda: tmp_path)
    return tmp_path / launch.LOCK_NAME


class TestPortSelection:
    def test_a_free_port_is_returned(self):
        port = launch.reserve_port()
        assert 1024 < port < 65536

    def test_the_port_is_actually_bindable(self):
        """Reserved by binding and releasing, so the number must be rebindable.

        There is an unavoidable race in that design — something else on the
        machine can take the port in the gap — so this retries rather than
        asserting on one attempt. A shared CI runner loses that race often
        enough to matter, and a test that fails for a reason the code is not
        responsible for teaches people to rerun rather than to read.
        """
        for attempt in range(5):
            port = launch.reserve_port()
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.bind((launch.HOST, port))
                return
            except OSError:
                if attempt == 4:
                    raise


class TestLockfile:
    def test_it_records_the_port_and_token(self, state: Path):
        session = Session(port=12345)
        launch.write_lock(session)
        data = json.loads(state.read_text(encoding="utf-8"))
        assert data["port"] == 12345
        assert data["token"] == session.token
        assert data["pid"]

    def test_clearing_removes_it(self, state: Path):
        launch.write_lock(Session(port=1))
        launch.clear_lock()
        assert not state.exists()

    def test_clearing_when_absent_is_not_an_error(self, state: Path):
        launch.clear_lock()  # must not raise

    def test_absent_means_nothing_is_running(self, state: Path):
        assert launch.already_running() is None

    def test_a_stale_lockfile_is_treated_as_absent(
        self, state: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The case that matters: a port nothing is listening on.

        Believing this would make Tickmark refuse to start and send the user to
        a dead address, which is indistinguishable from the program being broken.

        The liveness probe is stubbed rather than pointed at a port we hope is
        free. Reserving a port and releasing it does not keep it free, so the
        original version of this test asserted "nothing is listening here" and
        depended on the rest of the machine to cooperate. What is being tested
        is the decision — a lockfile naming a dead port is ignored — and that
        deserves to be checked deterministically. `_port_is_live` itself is
        covered by the live-listener test below, which holds its own socket.
        """
        monkeypatch.setattr(launch, "_port_is_live", lambda port: False)
        launch.write_lock(Session(port=54321))
        assert launch.already_running() is None

    def test_a_malformed_lockfile_is_treated_as_absent(self, state: Path):
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{not json at all", encoding="utf-8")
        assert launch.already_running() is None

    def test_a_lockfile_missing_its_fields_is_treated_as_absent(self, state: Path):
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pid": 1}), encoding="utf-8")
        assert launch.already_running() is None

    def test_a_live_port_is_reported_as_running(self, state: Path):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((launch.HOST, 0))
            listener.listen(1)
            port = listener.getsockname()[1]

            session = Session(port=port)
            launch.write_lock(session)

            running = launch.already_running()
            assert running is not None
            assert running.port == port
            assert running.token == session.token
            assert running.url.startswith(f"http://{launch.HOST}:{port}/?t=")


class TestStateDirectory:
    def test_it_sits_under_localappdata(self, monkeypatch: pytest.MonkeyPatch):
        # G10.2: not Documents, which is a OneDrive sync root on a default
        # Windows 11 install. This file carries a session token.
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
        assert launch.state_dir() == Path(r"C:\Users\someone\AppData\Local\Tickmark")

    def test_it_still_resolves_with_no_localappdata(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert launch.state_dir().name == "Tickmark"
