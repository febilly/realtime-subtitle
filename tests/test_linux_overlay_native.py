import ctypes
from unittest.mock import MagicMock

from realtime_subtitle.outputs import linux_overlay_native, overlay_window


def test_linux_set_click_through_disabled_off_linux(monkeypatch):
    monkeypatch.setattr(linux_overlay_native, "IS_LINUX", False)
    assert linux_overlay_native.set_click_through(12345, True) is False


def test_linux_set_click_through_zero_window_id(monkeypatch):
    monkeypatch.setattr(linux_overlay_native, "IS_LINUX", True)
    assert linux_overlay_native.set_click_through(0, True) is False


def test_linux_set_click_through_missing_x11_returns_false(monkeypatch):
    monkeypatch.setattr(linux_overlay_native, "IS_LINUX", True)
    monkeypatch.setattr(linux_overlay_native, "_load_x11_shape", lambda: None)
    assert linux_overlay_native.set_click_through(12345, True) is False


def test_linux_set_click_through_toggle_calls_xshape(monkeypatch):
    monkeypatch.setattr(linux_overlay_native, "IS_LINUX", True)

    libx11 = MagicMock()
    libx11.XOpenDisplay.return_value = 0xDEAD
    libxext = MagicMock()

    def _query_ext(_dpy, ev_ptr, err_ptr):
        ctypes.cast(ev_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 64
        ctypes.cast(err_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 0
        return 1

    libxext.XShapeQueryExtension.side_effect = _query_ext
    monkeypatch.setattr(linux_overlay_native, "_load_x11_shape", lambda: (libx11, libxext))

    assert linux_overlay_native.set_click_through(0x1234, True) is True
    assert libxext.XShapeCombineRectangles.call_count == 1
    rect_args = libxext.XShapeCombineRectangles.call_args[0]
    assert rect_args[0] == 0xDEAD
    assert rect_args[1].value == 0x1234
    assert rect_args[2] == linux_overlay_native._SHAPE_INPUT
    assert rect_args[6] == 0

    assert linux_overlay_native.set_click_through(0x1234, False) is True
    assert libxext.XShapeCombineMask.call_count == 1
    mask_args = libxext.XShapeCombineMask.call_args[0]
    assert mask_args[0] == 0xDEAD
    assert mask_args[1].value == 0x1234
    assert mask_args[2] == linux_overlay_native._SHAPE_INPUT
    assert mask_args[5] == 0
    assert libx11.XFlush.call_count == 2
    assert libx11.XCloseDisplay.call_count == 2


def test_configure_qt_platform_prefers_xcb_when_display_present(monkeypatch):
    monkeypatch.setattr(overlay_window.sys, "platform", "linux")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("DISPLAY", ":1")
    overlay_window._configure_qt_platform()
    assert overlay_window.os.environ.get("QT_QPA_PLATFORM") == "xcb"


def test_configure_qt_platform_preserves_existing_env(monkeypatch):
    monkeypatch.setattr(overlay_window.sys, "platform", "linux")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("DISPLAY", ":1")
    overlay_window._configure_qt_platform()
    assert overlay_window.os.environ.get("QT_QPA_PLATFORM") == "offscreen"


def test_configure_qt_platform_skips_without_display(monkeypatch):
    monkeypatch.setattr(overlay_window.sys, "platform", "linux")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    overlay_window._configure_qt_platform()
    assert "QT_QPA_PLATFORM" not in overlay_window.os.environ


def test_system_font_stack_includes_linux_cjk():
    assert "Noto Sans CJK SC" in overlay_window.SYSTEM_FONT_STACK
    assert "Noto Sans CJK JP" in overlay_window.SYSTEM_FONT_STACK


def test_linux_set_click_through_with_interactive_rects(monkeypatch):
    monkeypatch.setattr(linux_overlay_native, "IS_LINUX", True)
    libx11 = MagicMock()
    libx11.XOpenDisplay.return_value = 0xCAFE
    libxext = MagicMock()

    def _query_ext(_dpy, ev_ptr, err_ptr):
        ctypes.cast(ev_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 64
        ctypes.cast(err_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 0
        return 1

    libxext.XShapeQueryExtension.side_effect = _query_ext
    monkeypatch.setattr(linux_overlay_native, "_load_x11_shape", lambda: (libx11, libxext))

    assert linux_overlay_native.set_click_through(
        0x5678, True, interactive_rects=[(10, 20, 24, 24), (0, 0, 0, 5)]
    ) is True
    rect_args = libxext.XShapeCombineRectangles.call_args[0]
    assert rect_args[6] == 1
    assert rect_args[5][0].x == 10
    assert rect_args[5][0].y == 20
    assert rect_args[5][0].width == 24
    assert rect_args[5][0].height == 24


def test_server_configure_linux_webview_backend(monkeypatch):
    import server

    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.setenv("DISPLAY", ":1")
    server._configure_linux_webview_backend()
    assert server.os.environ.get("GDK_BACKEND") == "x11"

    monkeypatch.setenv("GDK_BACKEND", "wayland")
    server._configure_linux_webview_backend()
    assert server.os.environ.get("GDK_BACKEND") == "wayland"


def test_linux_webview_window_size_scales_with_xft_dpi(monkeypatch):
    import types
    import server

    monkeypatch.setattr(server.sys, "platform", "linux")

    fake_geom = types.SimpleNamespace(width=1920, height=1080)
    fake_monitor = types.SimpleNamespace(
        get_scale_factor=lambda: 1,
        get_geometry=lambda: fake_geom,
    )
    fake_display = types.SimpleNamespace(
        get_primary_monitor=lambda: fake_monitor,
        get_n_monitors=lambda: 1,
        get_monitor=lambda idx: fake_monitor,
    )
    fake_screen = types.SimpleNamespace(get_resolution=lambda: 144.0)
    fake_gtk = types.SimpleNamespace(init_check=lambda argv: (True, argv))
    fake_gdk = types.SimpleNamespace(
        Screen=types.SimpleNamespace(get_default=lambda: fake_screen),
        Display=types.SimpleNamespace(get_default=lambda: fake_display),
    )
    fake_gi_repo = types.ModuleType("gi.repository")
    fake_gi_repo.Gtk = fake_gtk
    fake_gi_repo.Gdk = fake_gdk
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *args: None
    fake_gi.repository = fake_gi_repo

    monkeypatch.setitem(server.sys.modules, "gi", fake_gi)
    monkeypatch.setitem(server.sys.modules, "gi.repository", fake_gi_repo)

    assert server._linux_webview_window_size(350, 600) == (525, 900)

    monkeypatch.setattr(server.sys, "platform", "win32")
    assert server._linux_webview_window_size(350, 600) == (350, 600)


def test_overlay_window_uses_x11_bypass_flag_only_on_linux_xcb(monkeypatch):
    import os
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    assert app is not None

    monkeypatch.setattr(overlay_window.OverlayWindow, "_is_linux_xcb", staticmethod(lambda: True))
    win_xcb = overlay_window.OverlayWindow("http://127.0.0.1:18999")
    try:
        assert bool(win_xcb.windowFlags() & Qt.X11BypassWindowManagerHint) is True
    finally:
        win_xcb.close()
        win_xcb.deleteLater()

    monkeypatch.setattr(overlay_window.OverlayWindow, "_is_linux_xcb", staticmethod(lambda: False))
    win_other = overlay_window.OverlayWindow("http://127.0.0.1:18999")
    try:
        assert bool(win_other.windowFlags() & Qt.X11BypassWindowManagerHint) is False
    finally:
        win_other.close()
        win_other.deleteLater()


def test_appimage_uses_writable_xdg_dirs_for_models_and_logs(tmp_path, monkeypatch):
    from pathlib import Path
    from realtime_subtitle.core import logger as logger_mod
    from realtime_subtitle.local_inference import model_manager

    monkeypatch.setattr(model_manager.sys, "platform", "linux")
    monkeypatch.setattr(model_manager.sys, "frozen", True, raising=False)
    monkeypatch.delenv("REALTIME_SUBTITLE_LOCAL_MODELS_DIR", raising=False)
    monkeypatch.setenv("APPIMAGE", "/tmp/RealtimeSubtitle-x86_64.AppImage")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    assert model_manager._default_models_dir() == Path(tmp_path / "xdg_data" / "RealtimeSubtitle" / "local_models")

    monkeypatch.setattr(logger_mod.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    log_inst = logger_mod.TranscriptLogger(enabled=True)
    log_path = log_inst.init_log_file()
    try:
        assert log_path is not None
        assert Path(log_path).parent == Path(tmp_path / "xdg_config" / "RealtimeSubtitle" / "logs")
    finally:
        log_inst.close_log_file()
