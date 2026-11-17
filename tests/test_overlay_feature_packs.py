import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import realtime_subtitle.outputs.overlay_window as overlay_module
from realtime_subtitle.outputs.overlay_window import OverlayWindow

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_overlay(tmp_path, monkeypatch):
    ini_path = str(tmp_path / "overlay.ini")
    seed = QSettings(ini_path, QSettings.Format.IniFormat)
    seed.setValue("furigana_enabled", True)
    seed.sync()

    monkeypatch.setattr(
        overlay_module,
        "QSettings",
        lambda *_args, **_kwargs: QSettings(ini_path, QSettings.Format.IniFormat),
    )
    monkeypatch.setattr(OverlayWindow, "_init_ws", lambda self: None)

    window = OverlayWindow("http://127.0.0.1:1")
    yield window, ini_path
    window._hover_timer.stop()
    window.deleteLater()


def test_learning_pack_gates_overlay_furigana_and_preserves_setting(isolated_overlay):
    window, ini_path = isolated_overlay

    # Initial user preference was True and pack starts enabled
    assert window.furigana_enabled is True
    assert window.furigana.is_enabled() is True
    assert window.btn_furigana.isHidden() is False

    # Disabling the learning pack hides the button, disables furigana runtime, and keeps QSettings intact
    window.set_learning_pack_enabled(False)
    assert window.btn_furigana.isHidden() is True
    assert window.furigana_enabled is False
    assert window.furigana.is_enabled() is False

    stored = QSettings(ini_path, QSettings.Format.IniFormat)
    assert str(stored.value("furigana_enabled")).lower() == "true"

    # Clicking/toggling furigana while learning pack is disabled is a no-op
    window._toggle_furigana()
    assert window.furigana_enabled is False
    assert window._user_furigana_enabled is True

    # Re-enabling the learning pack restores button visibility and user's furigana preference
    window.set_learning_pack_enabled(True)
    assert window.btn_furigana.isHidden() is False
    assert window.furigana_enabled is True
    assert window.furigana.is_enabled() is True


def test_websocket_feature_packs_frame_updates_learning_pack(isolated_overlay):
    window, _ = isolated_overlay
    assert window.furigana_enabled is True

    window._on_message(json.dumps({
        "type": "feature_packs",
        "packs": {"vrchat": False, "learning": False},
    }))
    assert window.btn_furigana.isHidden() is True
    assert window.furigana_enabled is False

    window._on_message(json.dumps({
        "type": "feature_packs",
        "packs": {"vrchat": False, "learning": True},
    }))
    assert window.btn_furigana.isHidden() is False
    assert window.furigana_enabled is True
