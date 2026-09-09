from types import SimpleNamespace

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
