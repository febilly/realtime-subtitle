from types import SimpleNamespace

import numpy as np
import pytest

from local_inference.boundary_scout import (
    BoundaryScoutUnavailableError,
    NemotronBoundaryScout,
    ScoutSnapshot,
    align_confirmed_prefix_to_scout,
)
from local_inference import model_manager


class _FakeResult:
    def __init__(self, text="", tokens=(), timestamps=()):
        self.text = text
        self.tokens = list(tokens)
        self.timestamps = list(timestamps)


class _FakeStream:
    def __init__(self, result):
        self.result = result
        self.accepted = []
        self.options = []
        self.ready = 1

    def accept_waveform(self, sample_rate, samples):
        self.accepted.append((sample_rate, np.asarray(samples).copy()))
        self.ready = 1

    def set_option(self, key, value):
        self.options.append((key, value))


class _FakeOnlineRecognizer:
    instances = []
    default_result = _FakeResult()

    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.streams = []
        self.decode_calls = 0
        self.__class__.instances.append(self)

    @classmethod
    def from_transducer(cls, **kwargs):
        return cls(kwargs)

    def create_stream(self):
        stream = _FakeStream(self.default_result)
        self.streams.append(stream)
        return stream

    def is_ready(self, stream):
        return stream.ready > 0

    def decode_stream(self, stream):
        stream.ready -= 1
        self.decode_calls += 1

    def get_result_all(self, stream):
        return stream.result


@pytest.fixture
def model_dir(tmp_path):
    for filename in (
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "joiner.int8.onnx",
        "tokens.txt",
    ):
        (tmp_path / filename).touch()
    return tmp_path


@pytest.fixture
def fake_sherpa(monkeypatch):
    _FakeOnlineRecognizer.instances.clear()
    _FakeOnlineRecognizer.default_result = _FakeResult()
    module = SimpleNamespace(OnlineRecognizer=_FakeOnlineRecognizer)
    monkeypatch.setattr(
        "local_inference.boundary_scout.importlib.import_module",
        lambda name: module if name == "sherpa_onnx" else None,
    )
    return module


def test_scout_validates_all_four_model_files_before_import(tmp_path):
    (tmp_path / "tokens.txt").touch()

    with pytest.raises(BoundaryScoutUnavailableError) as caught:
        NemotronBoundaryScout(tmp_path)

    message = str(caught.value)
    assert "encoder.int8.onnx" in message
    assert "decoder.int8.onnx" in message
    assert "joiner.int8.onnx" in message
    assert str(tmp_path) in message


def test_scout_reports_missing_sherpa_dependency_clearly(model_dir, monkeypatch):
    def unavailable(_name):
        raise ModuleNotFoundError("sherpa_onnx")

    monkeypatch.setattr(
        "local_inference.boundary_scout.importlib.import_module", unavailable
    )

    with pytest.raises(BoundaryScoutUnavailableError, match="requires sherpa-onnx"):
        NemotronBoundaryScout(model_dir)


def test_scout_decodes_float32_and_saves_parallel_snapshot(model_dir, fake_sherpa):
    _FakeOnlineRecognizer.default_result = _FakeResult(
        "Hello, world. Next",
        ("<|en|>", "Hello", ",", " world", ".", " Next"),
        (0.0, 0.10, 0.20, 0.35, 0.50, 0.72),
    )
    scout = NemotronBoundaryScout(model_dir, num_threads=3, language="EN")
    samples = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)

    snapshot = scout.feed(samples)

    recognizer = _FakeOnlineRecognizer.instances[-1]
    assert recognizer.kwargs["num_threads"] == 3
    assert recognizer.kwargs["enable_endpoint_detection"] is False
    assert recognizer.kwargs["provider"] == "cpu"
    assert recognizer.streams[0].options == [("language", "en")]
    assert recognizer.streams[0].accepted[0][0] == 16_000
    assert recognizer.streams[0].accepted[0][1].dtype == np.float32
    np.testing.assert_array_equal(recognizer.streams[0].accepted[0][1], samples)
    assert recognizer.decode_calls == 1
    assert snapshot == ScoutSnapshot(
        text="Hello, world. Next",
        tokens=("<|en|>", "Hello", ",", " world", ".", " Next"),
        timestamps=(0.0, 0.10, 0.20, 0.35, 0.50, 0.72),
        start_time=0.0,
    )
    assert scout.snapshot is snapshot
    assert scout.timeline_seconds == pytest.approx(0.1)


def test_reset_creates_new_stream_and_replay_does_not_advance_timeline(
    model_dir, fake_sherpa
):
    _FakeOnlineRecognizer.default_result = _FakeResult(
        "suffix grows", ("suffix", " grows"), (0.20, 0.55)
    )
    scout = NemotronBoundaryScout(model_dir)
    scout.feed(np.ones(16_000, dtype=np.float32))

    snapshot = scout.reset(np.ones(4_000, dtype=np.float32))

    recognizer = _FakeOnlineRecognizer.instances[-1]
    assert len(recognizer.streams) == 2
    assert recognizer.streams[1].accepted[0][0] == 16_000
    assert recognizer.streams[1].accepted[0][1].size == 4_000
    assert snapshot.start_time == pytest.approx(0.75)
    assert scout.timeline_seconds == pytest.approx(1.0)


def test_scout_rejects_results_without_parallel_timestamps(model_dir, fake_sherpa):
    _FakeOnlineRecognizer.default_result = _FakeResult(
        "hello next", ("hello", " next"), ()
    )
    scout = NemotronBoundaryScout(model_dir)

    with pytest.raises(BoundaryScoutUnavailableError, match="one timestamp per token"):
        scout.feed(np.ones(160, dtype=np.float32))


def test_align_english_prefix_ignores_case_spaces_punctuation_and_language_tag():
    snapshot = ScoutSnapshot(
        text="HELLO, world! This continues",
        tokens=("<|en|>", "\u2581HELLO", ",", "\u2581world", "!", "\u2581This", " continues"),
        timestamps=(0.0, 0.10, 0.22, 0.35, 0.52, 0.70, 0.90),
        start_time=4.0,
    )

    alignment = align_confirmed_prefix_to_scout("Hello world.", snapshot)

    assert alignment is not None
    assert alignment.cut_seconds == pytest.approx(4.70)
    assert alignment.confidence == 1.0
    assert alignment.evidence == "normalized_exact_prefix_with_next_lexical_token"
    assert alignment.matched_lexical_tokens == 2
    assert alignment.matched_scout_token_index == 3
    assert alignment.next_scout_token_index == 5
    assert alignment.next_token == "\u2581This"


def test_align_chinese_prefix_tolerates_different_punctuation_tokens():
    snapshot = ScoutSnapshot(
        text="你好,世界!下一句",
        tokens=("你", "好", ",", "世界", "!", "下一", "句"),
        timestamps=(0.05, 0.12, 0.18, 0.30, 0.48, 0.62, 0.78),
        start_time=1.5,
    )

    alignment = align_confirmed_prefix_to_scout("你好，世界。", snapshot)

    assert alignment is not None
    assert alignment.cut_seconds == pytest.approx(2.12)
    assert alignment.matched_scout_token_index == 3
    assert alignment.next_scout_token_index == 5


@pytest.mark.parametrize(
    "prefix,snapshot",
    [
        (
            "hello earth.",
            ScoutSnapshot(
                "hello world next",
                ("hello", " world", " next"),
                (0.1, 0.3, 0.5),
                0.0,
            ),
        ),
        (
            "hello world.",
            ScoutSnapshot(
                "noise hello world next",
                ("noise", " hello", " world", " next"),
                (0.0, 0.2, 0.4, 0.6),
                0.0,
            ),
        ),
        (
            "hello world.",
            ScoutSnapshot(
                "<unk> hello world next",
                ("<unk>", " hello", " world", " next"),
                (0.0, 0.2, 0.4, 0.6),
                0.0,
            ),
        ),
        (
            "hello",
            ScoutSnapshot("helloworld next", ("helloworld", " next"), (0.1, 0.5), 0.0),
        ),
        (
            "你好。",
            ScoutSnapshot("你好", ("你", "好", "。"), (0.1, 0.2, 0.3), 0.0),
        ),
    ],
)
def test_align_rejects_mismatch_noninitial_midtoken_or_missing_right_context(
    prefix, snapshot
):
    assert align_confirmed_prefix_to_scout(prefix, snapshot) is None


def test_align_rejects_nonparallel_timestamp_array():
    snapshot = ScoutSnapshot(
        "hello next", ("hello", " next"), (0.1,), start_time=0.0
    )

    assert align_confirmed_prefix_to_scout("hello", snapshot) is None


def test_audio_validation_is_conservative(model_dir, fake_sherpa):
    scout = NemotronBoundaryScout(model_dir)

    with pytest.raises(ValueError, match="one-dimensional mono"):
        scout.feed(np.zeros((2, 10), dtype=np.float32))
    with pytest.raises(ValueError, match="finite"):
        scout.feed(np.array([0.0, np.nan], dtype=np.float32))
    with pytest.raises(ValueError, match="longer"):
        scout.reset(np.ones(1, dtype=np.float32))


def test_frozen_relative_sidecar_path_resolves_beside_executable(
    monkeypatch, tmp_path
):
    executable_dir = tmp_path / "portable"
    model_dir = (
        executable_dir
        / "models"
        / model_manager.NEMOTRON_BOUNDARY_DIR_NAME
    )
    model_dir.mkdir(parents=True)
    for filename in model_manager.NEMOTRON_BOUNDARY_FILES:
        (model_dir / filename).write_bytes(b"fixture")

    monkeypatch.setattr(model_manager.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        model_manager.sys,
        "executable",
        str(executable_dir / "RealtimeSubtitle.exe"),
    )

    configured = f"models/{model_manager.NEMOTRON_BOUNDARY_DIR_NAME}"
    assert model_manager.get_semantic_boundary_model_path(configured) == model_dir.resolve()
