import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import audio_capture
import audio_preferences as prefs
import local_store
import window_audio
from realtime_session_base import RealtimeSessionBase


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(local_store, "_path", lambda: str(tmp_path / "settings.json"))


def test_only_opted_in_settings_survive_restart_and_uncheck_removes_them(store):
    settings = prefs.defaults() | {"microphone_enabled": True, "remember": True, "microphone_device_id": "usb"}
    prefs.save(settings)
    assert prefs.load() == settings
    prefs.save(settings | {"remember": False})
    assert prefs.load() == prefs.defaults()
    assert prefs.STORE_KEY not in local_store.load()


def test_window_restore_requires_unique_executable_and_title_not_old_pid(monkeypatch, store):
    target = dict(hwnd="11", pid=12, created="100", executable="C:\\player.exe", title="Player")
    prefs.save(prefs.defaults() | {"desktop_mode": "window", "window": target, "remember": True})
    next_window = target | dict(hwnd="22", pid=24, created="200")
    monkeypatch.setattr(window_audio, "list_windows", lambda: [next_window])
    assert prefs.load()["window"] == next_window
    assert window_audio.resolve_window(target) is None
    monkeypatch.setattr(window_audio, "list_windows", lambda: [next_window, next_window | dict(hwnd="33")])
    assert window_audio.resolve_window(target, restore=True) is None
    # Keep the unavailable window target so capture stays isolated, never full desktop.
    assert prefs.load()["desktop_mode"] == "window"
    assert prefs.load()["window"] == target


def test_rejects_all_disabled_and_stale_window_before_persisting(monkeypatch, store):
    with pytest.raises(ValueError, match="At least one"):
        prefs.normalize(prefs.defaults() | {"desktop_enabled": False})
    monkeypatch.setattr(window_audio, "supported", lambda: True)
    monkeypatch.setattr(window_audio, "list_windows", lambda: [])
    with pytest.raises(ValueError, match="window is unavailable"):
        prefs.normalize(prefs.defaults() | {"desktop_mode": "window", "window": {"pid": 42}})
    assert prefs.load() == prefs.defaults()


def test_session_applies_whole_selection_and_saves_only_on_request(monkeypatch, store):
    session = RealtimeSessionBase.__new__(RealtimeSessionBase)
    session.audio_lock = threading.Lock()
    session._audio_capture_settings = prefs.defaults()
    session.audio_source = "system"
    session.output_device_id = session.microphone_device_id = ""
    applied = []
    session.audio_streamer = SimpleNamespace(configure_capture=applied.append)
    monkeypatch.setattr("realtime_session_base.USE_TWITCH_AUDIO_STREAM", False)
    value = prefs.defaults() | {"microphone_enabled": True, "remember": True}
    assert session.set_audio_capture_settings(value)[0]
    assert applied == [value]
    assert session.get_audio_source() == "mix"
    assert local_store.load() == {}
    assert session.set_audio_capture_settings(value, persist=True)[0]
    assert json.loads(local_store.load()[prefs.STORE_KEY]) == value
    assert not session.set_audio_capture_settings(value | {"desktop_enabled": False, "microphone_enabled": False}, persist=True)[0]
    assert session.get_audio_source() == "mix"


def test_missing_window_returns_paced_silence_and_never_opens_output_loopback(monkeypatch):
    monkeypatch.setattr(window_audio, "list_windows", lambda: [])
    monkeypatch.setattr(audio_capture, "sc", SimpleNamespace(default_speaker=lambda: pytest.fail("must not capture whole desktop")))
    streamer = audio_capture.AudioStreamer(SimpleNamespace(send=lambda _: None), desktop_mode="window", window={"pid": 42})
    with streamer._create_recorder("system") as recorder:
        assert np.count_nonzero(recorder.record(160)) == 0
    assert streamer.capture_error == "Selected audio window is unavailable"


def test_apply_during_recorder_replacement_is_not_lost(monkeypatch, store):
    session = RealtimeSessionBase.__new__(RealtimeSessionBase)
    session.audio_lock = threading.Lock()
    session._audio_capture_settings = prefs.defaults()
    session.audio_source = "system"
    session.output_device_id = session.microphone_device_id = ""
    session.audio_streamer = None
    session.sample_rate, session.chunk_size = 16000, 1600
    session._vrchat_self_muted = False
    next_settings = prefs.defaults() | {"microphone_enabled": True, "desktop_enabled": False}

    class Recorder:
        def __init__(self, *args, **kwargs):
            assert kwargs["initial_source"] == "system"
            assert session.set_audio_capture_settings(next_settings)[0]

        def set_vrchat_mic_muted(self, _):
            pass

        def configure_capture(self, settings):
            self.settings = settings

        def start(self):
            assert self.settings == next_settings

    monkeypatch.setattr("realtime_session_base.USE_TWITCH_AUDIO_STREAM", False)
    monkeypatch.setattr("realtime_session_base.AudioStreamer", Recorder)
    session._start_audio_streamer(SimpleNamespace())
    assert session.audio_streamer.settings == next_settings


@pytest.mark.parametrize("value", [None, [], {}, {"desktop_enabled": 1}, prefs.defaults() | {"desktop_mode": "unknown"}])
def test_rejects_malformed_settings(value):
    with pytest.raises(ValueError):
        prefs.normalize(value)
