"""The local server, and mostly the things it refuses (G10.1).

The interesting tests here are not the ones proving an audit works through the
browser. They are the ones proving that **a page the user happens to visit
cannot drive this server.** That threat is easy to dismiss — "it only listens on
localhost" — and the dismissal is wrong: JavaScript on any site can reach
127.0.0.1, and through DNS rebinding it can do so believing it is same-origin.
Both arrive *from* 127.0.0.1, so nothing about the source address distinguishes
them from the real UI.

So every gate gets a test for the attack it exists to stop, and the audit
endpoint gets one proving it is unreachable without the token — because that
endpoint reads any path on the machine and returns what it finds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from tickmark.server.app import create_app
from tickmark.server.middleware import CSP
from tickmark.server.session import COOKIE_NAME, HEADER_NAME, Session

PORT = 8765
GOOD_HOST = f"127.0.0.1:{PORT}"


@pytest.fixture
def session() -> Session:
    return Session(port=PORT)


@pytest.fixture
def client(session: Session) -> TestClient:
    # base_url fixes the Host header to one the allowlist accepts; individual
    # tests override it to model an attack.
    return TestClient(create_app(session), base_url=f"http://{GOOD_HOST}")


@pytest.fixture
def auth(session: Session) -> dict[str, str]:
    return {HEADER_NAME: session.token}


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    for row in range(1, 8):
        ws[f"A{row}"] = row * 10
        ws[f"B{row}"] = f"=A{row}*2"
    ws["B4"] = 999  # the classic bug, so there is a finding to return
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return path


class TestTheTokenGate:
    def test_the_audit_endpoint_is_closed_without_a_token(self, client: TestClient):
        """The one that matters most.

        This endpoint opens any path on the machine and returns what is in it.
        Reachable without a token, it would let any page the user visits read
        their finance folder.
        """
        response = client.post("/api/audit", json={"path": "C:\\"})
        assert response.status_code == 403

    def test_a_wrong_token_is_refused(self, client: TestClient):
        response = client.post(
            "/api/audit", json={"path": "C:\\"}, headers={HEADER_NAME: "not-the-token"}
        )
        assert response.status_code == 403

    def test_the_real_token_is_accepted(self, client: TestClient, auth, workbook: Path):
        response = client.post("/api/audit", json={"path": str(workbook)}, headers=auth)
        assert response.status_code == 200

    def test_a_cookie_works_too(self, client: TestClient, session: Session, workbook: Path):
        client.cookies.set(COOKIE_NAME, session.token)
        assert client.post("/api/audit", json={"path": str(workbook)}).status_code == 200

    def test_refusals_do_not_say_which_gate_failed(self, client: TestClient):
        # A message distinguishing "wrong token" from "wrong host" would tell a
        # probing page how far it had got.
        wrong_token = client.post("/api/audit", json={}, headers={HEADER_NAME: "x"})
        wrong_host = client.post("/api/audit", json={}, headers={"host": "evil.example"})
        assert wrong_token.json() == wrong_host.json() == {"error": "refused"}


class TestTheHostGate:
    def test_a_rebound_hostname_is_refused(self, client: TestClient, auth):
        """DNS rebinding, which the source address cannot detect.

        The attacker points their domain at 127.0.0.1. The packet genuinely
        arrives from loopback; the only evidence of the attack is the Host
        header naming their domain.
        """
        response = client.post(
            "/api/audit",
            json={"path": "C:\\"},
            headers={**auth, "host": "evil.example"},
        )
        assert response.status_code == 403

    def test_the_right_host_on_the_wrong_port_is_refused(self, client: TestClient, auth):
        response = client.get("/api/health", headers={**auth, "host": "127.0.0.1:9999"})
        assert response.status_code == 403

    def test_localhost_is_allowed(self, client: TestClient, auth):
        response = client.get("/api/health", headers={**auth, "host": f"localhost:{PORT}"})
        assert response.status_code == 200


class TestTheOriginGate:
    def test_a_foreign_origin_is_refused(self, client: TestClient, auth, workbook: Path):
        response = client.post(
            "/api/audit",
            json={"path": str(workbook)},
            headers={**auth, "origin": "https://evil.example"},
        )
        assert response.status_code == 403

    def test_our_own_origin_is_allowed(self, client: TestClient, auth, workbook: Path):
        response = client.post(
            "/api/audit",
            json={"path": str(workbook)},
            headers={**auth, "origin": f"http://{GOOD_HOST}"},
        )
        assert response.status_code == 200

    def test_no_origin_is_allowed(self, client: TestClient, auth, workbook: Path):
        # A same-origin request typed into the address bar sends none.
        response = client.post("/api/audit", json={"path": str(workbook)}, headers=auth)
        assert response.status_code == 200


class TestTheHandshake:
    def test_a_token_in_the_url_is_exchanged_for_a_strict_cookie(
        self, client: TestClient, session: Session
    ):
        response = client.get(f"/?t={session.token}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        cookie = response.headers["set-cookie"]
        # Each of these is load-bearing: Strict is what stops another site
        # causing the browser to attach it, HttpOnly what keeps script away.
        assert "samesite=strict" in cookie.lower()
        assert "httponly" in cookie.lower()

    def test_a_wrong_token_in_the_url_is_refused(self, client: TestClient):
        assert client.get("/?t=nonsense", follow_redirects=False).status_code == 403

    def test_the_page_is_not_served_without_the_handshake(self, client: TestClient):
        response = client.get("/")
        assert response.status_code == 403
        assert "needs the link" in response.text

    def test_the_page_is_served_after_it(self, client: TestClient, session: Session):
        client.cookies.set(COOKIE_NAME, session.token)
        response = client.get("/")
        assert response.status_code == 200
        assert "<title>Tickmark</title>" in response.text


class TestHeaders:
    def test_the_csp_forbids_external_origins(self, client: TestClient, auth):
        response = client.get("/api/health", headers=auth)
        policy = response.headers["content-security-policy"]
        assert policy == CSP
        assert "default-src 'self'" in policy
        # No inline script anywhere: that is why the page's JS is a served file.
        assert "'unsafe-inline'" not in policy.split("style-src")[0]

    def test_findings_are_never_cached(self, client: TestClient, auth, workbook: Path):
        response = client.post("/api/audit", json={"path": str(workbook)}, headers=auth)
        assert response.headers["cache-control"] == "no-store"

    def test_the_ui_cannot_be_framed(self, client: TestClient, auth):
        response = client.get("/api/health", headers=auth)
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


class TestAuditing:
    def test_it_returns_the_same_findings_the_engine_produces(
        self, client: TestClient, auth, workbook: Path
    ):
        from tickmark.checks.registry import run_audit
        from tickmark.workbook.loader import open_workbook

        response = client.post("/api/audit", json={"path": str(workbook)}, headers=auth)
        served = response.json()["files"][0]

        with open_workbook(workbook) as wb:
            direct = run_audit(wb).findings

        # The browser and the terminal must never disagree about a workbook.
        assert [f["summary"] for f in served["findings"]] == [f.summary for f in direct]

    def test_coverage_travels_with_the_findings(self, client: TestClient, auth, workbook: Path):
        response = client.post("/api/audit", json={"path": str(workbook)}, headers=auth)
        coverage = response.json()["files"][0]["coverage"]
        assert coverage["total"] > 0

    def test_a_folder_audits_every_workbook_in_it(
        self, client: TestClient, auth, workbook: Path, tmp_path: Path
    ):
        second = Workbook()
        second.active["A1"] = 1
        second.save(tmp_path / "second.xlsx")

        response = client.post("/api/audit", json={"path": str(tmp_path)}, headers=auth)
        names = sorted(f["name"] for f in response.json()["files"])
        assert names == ["book.xlsx", "second.xlsx"]

    def test_a_missing_path_is_a_clear_message_not_a_crash(self, client: TestClient, auth):
        response = client.post("/api/audit", json={"path": "Q:\\nowhere"}, headers=auth)
        assert response.status_code == 400
        assert "Nothing found" in response.json()["error"]

    def test_an_empty_path_is_rejected(self, client: TestClient, auth):
        assert client.post("/api/audit", json={"path": "  "}, headers=auth).status_code == 400

    def test_a_folder_with_no_workbooks_says_so(self, client: TestClient, auth, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("nothing to audit", encoding="utf-8")
        response = client.post("/api/audit", json={"path": str(tmp_path)}, headers=auth)
        assert response.status_code == 400
        assert "No workbooks" in response.json()["error"]

    def test_an_unreadable_file_is_reported_not_fatal(
        self, client: TestClient, auth, tmp_path: Path, workbook: Path
    ):
        (tmp_path / "broken.xlsx").write_bytes(b"this is not a workbook")
        response = client.post("/api/audit", json={"path": str(tmp_path)}, headers=auth)
        assert response.status_code == 200
        files = {f["name"]: f for f in response.json()["files"]}
        # The good one still audited; the bad one carries a reason.
        assert files["broken.xlsx"]["error"]
        assert "findings" in files["book.xlsx"]

    def test_the_report_is_served_and_renders_its_own_styles(
        self, client: TestClient, auth, workbook: Path
    ):
        client.post("/api/audit", json={"path": str(workbook)}, headers=auth)
        response = client.get("/api/report/0", headers=auth)
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text
        # The emailable report carries its own <style>; its CSP has to allow it
        # while still refusing inline script.
        policy = response.headers["content-security-policy"]
        assert "style-src 'self' 'unsafe-inline'" in policy
        assert "script-src 'self'" in policy

    def test_a_report_that_was_never_produced_is_a_404(self, client: TestClient, auth):
        assert client.get("/api/report/999", headers=auth).status_code == 404
