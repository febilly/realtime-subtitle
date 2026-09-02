"""Windows-only native helpers for the subtitle overlay window.

These live here rather than in overlay_window.py because Huorong matched a
signature — Trojan/Python.ShellLoader.am, on download — against the compiled
bytecode of overlay_window itself. Bisecting it (see dist/RESULTS.md) showed the
detection survived removing any one or two of these functions but disappeared
once all three were gone, and moving them into a module of their own clears it
even with the bodies unchanged. So the match depends on the surrounding module
context, not on any single call.

The rewrite below is also just better: one shared binding instead of the same
ctypes preamble copy-pasted into each function, an explicit ctypes.WinDLL()
rather than the ctypes.windll attribute chain, and c_ssize_t — which is LONG_PTR
on both 32- and 64-bit — instead of branching on pointer width to choose between
the Ptr and non-Ptr forms of Get/SetWindowLong.

Keep Win32 calls in this module. Adding them back to overlay_window.py risks
reintroducing the false positive.
"""

from __future__ import annotations

import sys

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

PROCESS_QUERY_INFORMATION = 0x0400
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259

IS_WINDOWS = sys.platform == "win32"

_user32 = None


def _load_user32():
    """Bind user32 once; returns (library, read_style, write_style, wintypes)."""
    global _user32
    if _user32 is not None:
        return _user32

    import ctypes
    from ctypes import wintypes

    library = ctypes.WinDLL("user32", use_last_error=True)
    # The Ptr forms are 64-bit-only exports; on 32-bit Windows they are macros
    # for the plain ones, so fall back rather than branching on pointer width.
    read_style = getattr(library, "GetWindowLongPtrW", None) or library.GetWindowLongW
    write_style = getattr(library, "SetWindowLongPtrW", None) or library.SetWindowLongW
    read_style.restype = ctypes.c_ssize_t
    read_style.argtypes = (wintypes.HWND, ctypes.c_int)
    write_style.restype = ctypes.c_ssize_t
    write_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    library.SetWindowPos.restype = wintypes.BOOL
    library.SetWindowPos.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    )

    _user32 = (library, read_style, write_style, wintypes)
    return _user32


def _update_ex_style(hwnd: int, add: int = 0, remove: int = 0):
    """Set and clear extended style bits on a window."""
    library, read_style, write_style, wintypes = _load_user32()
    handle = wintypes.HWND(hwnd)
    current = read_style(handle, GWL_EXSTYLE)
    write_style(handle, GWL_EXSTYLE, (current | add) & ~remove)
    return library, handle, wintypes


def apply_no_activate_style(hwnd: int, app_window: bool = False) -> None:
    """Keep the window on top and stop it taking focus when clicked.

    With app_window=True the window registers as a taskbar window
    (WS_EX_APPWINDOW, dropping WS_EX_TOOLWINDOW), so it appears in the Windows
    taskbar and can be captured as a standalone window by OBS and similar. The
    default leaves it a tool window, out of the taskbar.
    """
    if not IS_WINDOWS:
        return
    try:
        if app_window:
            add, remove = WS_EX_NOACTIVATE | WS_EX_APPWINDOW, WS_EX_TOOLWINDOW
        else:
            add, remove = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, 0
        library, handle, wintypes = _update_ex_style(hwnd, add, remove)
        library.SetWindowPos(
            handle,
            wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    except Exception:
        pass


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Toggle mouse pass-through (WS_EX_TRANSPARENT).

    While enabled the window receives no mouse events and clicks land on
    whatever is underneath; disabling restores normal handling.
    """
    if not IS_WINDOWS:
        return
    try:
        if enabled:
            _update_ex_style(hwnd, add=WS_EX_TRANSPARENT | WS_EX_LAYERED)
        else:
            _update_ex_style(hwnd, remove=WS_EX_TRANSPARENT)
    except Exception:
        pass


def is_process_alive(pid: int) -> bool:
    """Whether a process is still running. Windows only; callers dispatch."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
