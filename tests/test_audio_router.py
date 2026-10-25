import audio_router as audio_router_module
from audio_router import (
    AudioSendRouter,
    EnergySilenceDetector,
    SileroVadLiteSilenceDetector,
    TenVadSilenceDetector,
)


def _force_energy_backend(monkeypatch):
    """Make every VAD backend unavailable so the router falls back to energy."""
    monkeypatch.setattr(audio_router_module, "TenVadBackend", None)
    monkeypatch.setattr(audio_router_module, "TenVadImportError", RuntimeError("missing"))
    monkeypatch.setattr(audio_router_module, "SileroVadLiteBackend", None)
    monkeypatch.setattr(audio_router_module, "SileroVadLiteImportError", RuntimeError("missing"))


class RecordingTarget:
    def __init__(self):
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)


def test_audio_router_buffers_until_target_is_set():
    router = AudioSendRouter(max_buffered_chunks=4)
    target = RecordingTarget()

    router.send(b"a")
    router.send(b"b")

    assert router.buffered_count() == 2
    assert router.set_target(target) is True
    assert target.payloads == [b"a", b"b"]
    assert router.buffered_count() == 0

    router.send(b"c")
    assert target.payloads == [b"a", b"b", b"c"]


def test_audio_router_keeps_recent_audio_when_buffer_is_full():
    router = AudioSendRouter(max_buffered_chunks=2)
    target = RecordingTarget()

    router.send(b"old")
    router.send(b"newer")
    router.send(b"newest")

    assert router.set_target(target) is True
    assert target.payloads == [b"newer", b"newest"]


def test_audio_router_buffers_after_target_is_cleared():
    router = AudioSendRouter(max_buffered_chunks=4)
    first = RecordingTarget()
    second = RecordingTarget()

    assert router.set_target(first) is True
    router.send(b"a")
    router.clear_target(first)
    router.send(b"b")

    assert first.payloads == [b"a"]
    assert router.set_target(second) is True
    assert second.payloads == [b"b"]


def test_audio_router_switches_active_target_without_replaying_to_old_target():
    router = AudioSendRouter(max_buffered_chunks=4)
    first = RecordingTarget()
    second = RecordingTarget()

    assert router.set_target(first) is True
    router.send(b"a")
    assert router.switch_target(second, expected_current=first) is True
    router.send(b"b")

    assert first.payloads == [b"a"]
    assert second.payloads == [b"b"]


def test_audio_router_does_not_switch_when_expected_current_mismatches():
    router = AudioSendRouter(max_buffered_chunks=4)
    first = RecordingTarget()
    unexpected = RecordingTarget()
    second = RecordingTarget()

    assert router.set_target(first) is True
    assert router.switch_target(second, expected_current=unexpected) is False
    router.send(b"a")

    assert first.payloads == [b"a"]
    assert second.payloads == []


def test_audio_router_flush_failure_with_concurrent_audio_preserves_fifo_order():
    """R-01: When _attach_target flush fails midway, unsent chunks must be prepended

    to the deque so newly arrived realtime audio remains strictly after older chunks.
    """
    router = AudioSendRouter(max_buffered_chunks=10)
    for chunk in [b"chunk0", b"chunk1", b"chunk2", b"chunk3"]:
        router.send(chunk)
    assert router.buffered_count() == 4

    class FailingOnChunk2Target:
        def __init__(self):
            self.payloads = []

        def send(self, payload: bytes):
            if payload == b"chunk2":
                # Concurrent real-time audio arriving during the flush while target detached
                router.send(b"realtime0")
                router.send(b"realtime1")
                raise RuntimeError("simulated network failure during flush")
            self.payloads.append(payload)

    failing_target = FailingOnChunk2Target()
    assert router.set_target(failing_target) is False
    assert failing_target.payloads == [b"chunk0", b"chunk1"]

    # Deque must strictly preserve chronological FIFO order:
    # [chunk2, chunk3] (older unsent) followed by [realtime0, realtime1] (newer)
    assert list(router._buffered_chunks) == [
        b"chunk2",
        b"chunk3",
        b"realtime0",
        b"realtime1",
    ]

    # Recovery target should receive all remaining chunks in strict FIFO order
    recovery_target = RecordingTarget()
    assert router.set_target(recovery_target) is True
    assert recovery_target.payloads == [
        b"chunk2",
        b"chunk3",
        b"realtime0",
        b"realtime1",
    ]
    assert router.buffered_count() == 0


def test_audio_router_flush_failure_overflow_preserves_fifo_and_drops_oldest():
    """R-01 overflow interaction: When re-inserted unsent chunks + concurrent chunks

    exceed max_buffered_chunks, keep drop-oldest semantic to avoid latency buildup
    while maintaining strict FIFO order among retained chunks.
    """
    router = AudioSendRouter(max_buffered_chunks=4)
    for chunk in [b"chunk0", b"chunk1", b"chunk2", b"chunk3"]:
        router.send(chunk)
    assert router.buffered_count() == 4

    class FailingOnChunk1Target:
        def __init__(self):
            self.payloads = []

        def send(self, payload: bytes):
            if payload == b"chunk1":
                router.send(b"realtime0")
                router.send(b"realtime1")
                raise RuntimeError("simulated network failure")
            self.payloads.append(payload)

    failing_target = FailingOnChunk1Target()
    assert router.set_target(failing_target) is False
    assert failing_target.payloads == [b"chunk0"]

    # Unsent older chunks: chunk1, chunk2, chunk3 (3 chunks)
    # Concurrent chunks: realtime0, realtime1 (2 chunks)
    # Total = 5 chunks > max_buffered_chunks (4).
    # Preserving FIFO and dropping the oldest chunk (chunk1) yields:
    # [chunk2, chunk3, realtime0, realtime1]
    assert list(router._buffered_chunks) == [
        b"chunk2",
        b"chunk3",
        b"realtime0",
        b"realtime1",
    ]

    recovery_target = RecordingTarget()
    assert router.set_target(recovery_target) is True
    assert recovery_target.payloads == [
        b"chunk2",
        b"chunk3",
        b"realtime0",
        b"realtime1",
    ]


def test_audio_router_switch_target_flush_failure_threaded_concurrent_sends():
    """Verify thread safety and FIFO order when real-time audio arrives from a separate

    thread during a failed switch_target flush.
    """
    import threading
    import time

    router = AudioSendRouter(max_buffered_chunks=50)
    initial_target = RecordingTarget()
    assert router.set_target(initial_target) is True

    # Fill initial buffer by clearing target
    router.clear_target(initial_target)
    for i in range(5):
        router.send(f"init_{i}".encode("ascii"))

    stop_event = threading.Event()
    produced = []

    def audio_feeder():
        count = 0
        while not stop_event.is_set():
            data = f"stream_{count}".encode("ascii")
            produced.append(data)
            router.send(data)
            count += 1
            time.sleep(0.001)

    class FailingTarget:
        def __init__(self):
            self.calls = 0

        def send(self, payload: bytes):
            self.calls += 1
            time.sleep(0.005)
            if self.calls >= 3:
                raise ConnectionResetError("Target disconnected during rollover flush")

    feeder_thread = threading.Thread(target=audio_feeder, daemon=True)
    feeder_thread.start()

    # switch_target will flush init_0..init_4, fail on call 3 (init_2)
    assert router.switch_target(FailingTarget()) is False

    stop_event.set()
    feeder_thread.join(timeout=1.0)

    # All remaining init chunks (init_2, init_3, init_4) must precede produced stream chunks
    buffered_list = list(router._buffered_chunks)
    init_remaining = [chunk for chunk in buffered_list if chunk.startswith(b"init_")]
    stream_chunks = [chunk for chunk in buffered_list if chunk.startswith(b"stream_")]

    assert init_remaining == [b"init_2", b"init_3", b"init_4"]
    # Verify that in buffered_list, all init_remaining appear BEFORE stream_chunks
    assert buffered_list[:3] == init_remaining
    assert buffered_list[3:] == stream_chunks

    # Also verify stream_chunks are strictly in increasing order
    stream_indices = [int(chunk.decode("ascii").split("_")[1]) for chunk in stream_chunks]
    assert stream_indices == sorted(stream_indices)


def test_energy_silence_detector_waits_for_hold_duration():
    detector = EnergySilenceDetector(sample_rate=16000, silence_hold_seconds=0.4)
    silence = b"\0" * 7680  # 3840 int16 samples, roughly 0.24s at 16 kHz

    assert detector.update(silence) is True
    assert detector.is_ready() is False
    assert detector.update(silence) is True
    assert detector.is_ready() is True


def test_ten_vad_silence_detector_waits_for_non_speech_hold(monkeypatch):
    class FakeTenVad:
        def __init__(self, hop_size, threshold):
            self.hop_size = hop_size
            self.threshold = threshold

        def process(self, audio_frame):
            flag = 1 if int(audio_frame[0]) else 0
            return float(flag), flag

    monkeypatch.setattr(audio_router_module, "TenVadBackend", FakeTenVad)
    monkeypatch.setattr(audio_router_module, "TenVadImportError", None)

    detector = TenVadSilenceDetector(sample_rate=16000, silence_hold_seconds=0.03, hop_size=256)
    silence_frame = b"\0" * 512
    speech_frame = (1).to_bytes(2, "little", signed=True) + (b"\0" * 510)

    assert detector.update(silence_frame) is True
    assert detector.is_ready() is False
    assert detector.update(silence_frame) is True
    assert detector.is_ready() is True
    assert detector.update(speech_frame) is False
    assert detector.is_ready() is False


def test_silero_vad_lite_silence_detector_waits_for_non_speech_hold(monkeypatch):
    class FakeSileroVAD:
        def __init__(self, sample_rate):
            self.sample_rate = sample_rate
            self.window_size_samples = 512

        def process(self, audio_frame):
            return 1.0 if float(audio_frame[0]) > 0.0 else 0.0

    monkeypatch.setattr(audio_router_module, "SileroVadLiteBackend", FakeSileroVAD)
    monkeypatch.setattr(audio_router_module, "SileroVadLiteImportError", None)

    detector = SileroVadLiteSilenceDetector(sample_rate=16000, silence_hold_seconds=0.06)
    silence_frame = b"\0" * 1024
    speech_frame = (1000).to_bytes(2, "little", signed=True) + (b"\0" * 1022)

    assert detector.update(silence_frame) is True
    assert detector.is_ready() is False
    assert detector.update(silence_frame) is True
    assert detector.is_ready() is True
    assert detector.update(speech_frame) is False
    assert detector.is_ready() is False


def _pcm_chunk(value: int, samples: int = 100) -> bytes:
    return int(value).to_bytes(2, "little", signed=True) * samples


def test_sleep_gate_counts_short_speech_blips_as_silence(monkeypatch):
    _force_energy_backend(monkeypatch)

    router = AudioSendRouter(
        max_buffered_chunks=8,
        sample_rate=1000,
        chunk_size=100,
        sleep_idle_seconds=0.5,
        sleep_speech_grace_seconds=0.5,
        sleep_speech_window_seconds=0.75,
    )

    silence = _pcm_chunk(0)
    blip = _pcm_chunk(2000)

    for _ in range(4):
        router.send(silence)
    router.send(blip)

    assert router.sleep_ready() is True


def test_sleep_gate_accepts_enough_speech_inside_window(monkeypatch):
    _force_energy_backend(monkeypatch)

    router = AudioSendRouter(
        max_buffered_chunks=8,
        sample_rate=1000,
        chunk_size=100,
        sleep_idle_seconds=2.0,
        sleep_speech_grace_seconds=0.5,
        sleep_speech_window_seconds=0.75,
    )

    silence = _pcm_chunk(0)
    speech = _pcm_chunk(2000)

    for _ in range(9):
        router.send(silence)
    assert router.sleep_ready() is False

    router.send(speech)
    router.send(speech)
    router.send(silence)
    router.send(speech)
    router.send(speech)
    assert router.sleep_ready() is False

    router.send(speech)
    assert router.sleep_ready() is False


def test_sleep_buffer_keeps_preroll_and_wake_audio(monkeypatch):
    _force_energy_backend(monkeypatch)

    router = AudioSendRouter(
        max_buffered_chunks=10,
        sample_rate=1000,
        chunk_size=100,
        sleep_idle_seconds=0.5,
        sleep_pre_roll_seconds=0.2,
        sleep_speech_grace_seconds=0.5,
        sleep_speech_window_seconds=0.75,
    )
    first = RecordingTarget()
    resumed = RecordingTarget()

    assert router.set_target(first) is True
    assert router.enter_sleep_buffering(first) is True

    silence = _pcm_chunk(0)
    speech_a = _pcm_chunk(2000)
    speech_b = _pcm_chunk(3000)

    router.send(silence)
    router.send(silence)
    router.send(silence)
    assert router.wake_ready() is False

    router.send(speech_a)
    assert router.wake_ready() is False
    router.send(speech_b)
    assert router.wake_ready() is False
    router.send(speech_a)
    assert router.wake_ready() is False
    router.send(speech_b)
    assert router.wake_ready() is False
    router.send(speech_a)
    assert router.wake_ready() is True

    assert router.set_target(resumed) is True
    assert resumed.payloads == [
        silence,
        silence,
        silence,
        speech_a,
        speech_b,
        speech_a,
        speech_b,
        speech_a,
    ]


def test_reset_sleep_tracking_restarts_idle_and_clears_latched_wake(monkeypatch):
    _force_energy_backend(monkeypatch)
    router = AudioSendRouter(
        sample_rate=1000,
        chunk_size=100,
        sleep_idle_seconds=0.3,
        sleep_speech_grace_seconds=0.2,
        sleep_speech_window_seconds=0.3,
    )
    silence = _pcm_chunk(0)
    speech = _pcm_chunk(2000)

    for _ in range(3):
        router.send(silence)
    assert router.sleep_ready() is True
    router.reset_sleep_tracking()
    assert router.sleep_ready() is False

    target = RecordingTarget()
    assert router.set_target(target) is True
    assert router.enter_sleep_buffering(target) is True
    router.send(speech)
    router.send(speech)
    assert router.wake_ready() is True
    router.reset_sleep_tracking()
    assert router.wake_ready() is False


def test_dormant_wake_requires_stricter_speech_evidence(monkeypatch):
    _force_energy_backend(monkeypatch)
    router = AudioSendRouter(
        sample_rate=1000,
        chunk_size=100,
        sleep_idle_seconds=1.0,
        sleep_speech_grace_seconds=0.2,
        sleep_speech_window_seconds=0.3,
        sleep_wake_speech_seconds=0.4,
        sleep_wake_speech_window_seconds=0.5,
    )
    target = RecordingTarget()
    assert router.set_target(target) is True
    assert router.enter_sleep_buffering(target) is True
    speech = _pcm_chunk(2000)

    for _ in range(3):
        router.send(speech)
    assert router.wake_ready() is False
    router.send(speech)
    assert router.wake_ready() is True


def test_audio_router_dual_vad_thresholds():
    router = AudioSendRouter(
        max_buffered_chunks=10,
        sample_rate=16000,
        chunk_size=3840,
        vad_speech_threshold=0.6,
        sleep_vad_threshold=0.2,
        sleep_wake_vad_threshold=0.7,
    )
    if hasattr(router._silence_detector, "speech_threshold"):
        assert router._silence_detector.speech_threshold == 0.6
    if hasattr(router._sleep_silence_detector, "speech_threshold"):
        assert router._sleep_silence_detector.speech_threshold == 0.2
    if hasattr(router._wake_silence_detector, "speech_threshold"):
        assert router._wake_silence_detector.speech_threshold == 0.7
