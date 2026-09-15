"""Build the release artefacts: the executable, its checksum, and the installer.

Run from the repository root:

    uv run --with pyinstaller python packaging/build.py

What it produces in ``dist/``:

* ``tickmark.exe`` — a single self-contained executable, no Python required
* ``tickmark.exe.sha256`` — the checksum the README tells users to verify
* ``tickmark-<version>-setup.exe`` — the installer, **only if Inno Setup is
  installed**; without it the executable is still a complete, usable release

The checksum is not decoration. Until the signing path in G3 resolves, an
unsigned binary is all users get, and SmartScreen will say the publisher is
unknown — which is true. A published checksum is the one verification step that
is actually available to them in the meantime, so the build must produce it
every time rather than as an afterthought at release.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SPEC = ROOT / "packaging" / "tickmark.spec"
ISS = ROOT / "packaging" / "installer.iss"


def _inno_candidates() -> tuple[str, ...]:
    """Where ISCC.exe might live, most likely first.

    Inno Setup does not put itself on PATH, so looking is more useful than
    telling the user to fix theirs. The %LOCALAPPDATA%\\Programs entry is not an
    afterthought: `winget install JRSoftware.InnoSetup` installs per-user by
    default, so for anyone who follows the README's own suggestion that is the
    only place it lands. A build that reports "Inno Setup not found" immediately
    after you installed it sends you hunting for a problem that is not there.
    """
    roots = [
        os.path.join(local, "Programs") if (local := os.environ.get("LOCALAPPDATA")) else "",
        # Uppercase because Python normalises environment keys to upper case on
        # Windows; these are the same two variables Explorer shows mixed-case.
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("PROGRAMFILES(X86)", ""),
    ]
    return tuple(
        os.path.join(root, edition, "ISCC.exe")
        for root in roots
        if root
        for edition in ("Inno Setup 6", "Inno Setup 5")
    )


def _version() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from tickmark import __version__

    return __version__


def _run(command: list[str]) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"failed: {' '.join(command)}")


def build_executable() -> Path:
    if shutil.which("pyinstaller") is None:
        raise SystemExit(
            "pyinstaller is not on PATH. Run this as:\n"
            "    uv run --with pyinstaller python packaging/build.py"
        )
    _run(["pyinstaller", str(SPEC), "--clean", "--noconfirm", "--distpath", str(DIST)])
    exe = DIST / "tickmark.exe"
    if not exe.exists():
        raise SystemExit("pyinstaller reported success but produced no executable")
    return exe


def write_checksum(exe: Path) -> Path:
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    target = exe.with_suffix(exe.suffix + ".sha256")
    # The two-space format is what `sha256sum -c` expects, so a user on any
    # platform can check it with a tool they already have.
    #
    # newline="" matters, and this shipped wrong once. Python translates "\n" to
    # "\r\n" on Windows by default, and `sha256sum -c` then reads the filename as
    # "tickmark.exe\r" and reports the file missing — a verification failure that
    # looks exactly like a corrupt or tampered download. That is the worst
    # possible false alarm to raise on an unsigned binary whose checksum is the
    # only assurance the user has.
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(f"{digest}  {exe.name}\n")
    print(f"sha256 {digest}")
    return target


def smoke_test(exe: Path) -> None:
    """Prove the frozen binary runs before it is wrapped in an installer.

    A PyInstaller build can succeed and still fail at startup on a missing
    hidden import. Catching that here costs a second; catching it after release
    costs the release.
    """
    result = subprocess.run([str(exe), "--version"], capture_output=True, text=True)
    if result.returncode != 0 or "tickmark" not in result.stdout.lower():
        raise SystemExit(f"the built executable does not run: {result.stderr.strip()}")

    fixture = ROOT / "tests" / "fixtures" / "excel_authored.xls"
    if fixture.exists():
        # Exit code 1 means findings were reported, which is the expected result
        # for this fixture. Only 2 — "nothing could be audited" — is a failure.
        audit = subprocess.run(
            [str(exe), str(fixture), "--no-report", "-q"], capture_output=True, text=True
        )
        if audit.returncode == 2:
            raise SystemExit(f"the built executable cannot audit a workbook: {audit.stderr}")
        print("smoke test: audited the legacy fixture")

    _smoke_test_server(exe)
    print("smoke test: ok")


def _smoke_test_server(exe: Path) -> None:
    """Start the frozen server and fetch its page.

    The browser UI is data rather than code, so nothing in the import graph
    would notice it missing: PyInstaller would happily build an executable that
    serves 404 for its own interface, and every test would still pass because
    the tests run from source where the files are simply there. The only way to
    catch that is to run the built binary and ask it for the page.
    """
    import json
    import os
    import time
    import urllib.request

    process = subprocess.Popen(
        [str(exe), "--serve", "--no-browser"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    lock = Path(os.environ.get("LOCALAPPDATA", "")) / "Tickmark" / "server.json"
    try:
        details = None
        for _ in range(60):  # up to ~15s; a frozen start is slower than a source one
            time.sleep(0.25)
            try:
                details = json.loads(lock.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                continue
        if details is None:
            raise SystemExit("the built executable did not start its server")

        base = f"http://127.0.0.1:{details['port']}"
        request = urllib.request.Request(base + "/")
        request.add_header("Cookie", f"tickmark_session={details['token']}")

        # Retried even though the lockfile is now written from the server's own
        # startup hook: a frozen binary on a cold filesystem is slow, and a
        # build that fails on a timing wobble teaches people to rerun it rather
        # than to read it.
        page = ""
        last: Exception | None = None
        for _ in range(20):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    page = response.read().decode("utf-8", "replace")
                break
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                last = exc
                time.sleep(0.5)
        if not page:
            raise SystemExit(f"the built executable did not serve its page: {last}")
        if "<title>Tickmark</title>" not in page:
            raise SystemExit("the built executable served a page without the UI in it")

        for asset in ("/app.js", "/style.css"):
            with urllib.request.urlopen(base + asset, timeout=20) as response:
                if not response.read():
                    raise SystemExit(f"the built executable served an empty {asset}")
        print("smoke test: served the browser UI")
    finally:
        process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=10)
        with contextlib.suppress(OSError):
            lock.unlink(missing_ok=True)


def build_installer(version: str) -> Path | None:
    compiler = shutil.which("ISCC") or next(
        (c for c in _inno_candidates() if os.path.exists(c)), None
    )
    if compiler is None:
        print(
            "\nInno Setup not found - skipping the installer.\n"
            "  dist/tickmark.exe is a complete release on its own.\n"
            "  To build the installer too:\n"
            "    winget install JRSoftware.InnoSetup\n"
            "  or download it from https://jrsoftware.org/isdl.php, then run this again."
        )
        return None
    _run([compiler, str(ISS)])
    return DIST / f"tickmark-{version}-setup.exe"


def main() -> int:
    # A legacy console codepage would mangle the prose below. main.py solves this
    # the same way for the same reason; a build script should not have to write
    # in ASCII to survive its own output.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")

    version = _version()
    print(f"building Tickmark {version}")

    exe = build_executable()
    smoke_test(exe)
    write_checksum(exe)
    installer = build_installer(version)

    artefacts = [exe, exe.with_suffix(exe.suffix + ".sha256")]
    if installer is not None and installer.exists():
        # The installer gets a checksum too. It is the download the README steers
        # most people towards, so publishing a verifiable hash for the bare .exe
        # and not for the installer would leave the majority of users with
        # nothing to check — which is the opposite of the intent.
        artefacts += [installer, write_checksum(installer)]

    print("\nartefacts:")
    for path in artefacts:
        if path.exists():
            print(f"  {path.relative_to(ROOT)}  ({path.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
