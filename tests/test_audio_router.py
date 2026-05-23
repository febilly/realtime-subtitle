from audio_router import AudioSendRouter, EnergySilenceDetector


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


def test_energy_silence_detector_waits_for_hold_duration():
    detector = EnergySilenceDetector(sample_rate=16000, silence_hold_seconds=0.4)
    silence = b"\0" * 7680  # 3840 int16 samples, roughly 0.24s at 16 kHz

    assert detector.update(silence) is True
    assert detector.is_ready() is False
    assert detector.update(silence) is True
    assert detector.is_ready() is True
