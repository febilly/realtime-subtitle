import itertools
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


def test_window_restore_prefers_title_then_path_and_takes_first_match(monkeypatch, store):
    target = dict(hwnd="11", pid=12, created="100", executable="C:\\player.exe", title="Player")
    prefs.save(prefs.defaults() | {"desktop_mode": "window", "window": target, "remember": True})
    next_window = target | dict(hwnd="22", pid=24, created="200")
    monkeypatch.setattr(window_audio, "list_windows", lambda: [next_window])
    assert prefs.load()["window"] == next_window
    assert window_audio.resolve_window(target) is None
    changed_title = next_window | dict(hwnd="33", title="Another title", executable="c:\\PLAYER.EXE")
    monkeypatch.setattr(window_audio, "list_windows", lambda: [changed_title, next_window, next_window | dict(hwnd="44")])
    assert prefs.load()["window"] == next_window
    monkeypatch.setattr(window_audio, "list_windows", lambda: [changed_title, changed_title | dict(hwnd="55")])
    assert prefs.load()["window"] == changed_title
    monkeypatch.setattr(window_audio, "list_windows", lambda: [next_window | dict(executable="C:\\other.exe")])
    assert prefs.load()["desktop_mode"] == "window"
    assert prefs.load()["window"] is None
    # Opening the selection prompt does not discard the remembered preference.
    assert json.loads(local_store.load()[prefs.STORE_KEY])["window"] == target


def test_live_window_title_change_keeps_same_process_before_restore_candidates(monkeypatch):
    target = dict(hwnd="11", pid=12, created="100", executable="C:\\player.exe", title="Player")
    renamed = target | dict(title="New title")
    other = target | dict(hwnd="22", pid=24, created="200")
    monkeypatch.setattr(window_audio, "list_windows", lambda: [other, renamed])
    streamer = audio_capture.AudioStreamer(SimpleNamespace(), desktop_mode="window", window=target)
    assert streamer._device_signature("system") == "12:100"
    assert streamer.get_window() == renamed


def test_missing_live_window_waits_then_recovers_by_path_and_refreshes_session(monkeypatch):
    target = dict(hwnd="11", pid=12, created="100", executable="C:\\player.exe", title="Player")
    windows = []
    monkeypatch.setattr(window_audio, "list_windows", lambda: windows)
    streamer = audio_capture.AudioStreamer(SimpleNamespace(), desktop_mode="window", window=target)
    assert streamer._device_signature("system") == "missing-window"
    assert streamer.get_window() == target
    assert streamer.capture_error
    next_window = target | dict(hwnd="22", pid=24, created="200", title="New title")
    windows.extend([next_window, next_window | dict(hwnd="33", pid=36, created="300")])
    assert streamer._device_signature("system") == "24:200"
    assert streamer.get_window() == next_window
    assert streamer.capture_error == ""
    recorders = []
    monkeypatch.setattr(window_audio, "ProcessRecorder", lambda selection, *args, **kwargs: recorders.append(selection))
    streamer._create_recorder("system")
    assert recorders == [next_window]

    session = RealtimeSessionBase.__new__(RealtimeSessionBase)
    session.audio_lock = threading.Lock()
    session._audio_capture_settings = prefs.defaults() | {"desktop_mode": "window", "window": target}
    session.audio_source = "system"
    session.output_device_id = session.microphone_device_id = ""
    session.audio_streamer = streamer
    assert session.get_audio_capture_settings()["window"] == next_window


def test_capture_recovery_does_not_overwrite_a_concurrent_manual_selection(monkeypatch):
    target = dict(hwnd="11", pid=12, created="100", executable="C:\\player.exe", title="Player")
    manual = target | dict(hwnd="22", pid=24, created="200", executable="C:\\other.exe")
    streamer = audio_capture.AudioStreamer(SimpleNamespace(), desktop_mode="window", window=target)

    def resolve(selection, **kwargs):
        streamer.configure_capture(prefs.defaults() | {"desktop_mode": "window", "window": manual})
        return target

    monkeypatch.setattr(window_audio, "resolve_window", resolve)
    assert streamer._resolve_capture_window() is None
    assert streamer.get_window() == manual


def test_capture_loop_reopens_recorder_when_the_missing_process_returns(monkeypatch):
    target = dict(hwnd="11", pid=12, created="100", executable="C:\\player.exe", title="Player")
    returned = target | dict(hwnd="22", pid=24, created="200", title="New title")
    windows, payloads = [], []
    monkeypatch.setattr(window_audio, "list_windows", lambda: windows)
    clock = itertools.count(step=0.6)
    monkeypatch.setattr(audio_capture, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None))

    def send(payload):
        payloads.append(payload)
        windows[:] = [returned]

    streamer = audio_capture.AudioStreamer(SimpleNamespace(send=send), desktop_mode="window", window=target)
    streamer._run_single_source_mode("system")
    assert payloads and not np.frombuffer(payloads[0], dtype=np.int16).any()
    assert streamer._source_changed_event.is_set()

    class Recorder:
        def __init__(self, selection, *args, **kwargs):
            assert selection == returned

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def record(self, numframes):
            streamer._stop_event.set()
            return np.ones(numframes, dtype=np.float32)

    monkeypatch.setattr(window_audio, "ProcessRecorder", Recorder)
    streamer._source_changed_event.clear()
    streamer._run_single_source_mode("system")
    assert np.frombuffer(payloads[-1], dtype=np.int16).any()
    assert streamer.capture_error == ""


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
