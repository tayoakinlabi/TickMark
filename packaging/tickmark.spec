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
    # uvicorn resolves its protocol and lifespan implementations by name at
    # runtime, so a frozen build without these starts and then fails to serve.
    + collect_submodules("uvicorn")
)

# The browser UI. These are data, not code, so nothing in the import graph pulls
# them in: without this the frozen build starts a server that 404s its own page.
# The smoke test in build.py exists partly to catch exactly this class of
# omission before a release goes out.
datas = [("../src/tickmark/server/static", "tickmark/server/static")]

analysis = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Tickmark has no desktop GUI and no scientific stack. Excluding these keeps
    # the download small, which is a real adoption factor for a tool people try
    # once.
    #
    # 'http' and 'email' were on this list and have been removed: uvicorn imports
    # http.HTTPStatus at module scope and starlette reaches for email.utils, so
    # excluding them produced an executable that audited fine from the terminal
    # and died the instant anyone passed --serve. The build's server smoke test
    # is what caught it; nothing in the test suite could, because from source
    # those modules are simply present.
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
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
