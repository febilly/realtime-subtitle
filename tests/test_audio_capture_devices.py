import queue
import threading
import time
from types import SimpleNamespace

import numpy as np

import audio_capture


class FakeSoundcard:
    def __init__(self):
        self.default_output = SimpleNamespace(id="out-default", name="Default output")
        self.default_input = SimpleNamespace(id="mic-default", name="Default mic")
        self.speakers = {
            "out-default": self.default_output,
            "out-usb": SimpleNamespace(id="out-usb", name="USB output"),
        }
        self.microphones = {
            "mic-default": self.default_input,
            "mic-usb": SimpleNamespace(id="mic-usb", name="USB mic"),
        }

    def default_speaker(self):
        return self.default_output

    def default_microphone(self):
        return self.default_input

    def all_speakers(self):
        return list(self.speakers.values())

    def get_speaker(self, id):
        return self.speakers.get(id)

    def get_microphone(self, id, include_loopback=False):
        return self.microphones.get(id)


class FakeRecorder:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class OverlappingLoopbackSoundcard:
    """Mimic soundcard's name-substring lookup with two similarly named outputs."""

    def __init__(self):
        self.default_output = SimpleNamespace(id="wasapi-headphones", name="耳机 (2- HECATE G2 Pro)")
        self.communication_output = SimpleNamespace(id="wasapi-headset", name="头戴式耳机 (2- HECATE G2 Pro)")
        self.loopbacks = {
            self.default_output.id: SimpleNamespace(id=self.default_output.id, recorder=lambda **kwargs: FakeRecorder()),
            self.communication_output.id: SimpleNamespace(id=self.communication_output.id, recorder=lambda **kwargs: FakeRecorder()),
        }
        self.microphone_lookups = []

    def default_speaker(self):
        return self.default_output

    def get_microphone(self, id, include_loopback=False):
        self.microphone_lookups.append((id, include_loopback))
        exact_match = self.loopbacks.get(id)
        if exact_match is not None:
            return exact_match
        # soundcard's name-substring fallback would return the communication
        # endpoint because "耳机" is contained in "头戴式耳机".
        if include_loopback and id == self.default_output.name:
            return self.loopbacks[self.communication_output.id]
        return None


def test_specific_devices_fall_back_and_clear_selection(monkeypatch):
    fake = FakeSoundcard()
    monkeypatch.setattr(audio_capture, "sc", fake)
    streamer = audio_capture.AudioStreamer(
        SimpleNamespace(send=lambda payload: None),
        microphone_device_id="missing-mic",
        output_device_id="missing-output",
    )

    assert streamer._resolve_microphone_device() is fake.default_input
    assert streamer._resolve_output_device() is fake.default_output
    assert streamer.get_microphone_device_id() == ""
    assert streamer.get_output_device_id() == ""


def test_default_signature_tracks_current_default_without_binding(monkeypatch):
    fake = FakeSoundcard()
    monkeypatch.setattr(audio_capture, "sc", fake)
    streamer = audio_capture.AudioStreamer(SimpleNamespace(send=lambda payload: None))

    assert streamer._device_signature("system") == "out-default"
    fake.default_output = SimpleNamespace(id="out-next", name="Next default")
    assert streamer._device_signature("system") == "out-next"
    assert streamer.get_output_device_id() == ""


def test_system_loopback_uses_output_wasapi_id_not_overlapping_display_name(monkeypatch):
    fake = OverlappingLoopbackSoundcard()
    monkeypatch.setattr(audio_capture, "sc", fake)
    streamer = audio_capture.AudioStreamer(SimpleNamespace(send=lambda payload: None))

    recorder = streamer._create_recorder("system")

    assert recorder is not None
    assert fake.microphone_lookups == [("wasapi-headphones", True)]
def test_mix_worker_zero_pads_when_system_queue_empty_and_mic_active():
    """FIX-5 / C-01: When system_queue is starved (e.g. WASAPI loopback silent) and microphone_queue

    continuously provides data, the worker should zero-pad system frames so mixed audio
    is continuously sent over ws.send with microphone content + silent system.
    """
    sent_payloads: list[bytes] = []
    mock_ws = SimpleNamespace(send=lambda payload: sent_payloads.append(payload))
    streamer = audio_capture.AudioStreamer(
        mock_ws,
        chunk_size=3840,
        mix_starvation_threshold=0.04,
    )
    streamer.mix_mic_gain = 1.0
    streamer.mix_system_gain = 1.0

    system_queue: queue.Queue[np.ndarray] = queue.Queue()
    microphone_queue: queue.Queue[np.ndarray] = queue.Queue()
    local_stop_event = threading.Event()

    worker = threading.Thread(
        target=streamer._mix_and_send_worker,
        args=(system_queue, microphone_queue, local_stop_event),
        daemon=True,
    )
    worker.start()

    try:
        # Feed 8 chunks of 960 frames (total 7680 frames = exactly 2 chunks of 3840)
        # Using a distinct mic signal: 0.5 (which maps to int16 ~16383)
        for _ in range(8):
            microphone_queue.put(np.full(960, 0.5, dtype=np.float32))
            time.sleep(0.01)

        # Wait until 2 mix payloads have been sent
        deadline = time.monotonic() + 2.0
        while len(sent_payloads) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)

        assert len(sent_payloads) >= 2, f"Expected at least 2 payloads, got {len(sent_payloads)}"

        for i in range(2):
            payload_samples = np.frombuffer(sent_payloads[i], dtype=np.int16)
            assert len(payload_samples) == 3840
            # System is 0.0 * 1.0 = 0, Mic is 0.5 * 1.0 = 0.5 -> int16 16383
            np.testing.assert_allclose(payload_samples, 16383, atol=2)
    finally:
        local_stop_event.set()
        worker.join(timeout=1.0)
        assert not worker.is_alive()


def test_mix_worker_zero_pads_when_mic_queue_empty_and_system_active():
    """Symmetric check: When microphone_queue is empty and system_queue provides data,

    the worker should zero-pad microphone frames and send mixed system audio.
    """
    sent_payloads: list[bytes] = []
    mock_ws = SimpleNamespace(send=lambda payload: sent_payloads.append(payload))
    streamer = audio_capture.AudioStreamer(
        mock_ws,
        chunk_size=3840,
        mix_starvation_threshold=0.04,
    )
    streamer.mix_mic_gain = 1.0
    streamer.mix_system_gain = 1.0

    system_queue: queue.Queue[np.ndarray] = queue.Queue()
    microphone_queue: queue.Queue[np.ndarray] = queue.Queue()
    local_stop_event = threading.Event()

    worker = threading.Thread(
        target=streamer._mix_and_send_worker,
        args=(system_queue, microphone_queue, local_stop_event),
        daemon=True,
    )
    worker.start()

    try:
        # Feed 4 chunks of 960 frames of system audio (3840 frames = 1 chunk)
        # Using a distinct system signal: 0.3 (maps to int16 ~9830)
        for _ in range(4):
            system_queue.put(np.full(960, 0.3, dtype=np.float32))
            time.sleep(0.01)

        deadline = time.monotonic() + 2.0
        while len(sent_payloads) < 1 and time.monotonic() < deadline:
            time.sleep(0.02)

        assert len(sent_payloads) >= 1
        payload_samples = np.frombuffer(sent_payloads[0], dtype=np.int16)
        assert len(payload_samples) == 3840
        np.testing.assert_allclose(payload_samples, int(0.3 * 32767), atol=2)
    finally:
        local_stop_event.set()
        worker.join(timeout=1.0)
        assert not worker.is_alive()


def test_mix_worker_does_not_send_when_both_queues_empty():
    """When both system and microphone queues are empty, the worker should not flood ws with synthetic silence."""
    sent_payloads: list[bytes] = []
    mock_ws = SimpleNamespace(send=lambda payload: sent_payloads.append(payload))
    streamer = audio_capture.AudioStreamer(
        mock_ws,
        chunk_size=3840,
        mix_starvation_threshold=0.04,
    )

    system_queue: queue.Queue[np.ndarray] = queue.Queue()
    microphone_queue: queue.Queue[np.ndarray] = queue.Queue()
    local_stop_event = threading.Event()

    worker = threading.Thread(
        target=streamer._mix_and_send_worker,
        args=(system_queue, microphone_queue, local_stop_event),
        daemon=True,
    )
    worker.start()

    try:
        # Wait for longer than the starvation threshold
        time.sleep(0.12)
        assert len(sent_payloads) == 0
    finally:
        local_stop_event.set()
        worker.join(timeout=1.0)
        assert not worker.is_alive()


def test_mix_worker_recovers_real_system_data_after_padding():
    """When real system data resumes after starvation padding, both streams mix properly

    in order without corruption or loss.
    """
    sent_payloads: list[bytes] = []
    mock_ws = SimpleNamespace(send=lambda payload: sent_payloads.append(payload))
    streamer = audio_capture.AudioStreamer(
        mock_ws,
        chunk_size=3840,
        mix_starvation_threshold=0.04,
    )
    streamer.mix_mic_gain = 1.0
    streamer.mix_system_gain = 1.0

    system_queue: queue.Queue[np.ndarray] = queue.Queue()
    microphone_queue: queue.Queue[np.ndarray] = queue.Queue()
    local_stop_event = threading.Event()

    worker = threading.Thread(
        target=streamer._mix_and_send_worker,
        args=(system_queue, microphone_queue, local_stop_event),
        daemon=True,
    )
    worker.start()

    try:
        # Phase 1: Mic active with 0.4, System empty -> pads silence and sends
        for _ in range(4):
            microphone_queue.put(np.full(960, 0.4, dtype=np.float32))
            time.sleep(0.01)

        deadline = time.monotonic() + 2.0
        while len(sent_payloads) < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(sent_payloads) >= 1
        p1 = np.frombuffer(sent_payloads[0], dtype=np.int16)
        np.testing.assert_allclose(p1, int(0.4 * 32767), atol=2)

        # Phase 2: Both active: mic has 0.2, system has 0.3 -> mixed is 0.5
        for _ in range(4):
            microphone_queue.put(np.full(960, 0.2, dtype=np.float32))
            system_queue.put(np.full(960, 0.3, dtype=np.float32))
            time.sleep(0.01)

        deadline = time.monotonic() + 2.0
        while len(sent_payloads) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(sent_payloads) >= 2
        p2 = np.frombuffer(sent_payloads[1], dtype=np.int16)
        np.testing.assert_allclose(p2, int(0.5 * 32767), atol=2)
    finally:
        local_stop_event.set()
        worker.join(timeout=1.0)
        assert not worker.is_alive()
