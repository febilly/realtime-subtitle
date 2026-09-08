import importlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest


# Keep this focused unit suite attached to the real config even when another
# legacy test installs a minimal config module during collection.
_previous_config = sys.modules.pop("config", None)
try:
    importlib.import_module("config")
    from local_inference.recognizer import LocalQwenRecognizer
    from local_inference.recognizer import config
    from local_inference.semantic_boundary import BoundaryCommit
finally:
    if _previous_config is None:
        sys.modules.pop("config", None)
    else:
        sys.modules["config"] = _previous_config


@pytest.fixture(autouse=True)
def _use_active_recognizer_config(monkeypatch):
    # Legacy collection/reload tests may replace the recognizer's config after
    # this module imports. Patch the object used by the running methods.
    monkeypatch.setitem(
        globals(), "config", LocalQwenRecognizer._on_transcription_done.__globals__["config"]
    )


def _recognizer(monkeypatch, captured):
    monkeypatch.setattr(config, "VAD_ENABLED", False)
    monkeypatch.setattr(config, "LOCAL_VAD_MODE", "disabled")
    recognizer = LocalQwenRecognizer(
        lambda text, final, raw: captured.append((text, final, raw)), Mock()
    )
    return recognizer


def _set_live_audio(recognizer, seconds=1.1):
    audio = np.ones(int(seconds * 16000), dtype=np.float32)
    recognizer._vad._speech_buffer = [audio]
    recognizer._vad._confidence_history = [1.0]
    recognizer._vad._speech_samples = audio.size
    recognizer._vad._is_speaking = True
    return audio


@pytest.mark.parametrize("semantic_enabled", [False, True])
def test_uninterrupted_speech_waits_for_four_second_fallback(monkeypatch, semantic_enabled):
    captured = []
    recognizer = _recognizer(monkeypatch, captured)
    _set_live_audio(recognizer, seconds=3.9)
    recognizer._enqueue_transcribe = Mock()
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_ASR", True)
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_MIN_UPDATE_INTERVAL", 0.3)
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_MAX_UPDATE_INTERVAL", 4.0)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_ENABLED", semantic_enabled)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_SCAN_INTERVAL", 1.0)
    monkeypatch.setattr("local_inference.recognizer.time.monotonic", lambda: 100.0)
    recognizer._last_partial_time = 96.1

    recognizer._maybe_emit_partial()
    recognizer._enqueue_transcribe.assert_not_called()

    _set_live_audio(recognizer, seconds=4.01)
    recognizer._last_partial_time = 95.99

    recognizer._maybe_emit_partial()

    assert recognizer._enqueue_transcribe.call_args.kwargs["present"] is True


def test_natural_pause_updates_before_four_second_fallback(monkeypatch):
    recognizer = _recognizer(monkeypatch, [])
    _set_live_audio(recognizer)
    recognizer._vad._silence_counter = 1
    recognizer._enqueue_transcribe = Mock()
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_ASR", True)
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_MIN_UPDATE_INTERVAL", 0.3)
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_MAX_UPDATE_INTERVAL", 4.0)
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_TRIGGER_SILENCE_MS", 10)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_ENABLED", False)
    monkeypatch.setattr("local_inference.recognizer.time.monotonic", lambda: 100.0)
    recognizer._last_partial_time = 99.71
    recognizer._maybe_emit_partial()
    recognizer._enqueue_transcribe.assert_not_called()

    recognizer._last_partial_time = 99.69
    recognizer._maybe_emit_partial()
    assert recognizer._enqueue_transcribe.call_args.kwargs["present"] is True
    assert not recognizer._silence_trigger_armed


def test_known_boundary_still_gets_fast_confirmation(monkeypatch):
    recognizer = _recognizer(monkeypatch, [])
    _set_live_audio(recognizer, seconds=1.4)
    recognizer._last_boundary_scan_samples = 16000
    recognizer._boundary_detector = SimpleNamespace(has_provisional_boundary=True)
    recognizer._enqueue_transcribe = Mock()
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_ASR", True)
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_MAX_UPDATE_INTERVAL", 4.0)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_ENABLED", True)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_SCAN_INTERVAL", 1.0)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_CONFIRM_INTERVAL", 0.35)
    monkeypatch.setattr("local_inference.recognizer.time.monotonic", lambda: 100.0)
    recognizer._last_partial_time = 99.6
    recognizer._maybe_emit_partial()
    assert recognizer._enqueue_transcribe.call_args.kwargs["present"] is True


def test_semantic_scan_is_presented_before_physical_location(monkeypatch):
    captured = []
    recognizer = _recognizer(monkeypatch, captured)
    audio = _set_live_audio(recognizer, seconds=6.0)
    recognizer._enqueue_transcribe = Mock()
    monkeypatch.setattr(config, "LOCAL_INCREMENTAL_ASR", True)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_ENABLED", True)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_MIN_AUDIO_SECONDS", 1.0)
    commit = BoundaryCommit(
        prefix="First sentence.",
        suffix=" Second sentence",
        boundary_index=len("First sentence."),
        agreement_count=2,
    )
    recognizer._boundary_detector.observe = Mock(return_value=commit)
    location_observed_after = []

    def locate(_commit, _audio):
        location_observed_after.append(list(captured))
        return 4.5, "nemotron_timestamp"

    recognizer._locate_boundary_seconds = locate
    future = Mock()
    future.result.return_value = (
        "First sentence. Second sentence",
        {"text": "First sentence. Second sentence", "language": "en"},
    )
    request = SimpleNamespace(
        is_final=False,
        stream_id=recognizer._stream_id,
        voiced_seq=7,
        present=True,
        audio=audio[: 5 * 16000],
    )

    assert recognizer._semantic_boundary_enabled()
    assert recognizer._vad.is_speaking
    assert request.audio.size >= recognizer._semantic_cut_min_samples()
    recognizer._on_transcription_done(future, request=request)

    assert location_observed_after == [[(
        "First sentence. Second sentence", False,
        {"text": "First sentence. Second sentence", "language": "en"},
    )]]
    assert [(text, final) for text, final, _raw in captured] == [
        ("First sentence. Second sentence", False),
        ("First sentence.", True),
        (" Second sentence", False),
    ]
    final_raw = captured[1][2]
    assert final_raw["semantic_suffix_text"] == " Second sentence"
    assert final_raw["semantic_boundary"]["method"] == "nemotron_timestamp"
    assert captured[2][2]["language"] == "en"
    assert recognizer._last_emitted_partial_text == " Second sentence"
    assert recognizer._replay_expected_suffix == " Second sentence"
    assert recognizer._enqueue_transcribe.call_args.kwargs == {
        "is_final": False,
        "present": True,
    }


def test_short_window_still_displays_source_without_expensive_cut_probes(monkeypatch):
    captured = []
    recognizer = _recognizer(monkeypatch, captured)
    audio = _set_live_audio(recognizer, seconds=6)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_ENABLED", True)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_MIN_AUDIO_SECONDS", 8.0)
    recognizer._locate_boundary_seconds = Mock(side_effect=AssertionError("short window probed"))
    for text in ["First sentence. Second sentence", "First sentence. Second sentence grows"]:
        future = Mock()
        future.result.return_value = (text, {"text": text, "language": "en"})
        recognizer._on_transcription_done(future, request=SimpleNamespace(
            is_final=False, stream_id=recognizer._stream_id, voiced_seq=1, present=True, audio=audio))
    assert len(captured) == 2 and all(not final for _, final, _ in captured)
    recognizer._locate_boundary_seconds.assert_not_called()


def test_long_window_cuts_through_latest_confirmed_sentence_and_retains_audio_tail(monkeypatch):
    captured = []
    recognizer = _recognizer(monkeypatch, captured)
    audio = _set_live_audio(recognizer, seconds=12)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_ENABLED", True)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_MIN_AUDIO_SECONDS", 8.0)
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_REPLAY_MS", 800)
    recognizer._locate_boundary_seconds = Mock(return_value=(9.0, "qwen_prefix_search"))
    recognizer._enqueue_transcribe = Mock()
    for text in ["First sentence. Second sentence. Third is starting", "First sentence. Second sentence. Third is growing"]:
        future = Mock()
        future.result.return_value = (text, {"text": text, "language": "en"})
        recognizer._on_transcription_done(future, request=SimpleNamespace(
            is_final=False, stream_id=recognizer._stream_id, voiced_seq=1, present=True, audio=audio))
    final = next(item for item in captured if item[1])
    assert final[0] == "First sentence. Second sentence."
    assert captured[-1][0] == " Third is growing"
    assert len(recognizer._enqueue_transcribe.call_args.args[0]) == int(3.8 * 16000)
    assert recognizer._semantic_stats["commits"] == 1


def test_replay_expected_suffix_is_passed_to_main_and_locator_dedup(monkeypatch):
    captured = []
    recognizer = _recognizer(monkeypatch, captured)
    recognizer._replayed_committed_text = "committed replay evidence"
    recognizer._replay_expected_suffix = "known suffix continuation"
    recognizer._replay_max_lexical_chars = 12
    calls = []

    def dedup(committed, replayed, **kwargs):
        calls.append((committed, replayed, kwargs))
        return replayed

    monkeypatch.setattr("local_inference.recognizer.deduplicate_normalized_replay", dedup)
    assert recognizer._deduplicate_replay("candidate", {"text": "candidate"})[0] == "candidate"

    engine = Mock()
    engine.probe_transcribe.return_value = {"text": "target"}
    recognizer._ensure_engine = Mock(return_value=engine)
    assert recognizer._qwen_prefix_locator(np.ones(16000, dtype=np.float32), "target") is None

    assert len(calls) == 2
    assert all(call[2]["expected_suffix"] == "known suffix continuation" for call in calls)
    assert all(call[2]["max_overlap_lexical_chars"] == 12 for call in calls)


def test_replay_expected_suffix_clears_at_replay_end_and_new_start(monkeypatch):
    captured = []
    recognizer = _recognizer(monkeypatch, captured)
    recognizer._replayed_committed_text = "old prefix"
    recognizer._replay_expected_suffix = "old suffix"
    recognizer._finish_stream_state()
    assert recognizer._replayed_committed_text == ""
    assert recognizer._replay_expected_suffix == ""

    engine = Mock()
    recognizer._engine = engine
    recognizer._ensure_engine = Mock(return_value=engine)
    recognizer._ensure_boundary_scout = Mock(return_value=None)
    recognizer._replayed_committed_text = "stale prefix"
    recognizer._replay_expected_suffix = "stale suffix"
    recognizer.start()
    try:
        assert recognizer._replay_expected_suffix == ""
    finally:
        recognizer.stop()
