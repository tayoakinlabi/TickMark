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

# The usual install locations, newest first. Inno Setup does not put itself on
# PATH, so looking is more useful than telling the user to fix their PATH.
_INNO_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
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
    target.write_text(f"{digest}  {exe.name}\n", encoding="utf-8")
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
    print("smoke test: ok")


def build_installer(version: str) -> Path | None:
    compiler = next((c for c in _INNO_CANDIDATES if os.path.exists(c)), None)
    if compiler is None:
        print(
            "\nInno Setup not found - skipping the installer.\n"
            "  dist/tickmark.exe is a complete release on its own.\n"
            "  To build the installer too, install Inno Setup 6 from\n"
            "  https://jrsoftware.org/isdl.php and run this again."
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

    print("\nartefacts:")
    for path in (exe, exe.with_suffix(exe.suffix + ".sha256"), installer):
        if path is not None and path.exists():
            print(f"  {path.relative_to(ROOT)}  ({path.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
