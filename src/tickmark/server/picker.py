"""A native "choose a folder" dialog, opened by the server on the user's machine.

The browser interface has one awkward step: a page cannot learn an absolute path.
A file input hands JavaScript a blob and a bare filename, never
``C:\\Shared\\Finance`` — deliberately, and no amount of markup gets around it.
So the user types or pastes a path, which is fine for anyone who lives in a
terminal and a small tax on everyone else.

The server, though, is running on the same machine as the browser. It can open a
real dialog. That is the one thing this module does.

**Why not tkinter.** It is the obvious answer and it is wrong twice over: it is
excluded from the frozen build to keep the download small, and Tk is not thread
safe, while this has to run on a worker thread of an async server. The Windows
shell dialog has neither problem — no dependency, nothing added to the binary,
and COM is happy on a worker thread provided it is initialised as a single
threaded apartment, which is exactly what a modal dialog wants anyway.

**It is deliberately not a file-system API.** Nothing here lists, reads or walks
anything. It shows a dialog and returns the one path a person standing at this
machine chose. A page that is somehow holding the session token still cannot use
this to enumerate the disk — the worst it can do is make a dialog appear, which
is visible and which the guard below limits to one at a time.
"""

from __future__ import annotations

import sys
import threading

__all__ = ["PickerBusy", "PickerUnavailable", "available", "choose_folder"]


class PickerUnavailable(RuntimeError):
    """No native dialog on this platform, or the shell refused to provide one."""


class PickerBusy(RuntimeError):
    """A dialog is already open. Showing a second is never what anyone wants."""


# One dialog at a time. Without this, a page could stack dialogs faster than a
# person can dismiss them; with it the second request is refused immediately and
# the UI says so.
_lock = threading.Lock()

# COM and shell constants, from the Windows SDK headers.
_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_INPROC_SERVER = 0x1
_S_OK = 0
_RPC_E_CHANGED_MODE = -2147417850  # already initialised, different model: fine

_FOS_PICKFOLDERS = 0x00000020
_FOS_FORCEFILESYSTEM = 0x00000040  # refuse virtual places we cannot hand back
_FOS_PATHMUSTEXIST = 0x00000800

_SIGDN_FILESYSPATH = 0x80058000

# Vtable slots. IFileOpenDialog inherits IUnknown then IModalWindow then
# IFileDialog, so the indexes are fixed by that inheritance chain.
_RELEASE = 2
_SHOW = 3
_SET_OPTIONS = 9
_GET_OPTIONS = 10
_SET_TITLE = 17
_GET_RESULT = 20

# IShellItem: QueryInterface, AddRef, Release, BindToHandler, GetParent,
# GetDisplayName, ...
_ITEM_RELEASE = 2
_ITEM_GET_DISPLAY_NAME = 5


def available() -> bool:
    """True if a native dialog can be shown here."""
    return sys.platform == "win32"


def _guid(text: str):
    """Build a GUID structure from its canonical string form."""
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    guid = GUID()
    if ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid)) != _S_OK:
        raise PickerUnavailable(f"could not parse the identifier {text}")
    return guid


def _call(interface, slot: int, argtypes=(), *args) -> int:
    """Invoke one method on a COM interface pointer through its vtable.

    Two details that are easy to get wrong and fail as an access violation
    rather than an exception:

    * **Double indirection.** The interface pointer points at an object whose
      *first field* is the vtable pointer. Reading the object's own memory as
      the vtable calls whatever happens to be there.
    * **Explicit argument types.** ``ctypes.byref(x)`` is a ``CArgObject``, not
      a ctypes type, so argument types cannot be inferred from the values —
      each call site declares them.
    """
    import ctypes

    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    signature = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return signature(vtable[slot])(interface, *args)


def choose_folder(title: str = "Choose a workbook folder") -> str | None:
    """Show the shell folder picker. Returns the chosen path, or None if cancelled.

    Blocks until the person dismisses the dialog, so call it off the event loop —
    FastAPI does that automatically for a route defined with ``def`` rather than
    ``async def``.
    """
    if not available():
        raise PickerUnavailable("a native folder dialog is only available on Windows")

    if not _lock.acquire(blocking=False):
        raise PickerBusy("a folder dialog is already open")
    try:
        return _show(title)
    finally:
        _lock.release()


def _show(title: str) -> str | None:
    import ctypes

    ole32 = ctypes.oledll.ole32

    # The dialog is modal and wants a single-threaded apartment. A worker thread
    # of the server has none until we say so.
    initialised = False
    result = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if result == _S_OK:
        initialised = True
    elif result != _RPC_E_CHANGED_MODE and result < 0:
        raise PickerUnavailable(f"could not initialise COM (0x{result & 0xFFFFFFFF:08x})")

    dialog = ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(
            ctypes.byref(_guid("{DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7}")),  # FileOpenDialog
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(_guid("{d57c7288-d4ad-4768-be02-9d969532d960}")),  # IFileOpenDialog
            ctypes.byref(dialog),
        )
    except OSError as exc:
        if initialised:
            ctypes.windll.ole32.CoUninitialize()
        raise PickerUnavailable(f"the shell would not provide a dialog ({exc})") from exc

    try:
        options = ctypes.c_uint32()
        _call(dialog, _GET_OPTIONS, (ctypes.POINTER(ctypes.c_uint32),), ctypes.byref(options))
        # Folders only, and only ones with a real path we can hand back — a
        # library or a phone under "This PC" has no filesystem path at all.
        _call(
            dialog,
            _SET_OPTIONS,
            (ctypes.c_uint32,),
            ctypes.c_uint32(
                options.value | _FOS_PICKFOLDERS | _FOS_FORCEFILESYSTEM | _FOS_PATHMUSTEXIST
            ),
        )
        _call(dialog, _SET_TITLE, (ctypes.c_wchar_p,), ctypes.c_wchar_p(title))

        shown = _call(dialog, _SHOW, (ctypes.c_void_p,), ctypes.c_void_p(0))
        if shown != _S_OK:
            # Cancelled. Every other failure looks the same from here and means
            # the same thing to the caller: no path was chosen.
            return None

        item = ctypes.c_void_p()
        got = _call(dialog, _GET_RESULT, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(item))
        if got != _S_OK or not item:
            return None
        try:
            buffer = ctypes.c_wchar_p()
            named = _call(
                item,
                _ITEM_GET_DISPLAY_NAME,
                (ctypes.c_uint32, ctypes.POINTER(ctypes.c_wchar_p)),
                ctypes.c_uint32(_SIGDN_FILESYSPATH),
                ctypes.byref(buffer),
            )
            if named != _S_OK:
                return None
            path = buffer.value
            if buffer:
                ctypes.windll.ole32.CoTaskMemFree(buffer)
            return path
        finally:
            _call(item, _ITEM_RELEASE)
    finally:
        _call(dialog, _RELEASE)
        if initialised:
            ctypes.windll.ole32.CoUninitialize()
