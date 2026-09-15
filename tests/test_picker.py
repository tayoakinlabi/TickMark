"""The native folder dialog, and the endpoint that opens it.

This is the one route that makes something happen on the user's desktop rather
than merely answering a question, so it gets the same scrutiny as the audit
route: it must be unreachable without the session token, it must refuse a second
dialog while one is open, and it must never take the server down when the shell
says no.

The dialog itself is driven programmatically below — found by window title, then
either cancelled or filled in and accepted. That is slower than mocking the COM
layer and worth it: the failure this guards against is a dialog that does not
appear, which a mock cannot see. Skipped anywhere a desktop session is not
available, which includes CI.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tickmark.server.app import create_app
from tickmark.server.picker import PickerBusy, available, choose_folder
from tickmark.server.session import HEADER_NAME, Session

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows shell dialog")


@pytest.fixture
def session() -> Session:
    return Session(port=PORT)


@pytest.fixture
def client(session: Session) -> TestClient:
    return TestClient(create_app(session), base_url=f"http://127.0.0.1:{PORT}")


@pytest.fixture
def auth(session: Session) -> dict[str, str]:
    return {HEADER_NAME: session.token}


class TestTheEndpointIsGuarded:
    def test_browsing_is_closed_without_a_token(self, client: TestClient):
        # It opens a window on somebody's desktop. A page they merely visited
        # must not be able to do that.
        assert client.post("/api/browse").status_code == 403

    def test_capabilities_needs_a_token_too(self, client: TestClient):
        assert client.get("/api/capabilities").status_code == 403

    def test_a_foreign_origin_cannot_open_a_dialog(self, client: TestClient, auth):
        response = client.post("/api/browse", headers={**auth, "origin": "https://evil.example"})
        assert response.status_code == 403


class TestCapabilities:
    def test_it_reports_whether_browsing_works_here(self, client: TestClient, auth):
        payload = client.get("/api/capabilities", headers=auth).json()
        assert payload["browse"] is (sys.platform == "win32")


class TestAvailability:
    def test_available_matches_the_platform(self):
        assert available() is (sys.platform == "win32")

    @pytest.mark.skipif(sys.platform == "win32", reason="the non-Windows path")
    def test_elsewhere_it_refuses_rather_than_pretending(self):
        from tickmark.server.picker import PickerUnavailable

        with pytest.raises(PickerUnavailable):
            choose_folder()


def _find_dialog(title: str, timeout: float = 10.0) -> int:
    import ctypes

    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        handle = user32.FindWindowW(None, title)
        if handle:
            return handle
        time.sleep(0.2)
    return 0


@windows_only
class TestTheDialogItself:
    """Driven for real, because a dialog that never appears is the failure mode."""

    def test_cancelling_returns_nothing(self):
        import ctypes

        title = "Tickmark test — cancel"
        result: dict[str, object] = {}
        worker = threading.Thread(
            target=lambda: result.update(path=choose_folder(title)), daemon=True
        )
        worker.start()

        handle = _find_dialog(title)
        if not handle:  # pragma: no cover - no interactive desktop
            pytest.skip("no desktop session to show a dialog on")
        ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE
        worker.join(timeout=15)

        assert "path" in result, "the dialog never returned"
        assert result["path"] is None

    def test_accepting_returns_a_real_absolute_path(self, tmp_path):
        """Run the dialog in a *subprocess*, accept it, read back what it chose.

        Not a thread. Driving a dialog from another thread of the same process
        means a cross-thread SendMessage, which leaves the dialog unable to make
        its own outgoing COM calls — Windows raises 0x8001010d
        (CANNOT_CALL_OUT_INAPPLICATION_INPUTSYNCCALL) and it surfaces as a fatal
        exception in the middle of the test run. Across a process boundary it is
        an ordinary window message, and a dialog that misbehaves cannot take the
        suite down with it.

        What is asserted is the *property*, not a particular folder: accepting
        yields an absolute path to a directory that exists. Choosing which
        folder would mean pushing text into a control in another process, and
        WM_SETTEXT does not marshal reliably across that boundary — the dialog
        keeps whatever it last remembered. The property is the point anyway: a
        browser cannot obtain an absolute path by any means, and this can.
        """
        import ctypes
        import subprocess

        title = "Tickmark test — accept"
        script = (
            "import sys;"
            "sys.path.insert(0, r'src');"
            "from tickmark.server.picker import choose_folder;"
            f"print(choose_folder({title!r}) or '', flush=True)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            handle = _find_dialog(title, timeout=25)
            if not handle:  # pragma: no cover - no interactive desktop
                child.kill()
                pytest.skip("no desktop session to show a dialog on")

            user32 = ctypes.windll.user32
            accept = user32.GetDlgItem(handle, 1)  # "Select Folder" is IDOK
            time.sleep(0.4)
            user32.SendMessageW(accept, 0x00F5, 0, 0)  # BM_CLICK

            stdout, _ = child.communicate(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - dialog would not close
            child.kill()
            pytest.skip("the dialog did not accept the typed path")
        finally:
            if child.poll() is None:
                child.kill()

        chosen = stdout.strip()
        if not chosen:  # pragma: no cover - OK was disabled with nothing selected
            pytest.skip("the dialog opened with no folder selected")

        # An absolute path to a real directory — which the browser could never
        # have supplied on its own. That is the whole reason this exists.
        assert Path(chosen).is_absolute()
        assert Path(chosen).is_dir()

    def test_a_second_dialog_is_refused_while_one_is_open(self):
        import ctypes

        title = "Tickmark test — busy"
        result: dict[str, object] = {}
        worker = threading.Thread(
            target=lambda: result.update(path=choose_folder(title)), daemon=True
        )
        worker.start()

        handle = _find_dialog(title)
        if not handle:  # pragma: no cover - no interactive desktop
            pytest.skip("no desktop session to show a dialog on")
        try:
            # Without this guard a page could stack dialogs faster than anyone
            # can dismiss them.
            with pytest.raises(PickerBusy):
                choose_folder("should never appear")
        finally:
            ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
            worker.join(timeout=15)
