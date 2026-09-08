import importlib
import asyncio
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

# Some legacy test modules install a deliberately minimal ``config`` stub at
# collection time. Load this integration test against the real application
# config without changing the module seen by those tests.
_previous_config = sys.modules.pop("config", None)
try:
    config = importlib.import_module("config")
    # A preceding collection module may already have imported these modules
    # against its own temporary real-config instance. Bind their runtime
    # globals to this test module's deliberately retained config object so
    # per-test monkeypatches affect the code under test.
    from local_inference import asr_qwen3 as _asr_qwen3_module
    from local_inference import recognizer as _recognizer_module
    _asr_qwen3_module.config = config
    _recognizer_module.config = config
    from local_inference.asr_qwen3 import Qwen3ASREngine
    from local_inference.recognizer import LocalQwenRecognizer
    from local_inference.semantic_boundary import BoundaryCommit
    from local_session import LocalInferenceSession
    from local_inference.subtitle_pipeline import LocalSubtitlePipeline
finally:
    if _previous_config is None:
        sys.modules.pop("config", None)
    else:
        sys.modules["config"] = _previous_config


def _session():
    logger = Mock(enabled=False, log_file=None)
    return LocalInferenceSession(logger, Mock())


def test_local_provider_language_and_capability_matrix():
    assert config.get_supported_language_codes("local") == {
        "zh", "en", "ja", "ko", "yue", "fr", "de", "es", "ru", "it",
    }
    assert config.get_language_codes_ordered("local")[:5] == ["zh", "en", "ja", "ko", "yue"]
    assert config.get_capabilities("local") == {
        "segment_mode": False,
        "speaker_diarization": False,
        "two_way_translation": False,
    }


def test_local_device_config_is_normalized(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_INFERENCE_DEVICE", "auto")
    monkeypatch.setattr(config, "LOCAL_QWEN_ENCODER_DEVICE", "auto")
    monkeypatch.setattr(config, "LOCAL_TRANSLATION_DEVICE", "auto")

    result = config.set_local_inference_config(
        asr_device="VULKAN:2",
        encoder_device="directml",
        translation_device="not-a-device",
    )

    assert result == {
        "backend": config.LOCAL_INFERENCE_BACKEND,
        "server_url": config.LOCAL_INFERENCE_SERVER_URL,
        "remote_timeout_seconds": config.LOCAL_INFERENCE_REMOTE_TIMEOUT_SECONDS,
        "asr_device": "vulkan:2",
        "encoder_device": "gpu",
        "translation_device": "auto",
    }


def test_local_session_emits_source_before_translation_and_preserves_final_side_effects():
    session = _session()
    session.loop = asyncio.new_event_loop()
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    translator = Mock()
    translator.translate.return_value = "你好，世界。"
    captured = []
    session.broadcast_callback = AsyncMock(side_effect=lambda frame: captured.append(frame))
    session._finalize_sentence_async = AsyncMock()
    session.get_osc_translation_enabled = Mock(return_value=False)
    session._subtitle_pipeline = LocalSubtitlePipeline(
        lambda: translator, session._publish_local_frame,
        session._finalize_local_row, session._broadcast_local_error,
    )
    try:
        session._present_recognition("Hello, world.", True, "en")
        session._subtitle_pipeline.close()
        session.loop.run_until_complete(asyncio.sleep(0))
        assert captured[0]["local_segments"][0]["source"] == "Hello, world."
        assert captured[0]["local_segments"][0]["translation"] == ""
        assert captured[1]["local_segments"][0]["translation"] == "你好，世界。"
        assert captured[0]["local_segments"][0]["id"] == captured[1]["local_segments"][0]["id"]
        session.logger.write_to_log.assert_called_once()
        logged = session.logger.write_to_log.call_args.args[0]
        assert [token["text"] for token in logged] == ["Hello, world.", "你好，世界。"]
        session._finalize_sentence_async.assert_awaited_once()
    finally:
        session._subtitle_pipeline.close()
        session.loop.close()


def test_local_session_passes_target_language_to_row_pipeline():
    session = _session()
    session.loop = object()
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session._subtitle_pipeline = Mock()

    session._present_recognition("你好。", False, "zh")

    session._subtitle_pipeline.update.assert_called_once_with(
        "你好。", False, "zh", "zh", translation_enabled=True, semantic_suffix=None,
    )


def test_semantic_final_passes_known_suffix_to_row_pipeline_before_next_partial():
    session = _session()
    session.loop = object()
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session._subtitle_pipeline = Mock()

    session._present_recognition("First sentence.", True, "en", {"semantic_suffix_text": "Second sentence"})
    session._present_recognition("Second sentence", False, "en")

    calls = session._subtitle_pipeline.update.call_args_list
    assert calls[0].args == ("First sentence.", True, "en", "zh")
    assert calls[0].kwargs["semantic_suffix"] == "Second sentence"
    assert calls[1].args == ("Second sentence", False, "en", "zh")
    assert calls[1].kwargs["semantic_suffix"] is None


def test_reused_partial_keeps_detected_language_metadata():
    recognizer = object.__new__(LocalQwenRecognizer)
    recognizer._last_partial_text = "你好"
    recognizer._last_partial_raw = {"language": "zh"}
    recognizer._last_partial_voiced_seq = 2
    recognizer._stream_id = 0
    recognizer._engine = None
    emitted = []
    recognizer._on_result = lambda text, final, raw: emitted.append((text, final, raw))

    recognizer._emit_final("你好", None)

    assert emitted == [("你好", True, {"language": "zh"})]
    assert recognizer._last_partial_raw is None


def test_frozen_gpu_probe_uses_executable_helper(monkeypatch):
    from local_inference import gpu_devices

    gpu_devices._probe_cache = None
    monkeypatch.setattr(gpu_devices.sys, "frozen", True, raising=False)
    run = Mock(return_value=SimpleNamespace(stdout='[{"index": 0}]'))
    monkeypatch.setattr(gpu_devices.subprocess, "run", run)

    assert gpu_devices.probe_gpu_devices() == [{"index": 0}]
    assert run.call_args.args[0] == [sys.executable, "--probe-local-gpus"]


def test_qwen_final_clears_utterance_context_instead_of_rolling_history(monkeypatch):
    model = SimpleNamespace(
        tokenize=lambda text, **_kwargs: list(range(len(text))),
        detokenize=lambda _tokens: "",
    )
    encoder = SimpleNamespace(
        encode=Mock(return_value=(np.zeros((2, 4), dtype=np.float32), 0.0))
    )
    vendor = SimpleNamespace(
        encoder=encoder,
        model=model,
        _build_prompt_embd=Mock(return_value=np.zeros((2, 4), dtype=np.float32)),
    )
    engine = object.__new__(Qwen3ASREngine)
    engine._engine = vendor
    engine._context = "an earlier finalized sentence"
    engine._corpus_text = "persistent proper noun"
    engine._draft_tokens = [9]
    engine.language = "en"
    engine._decode = Mock(return_value={"text": "fresh sentence", "tokens": [1, 2]})
    monkeypatch.setattr(config, "LOCAL_QWEN_LOG_PIPELINE_TIMING", False)

    assert engine.transcribe(np.ones(512, dtype=np.float32), update_context=True)["text"] == "fresh sentence"
    assert vendor._build_prompt_embd.call_args.kwargs["context"] == (
        "persistent proper noun\nan earlier finalized sentence"
    )
    assert engine._context == ""
    assert engine._prompt_context() == "persistent proper noun"


def _recognizer_without_models(monkeypatch, captured=None):
    monkeypatch.setattr(config, "VAD_ENABLED", False)
    monkeypatch.setattr(config, "LOCAL_VAD_MODE", "disabled")
    monkeypatch.setattr(config, "LOCAL_SEMANTIC_BOUNDARY_SCOUT_ENABLED", False)
    results = captured if captured is not None else []
    recognizer = LocalQwenRecognizer(
        lambda text, final, raw: results.append((text, final, raw)),
        Mock(),
    )
    return recognizer, results


def test_vad_semantic_trim_keeps_exact_live_suffix(monkeypatch):
    recognizer, _results = _recognizer_without_models(monkeypatch)
    audio = np.arange(6 * 16000, dtype=np.float32)
    recognizer._vad._speech_buffer = [audio]
    recognizer._vad._confidence_history = [1.0]
    recognizer._vad._speech_samples = audio.size
    recognizer._vad._is_speaking = True

    removed = recognizer._vad.trim_prefix(int(3.7 * 16000))
    tail, duration = recognizer._vad.peek_buffer()

    assert removed == int(3.7 * 16000)
    assert np.array_equal(tail, audio[removed:])
    assert duration == pytest.approx(2.3)
    assert recognizer._vad.is_speaking


def test_semantic_commit_trims_audio_and_orders_final_before_tail(monkeypatch):
    captured = []
    recognizer, _results = _recognizer_without_models(monkeypatch, captured)
    audio = np.ones(6 * 16000, dtype=np.float32)
    recognizer._vad._speech_buffer = [audio]
    recognizer._vad._confidence_history = [1.0]
    recognizer._vad._speech_samples = audio.size
    recognizer._vad._is_speaking = True
    recognizer._locate_boundary_seconds = Mock(
        return_value=(4.5, "nemotron_timestamp")
    )
    recognizer._enqueue_transcribe = Mock()
    commit = BoundaryCommit(
        prefix="第一句话。",
        suffix="第二句话正在继续",
        boundary_index=6,
        agreement_count=2,
    )

    assert recognizer._commit_semantic_boundary(
        commit,
        audio=audio[: 5 * 16000],
        raw={"language": "zh", "text": commit.prefix + commit.suffix},
    )

    assert captured[0][0:2] == ("第一句话。", True)
    assert captured[0][2]["semantic_boundary"]["method"] == "nemotron_timestamp"
    tail = recognizer._enqueue_transcribe.call_args.args[0]
    assert tail.size == int(2.3 * 16000)
    assert recognizer._enqueue_transcribe.call_args.kwargs == {
        "is_final": False,
        "present": True,
    }
    assert recognizer._stream_id == 1
    assert recognizer._semantic_stats["trimmed_samples"] == int(3.7 * 16000)


def test_qwen_prefix_locator_requires_an_independent_full_window_match(monkeypatch):
    recognizer, _results = _recognizer_without_models(monkeypatch)
    engine = Mock()
    engine.probe_transcribe.return_value = {"text": "完全不同的识别结果"}
    recognizer._ensure_engine = Mock(return_value=engine)

    located = recognizer._qwen_prefix_locator(
        np.ones(3 * 16000, dtype=np.float32),
        "已经确认的句子。",
    )

    assert located is None
    assert engine.probe_transcribe.call_count == 2


def test_final_requests_are_queued_instead_of_overwritten(monkeypatch):
    recognizer, _results = _recognizer_without_models(monkeypatch)
    recognizer._executor = object()
    recognizer._try_start_transcribe_locked = Mock()

    recognizer._enqueue_transcribe(np.ones(512, dtype=np.float32), is_final=True)
    recognizer._enqueue_transcribe(np.ones(1024, dtype=np.float32), is_final=True)

    assert [request.audio.size for request in recognizer._waiting_finals] == [512, 1024]


def test_max_window_rollover_processes_current_real_audio(monkeypatch):
    recognizer, _results = _recognizer_without_models(monkeypatch)
    monkeypatch.setattr(config, "LOCAL_VAD_MAX_SPEECH_DURATION", 1.0)
    old_audio = np.ones(16000, dtype=np.float32)
    recognizer._vad._speech_buffer = [old_audio]
    recognizer._vad._confidence_history = [1.0]
    recognizer._vad._speech_samples = old_audio.size
    recognizer._vad._is_speaking = True
    recognizer._enqueue_transcribe = Mock()
    recognizer._start_boundary_scout_window = Mock()
    current = np.full(512, 0.25, dtype=np.float32)

    recognizer._process_chunk(current)

    tail, _duration = recognizer._vad.peek_buffer()
    assert np.array_equal(tail, current)
    recognizer._enqueue_transcribe.assert_called_once()
    assert np.array_equal(recognizer._enqueue_transcribe.call_args.args[0], old_audio)


def test_audio_worker_dequeues_only_while_holding_processing_lock(monkeypatch):
    recognizer, _results = _recognizer_without_models(monkeypatch)

    class LockCheckingQueue(queue.Queue):
        def get_nowait(self):
            assert recognizer._lock._is_owned()
            return super().get_nowait()

    recognizer._audio_queue = LockCheckingQueue(maxsize=128)
    recognizer._feed_samples = Mock()
    recognizer._running = True
    recognizer._audio_queue.put_nowait(b"\x00\x00" * 512)
    recognizer._audio_ready.set()
    worker = threading.Thread(target=recognizer._worker_loop)
    worker.start()
    worker.join(timeout=1.0)

    # One chunk was processed; stop the polling worker without starting models.
    with recognizer._queue_gate:
        recognizer._running = False
    recognizer._audio_ready.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    recognizer._feed_samples.assert_called_once()


def test_stop_drains_final_queued_behind_active_partial(monkeypatch):
    captured = []
    recognizer, _results = _recognizer_without_models(monkeypatch, captured)
    partial_started = threading.Event()
    release_partial = threading.Event()
    calls = []

    def transcribe(_audio, *, is_final):
        calls.append(is_final)
        if not is_final:
            partial_started.set()
            assert release_partial.wait(timeout=2.0)
            return "draft", {"text": "draft", "language": "en"}
        return "final text", {"text": "final text", "language": "en"}

    recognizer._transcribe = transcribe
    recognizer._executor = ThreadPoolExecutor(max_workers=1)
    recognizer._running = True
    recognizer._enqueue_transcribe(np.ones(512, dtype=np.float32), is_final=False)
    assert partial_started.wait(timeout=1.0)

    tail = np.ones(1024, dtype=np.float32)
    recognizer._vad._speech_buffer = [tail]
    recognizer._vad._confidence_history = [1.0]
    recognizer._vad._speech_samples = tail.size
    recognizer._vad._is_speaking = True

    stopper = threading.Thread(target=recognizer.stop)
    stopper.start()
    release_partial.set()
    stopper.join(timeout=3.0)

    assert not stopper.is_alive()
    assert calls == [False, True]
    assert any(text == "final text" and final for text, final, _raw in captured)


def test_result_callback_failure_does_not_strand_later_final(monkeypatch):
    callback_calls = []
    errors = []

    def on_result(text, _final, _raw):
        callback_calls.append(text)
        if text == "first":
            raise RuntimeError("presentation failed")

    recognizer = LocalQwenRecognizer(on_result, errors.append)
    recognizer._transcribe = lambda audio, *, is_final: (
        ("first" if audio.size == 512 else "second"),
        {"text": "first" if audio.size == 512 else "second"},
    )
    recognizer._executor = ThreadPoolExecutor(max_workers=1)
    recognizer._enqueue_transcribe(np.ones(512, dtype=np.float32), is_final=True)
    recognizer._enqueue_transcribe(np.ones(1024, dtype=np.float32), is_final=True)

    recognizer._wait_for_transcriptions()
    recognizer._executor.shutdown(wait=True)

    assert callback_calls == ["first", "second"]
    assert len(errors) == 1
    assert str(errors[0]) == "presentation failed"


def test_empty_suffix_final_ends_replay_epoch_before_next_utterance(monkeypatch):
    recognizer, _results = _recognizer_without_models(monkeypatch)
    recognizer._replayed_committed_text = "We saw the blue car."
    recognizer._replay_max_lexical_chars = 32
    recognizer._last_partial_text = "The blue car"
    recognizer._last_emitted_partial_text = "The blue car"
    recognizer._last_partial_raw = {"text": "The blue car"}
    completed = Mock()
    completed.result.return_value = None
    request = SimpleNamespace(is_final=True, stream_id=0)

    recognizer._on_transcription_done(completed, request=request)
    next_text, _raw = recognizer._deduplicate_replay(
        "The blue car was gone",
        {"text": "The blue car was gone"},
    )

    assert recognizer._replayed_committed_text == ""
    assert recognizer._replay_max_lexical_chars == 0
    assert recognizer._last_partial_text == ""
    assert recognizer._last_emitted_partial_text == ""
    assert recognizer._last_partial_raw is None
    assert next_text == "The blue car was gone"


def test_session_stop_keeps_final_gate_when_run_thread_is_still_draining(monkeypatch):
    session = _session()
    stop_event = threading.Event()
    pipeline = Mock()

    class StillDrainingThread:
        def is_alive(self):
            return True

        def join(self, timeout):
            assert timeout == 15.0

    session._local_stop_event = stop_event
    session.stop_event = stop_event
    session._subtitle_pipeline = pipeline
    session.loop = object()
    session.thread = StillDrainingThread()
    session.ws = object()
    session._stop_audio_streamer = Mock()

    session.stop()
    session._on_recognition_result(
        "late final",
        True,
        {"language": "en"},
    )

    assert session._local_stop_event is stop_event
    assert session.ws is not None
    pipeline.update.assert_called_once()
