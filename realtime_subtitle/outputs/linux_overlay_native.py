"""Linux/X11 native helpers for the subtitle overlay window."""

from __future__ import annotations

import ctypes
import sys

IS_LINUX = sys.platform.startswith("linux")

_SHAPE_SET = 0
_SHAPE_INPUT = 2
_SHAPE_UNSORTED = 0

_x11_libs = None
_x11_unavailable = False


class _XRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
    ]


def _load_x11_shape():
    """Bind libX11 + libXext XShape functions once; returns None if unavailable."""
    global _x11_libs, _x11_unavailable
    if _x11_unavailable:
        return None
    if _x11_libs is not None:
        return _x11_libs

    try:
        libx11 = ctypes.CDLL("libX11.so.6")
        libxext = ctypes.CDLL("libXext.so.6")

        libx11.XOpenDisplay.restype = ctypes.c_void_p
        libx11.XOpenDisplay.argtypes = (ctypes.c_char_p,)
        libx11.XCloseDisplay.restype = ctypes.c_int
        libx11.XCloseDisplay.argtypes = (ctypes.c_void_p,)
        libx11.XFlush.restype = ctypes.c_int
        libx11.XFlush.argtypes = (ctypes.c_void_p,)

        libxext.XShapeQueryExtension.restype = ctypes.c_int
        libxext.XShapeQueryExtension.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        )
        libxext.XShapeCombineRectangles.restype = None
        libxext.XShapeCombineRectangles.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(_XRectangle),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        )
        libxext.XShapeCombineMask.restype = None
        libxext.XShapeCombineMask.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_int,
        )
    except OSError:
        _x11_unavailable = True
        return None

    _x11_libs = (libx11, libxext)
    return _x11_libs


def set_click_through(
    window_id: int,
    enabled: bool,
    interactive_rects: list[tuple[int, int, int, int]] | None = None,
) -> bool:
    """Toggle X11 ShapeInput click-through on a window.

    When ``enabled`` is True and ``interactive_rects`` is provided, only those
    window-local ``(x, y, width, height)`` rectangles continue receiving mouse
    events while the rest of the window passes clicks through. Returns True when
    the X11 shape request was dispatched.
    """
    if not IS_LINUX or not window_id:
        return False

    libs = _load_x11_shape()
    if libs is None:
        return False
    libx11, libxext = libs

    try:
        display = libx11.XOpenDisplay(None)
    except Exception:
        return False
    if not display:
        return False

    try:
        event_base = ctypes.c_int()
        error_base = ctypes.c_int()
        if not libxext.XShapeQueryExtension(
            display, ctypes.byref(event_base), ctypes.byref(error_base)
        ):
            return False

        if enabled:
            valid_rects = [
                (int(x), int(y), int(w), int(h))
                for x, y, w, h in (interactive_rects or [])
                if int(w) > 0 and int(h) > 0
            ]
            if valid_rects:
                rect_array = (_XRectangle * len(valid_rects))(
                    *(_XRectangle(x, y, w, h) for x, y, w, h in valid_rects)
                )
                rect_ptr = ctypes.cast(rect_array, ctypes.POINTER(_XRectangle))
                rect_count = len(valid_rects)
            else:
                rect_ptr = ctypes.POINTER(_XRectangle)()
                rect_count = 0

            libxext.XShapeCombineRectangles(
                display,
                ctypes.c_ulong(window_id),
                _SHAPE_INPUT,
                0,
                0,
                rect_ptr,
                rect_count,
                _SHAPE_SET,
                _SHAPE_UNSORTED,
            )
        else:
            libxext.XShapeCombineMask(
                display,
                ctypes.c_ulong(window_id),
                _SHAPE_INPUT,
                0,
                0,
                0,
                _SHAPE_SET,
            )
        libx11.XFlush(display)
        return True
    except Exception:
        return False
    finally:
        try:
            libx11.XCloseDisplay(display)
        except Exception:
            pass
