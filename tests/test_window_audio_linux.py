import json
import os
import subprocess
from types import SimpleNamespace

import numpy as np

from realtime_subtitle.audio import window_audio


def test_supported_linux_requires_pactl_and_parec(monkeypatch):
    monkeypatch.setattr(window_audio.sys, "platform", "linux")
    monkeypatch.setattr(window_audio.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    assert window_audio.supported() is True

    monkeypatch.setattr(
        window_audio.shutil,
        "which",
        lambda cmd: "/usr/bin/pactl" if cmd == "pactl" else None,
    )
    assert window_audio.supported() is False


def test_list_windows_linux_parses_sink_inputs_and_skips_helpers(monkeypatch):
    monkeypatch.setattr(window_audio.sys, "platform", "linux")
    monkeypatch.setattr(window_audio.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    sample_inputs = [
        {
            "index": 12,
            "properties": {
                "application.process.id": str(os.getpid()),
                "application.name": "SelfProcess",
                "media.name": "loop",
            },
        },
        {
            "index": 13,
            "properties": {
                "application.process.id": "99901",
                "application.name": "pacat",
                "application.process.binary": "/usr/bin/parec",
                "media.name": "Record Stream",
            },
        },
        {
            "index": 14,
            "properties": {
                "application.process.id": "99902",
                "application.name": "Firefox",
                "application.process.binary": "firefox",
                "media.name": "YouTube",
            },
        },
    ]

    monkeypatch.setattr(
        window_audio.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(sample_inputs)),
    )
    monkeypatch.setattr(
        window_audio,
        "_linux_process_identity",
        lambda pid, binary, idx: (f"/usr/lib/{binary}", "123456"),
    )

    windows = window_audio.list_windows()
    assert windows == [
        {
            "hwnd": "14",
            "pid": 99902,
            "title": "Firefox - YouTube",
            "executable": "/usr/lib/firefox",
            "created": "123456",
        }
    ]


def test_process_recorder_linux_reads_parec_stream(monkeypatch):
    monkeypatch.setattr(window_audio.sys, "platform", "linux")
    monkeypatch.setattr(window_audio.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    target = {
        "hwnd": "42",
        "pid": 5000,
        "title": "Firefox - Video",
        "executable": "/usr/bin/firefox",
        "created": "9876",
    }
    monkeypatch.setattr(window_audio, "resolve_window", lambda sel, restore=False: target)

    expected = np.linspace(-0.5, 0.5, 800, dtype=np.float32)
    r_fd, w_fd = os.pipe()
    os.write(w_fd, expected.tobytes())
    os.close(w_fd)

    popen_calls = []

    class FakeProc:
        def __init__(self, cmd):
            popen_calls.append(cmd)
            self.stdout = os.fdopen(r_fd, "rb", buffering=0)
            self._terminated = False

        def poll(self):
            return 0 if self._terminated else None

        def terminate(self):
            self._terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self._terminated = True

    monkeypatch.setattr(
        window_audio.subprocess,
        "Popen",
        lambda cmd, **kwargs: FakeProc(cmd),
    )

    with window_audio.ProcessRecorder(target, sample_rate=16000) as recorder:
        data = recorder.record(800)

    assert len(popen_calls) == 1
    assert "--monitor-stream=42" in popen_calls[0]
    assert data.shape == (800,)
    assert data.dtype == np.float32
    np.testing.assert_allclose(data, expected)
