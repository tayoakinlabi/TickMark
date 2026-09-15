# PyInstaller build definition for the Tickmark CLI.
#
# NF 2 calls for a single Windows installer with a bundled Python, so the user
# never installs a runtime. This spec produces the executable that installer
# ships; installer.iss wraps it.
#
# One-file rather than one-folder, deliberately. A one-folder build is faster to
# start, but it drops ~200 files into Program Files and the extra startup time
# is irrelevant next to a 40-second audit. A single .exe is also what makes the
# "download it and run it" path work without an installer at all, which matters
# while the signing story (G3) is still unresolved and some users will prefer to
# check a SHA-256 by hand.
#
# Build with:
#     uv run --with pyinstaller pyinstaller packaging/tickmark.spec --clean --noconfirm

from PyInstaller.utils.hooks import collect_submodules

# openpyxl and xlrd both reach for modules dynamically in places, and a missing
# one surfaces only at runtime on a user's machine — the worst place to find it.
# Collecting them wholesale costs a few MB and removes that entire class of bug.
hiddenimports = (
    collect_submodules("openpyxl")
    + collect_submodules("xlrd")
    + collect_submodules("olefile")
)

analysis = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Tickmark has no GUI and no scientific stack. Excluding these keeps the
    # binary near 15 MB instead of dragging in tkinter and friends, and a
    # smaller download is a real adoption factor for a tool people try once.
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
        "email",
        "http",
        "xmlrpc",
        "pdb",
        "numpy",
        "PIL",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="tickmark",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries trip antivirus heuristics; not worth it
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
