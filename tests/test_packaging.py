"""The parts of the build script that users depend on.

Only one thing here is load-bearing enough to test, and it shipped wrong in
0.1.0: the checksum sidecar. Builds are unsigned, Windows tells people the
publisher is unknown, and the published SHA-256 is the only assurance available
— so a sidecar that a checksum tool refuses to read is not a cosmetic defect. It
raises a verification failure that looks exactly like a tampered download.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_build():
    """Load packaging/build.py by path.

    `packaging/` is a folder of build scripts, not an importable package, and the
    name collides with the PyPI `packaging` library installed here as a
    transitive dependency.
    """
    spec = importlib.util.spec_from_file_location("tickmark_build", ROOT / "packaging" / "build.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()


@pytest.fixture
def artefact(tmp_path: Path) -> Path:
    path = tmp_path / "tickmark.exe"
    path.write_bytes(b"not really an executable, but it hashes the same way")
    return path


class TestWriteChecksum:
    def test_the_digest_is_correct(self, artefact: Path):
        sidecar = build.write_checksum(artefact)
        expected = hashlib.sha256(artefact.read_bytes()).hexdigest()
        assert sidecar.read_text(encoding="utf-8").split()[0] == expected

    def test_it_is_named_after_the_file_it_describes(self, artefact: Path):
        sidecar = build.write_checksum(artefact)
        assert sidecar.name == "tickmark.exe.sha256"
        assert sidecar.read_text(encoding="utf-8").split()[1] == "tickmark.exe"

    def test_the_separator_is_two_spaces(self, artefact: Path):
        # `sha256sum -c` treats one space as the BSD-style variant; two spaces
        # is the GNU binary/text form the rest of the file assumes.
        sidecar = build.write_checksum(artefact)
        digest, rest = sidecar.read_text(encoding="utf-8").split(" ", 1)
        assert rest.startswith(" ")
        assert len(digest) == 64

    def test_there_is_no_carriage_return(self, artefact: Path):
        """Regression for 0.1.0.

        Python translates "\\n" to "\\r\\n" on Windows unless told otherwise, and
        `sha256sum -c` then looks for a file called "tickmark.exe\\r" and reports
        it missing. Byte-level assertion on purpose: read_text() would hide the
        very thing that broke.
        """
        sidecar = build.write_checksum(artefact)
        raw = sidecar.read_bytes()
        assert b"\r" not in raw
        assert raw.endswith(b"\n")

    def test_a_checksum_tool_can_actually_read_it(self, artefact: Path):
        """End to end, with the tool users are told to use, when it is available.

        The unit assertions above encode what we believe the format is; this
        checks the belief against a real implementation.
        """
        sha256sum = __import__("shutil").which("sha256sum")
        if sha256sum is None:  # pragma: no cover - platform dependent
            pytest.skip("sha256sum is not available")

        build.write_checksum(artefact)
        result = subprocess.run(
            [sha256sum, "-c", "tickmark.exe.sha256"],
            cwd=artefact.parent,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout
