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
    # All entries from the official base table, with tl canonicalized to fil.
    assert config.get_supported_language_codes("local") == set(
        "zh en fr pt es ja tr ru ar ko th it de vi ms id fil hi zh-hant "
        "pl cs nl km my fa gu ur te mr he bn ta uk bo kk mn ug yue".split()
    )
    ordered = config.get_language_codes_ordered("local")
    assert ordered[:4] == ["en", "zh", "ja", "ko"]
    assert len(ordered) == len(set(ordered)) == 38
    from shared.hymt_languages import HYMT2_LANGUAGE_NAMES
    assert ordered[4:] == sorted(ordered[4:], key=lambda code: HYMT2_LANGUAGE_NAMES[code].casefold())
    # A target need not be recognizable as ASR input.
    assert "bo" in ordered and "bo" not in _asr_qwen3_module._LANG_MAP
    assert config.get_capabilities("local") == {
        "segment_mode": False,
        "speaker_diarization": False,
        "two_way_translation": True,
    }


@pytest.mark.parametrize("target", "zh en fr pt es ja tr ru ar ko th it de vi ms id fil hi zh-hant pl cs nl km my fa gu ur te mr he bn ta uk bo kk mn ug yue".split())
def test_local_session_accepts_every_hymt_base_target(target):
    session = _session()
    assert session.set_translation_target_lang(target) == (True, "ok")
    assert session.get_translation_target_lang() == target


@pytest.mark.parametrize("value,canonical", [
    ("zh-Hant", "zh-hant"), ("zh_TW", "zh-hant"), ("zh_HK", "zh-hant"),
    ("zh-Hans", "zh"), ("tl", "fil"), ("fil-PH", "fil"),
])
def test_local_target_alias_survives_selection_and_row_pipeline(value, canonical):
    session = _session()
    assert session.set_translation_target_lang(value) == (True, "ok")
    session.loop = object()
    session._subtitle_pipeline = Mock()
    session.translation = "one_way"
    session._present_recognition("Hello.", False, "en")
    assert session._subtitle_pipeline.update.call_args.args == ("Hello.", False, "en", canonical)
    assert config.canonicalize_target_lang(value, "local") == canonical
    assert config.is_supported_language_code(value, "local")


def test_local_session_rejects_unknown_target_without_changing_selection():
    session = _session()
    assert session.set_translation_target_lang("vi")[0]
    assert not session.set_translation_target_lang("not-a-language")[0]
    assert session.get_translation_target_lang() == "vi"


@pytest.mark.parametrize("pair,source,target", [
    (("en", "zh"), "en", "zh"),
    (("en", "zh"), "zh", "en"),
    (("ja", "ko"), "ja", "ko"),
    (("ja", "ko"), "ko", "ja"),
    (("en-US", "zh_TW"), "en", "zh-hant"),
    (("en-US", "zh_TW"), "zh", "en"),
    (("tl", "en"), "fil", "en"),
    (("tl", "en"), "en", "fil"),
])
def test_local_two_way_routes_detected_language_to_partner(pair, source, target):
    session = _session()
    assert session.set_target_langs(*pair) == (True, "ok")
    session.translation = "two_way"
    session.loop = object()
    session._local_stop_event = threading.Event()
    session._subtitle_pipeline = Mock()

    session._on_recognition_result("Speech.", True, {"language": source})

    session._subtitle_pipeline.update.assert_called_once_with(
        "Speech.", True, source, target, translation_enabled=True, semantic_suffix=None,
    )


@pytest.mark.parametrize("pair", [("en", "EN-us"), ("zh", "zh-hant"), ("xx", "en"), ("en", "xx")])
def test_local_two_way_rejects_invalid_pair_without_mutating_selection(pair):
    session = _session()
    assert session.set_target_langs("ja", "ko")[0]
    assert not session.set_target_langs(*pair)[0]
    assert session.get_target_langs() == ("ja", "ko")


@pytest.mark.parametrize("fixed_target,expected", [("ja", "zh-hant"), ("en", "en")])
def test_local_two_way_outside_pair_uses_consistent_fallback(fixed_target, expected):
    session = _session()
    session.set_target_langs("en", "zh-hant")
    session.set_translation_target_lang(fixed_target)
    session.translation = "two_way"
    assert session._osc_translation_language_for_comparison([{"language": "fr"}]) == expected


def test_local_start_and_resume_preserve_two_way_mode(monkeypatch):
    session = _session()
    session._check_models = Mock()
    session._start_relay_link = Mock()
    monkeypatch.setattr("local_session.threading.Thread", Mock())

    assert session.start(None, "pcm_s16le", "two_way", object(), "zh")
    assert session.translation == "two_way"
    session.thread = None
    session.is_paused = True
    assert session.resume()
    assert session.translation == "two_way"


@pytest.mark.parametrize("mode", ["fast", "hybrid", "accurate"])
def test_local_two_way_pipeline_llm_and_osc_share_direction(monkeypatch, mode):
    import soniox_session

    monkeypatch.setattr(soniox_session, "is_llm_refine_available", lambda: True)
    send_osc = Mock(return_value=1)
    monkeypatch.setattr(soniox_session.osc_manager, "add_message_and_send", send_osc)
    monkeypatch.setattr(soniox_session.osc_manager, "update_message_and_send", Mock())
    session = _session()
    session.translation = "two_way"
    session.set_target_langs("en", "zh-hant")
    session.set_translation_mode(mode)
    session.set_osc_translation_enabled(True)
    session.loop = asyncio.new_event_loop()
    session.broadcast_callback = AsyncMock()
    session._perform_refine = AsyncMock(return_value={"status": "ok", "no_change": True})
    session._perform_translate = AsyncMock(side_effect=[
        {"status": "ok", "translation": "你好。"},
        {"status": "ok", "translation": "Hello."},
    ])
    mt_calls, finalized = [], []

    def make_translator():
        translator = Mock()
        def translate(source, **kwargs):
            mt_calls.append((source, kwargs["target_language"]))
            return "你好。" if kwargs["target_language"] == "zh-hant" else "Hello."
        translator.translate.side_effect = translate
        return translator

    def finalize(row):
        finalized.append(row)
        tokens = session._local_row_tokens(row)
        session.loop.run_until_complete(session._finalize_sentence_async(
            "0", tokens[:1], tokens[1:], row["id"], announce=False,
        ))

    pipeline = LocalSubtitlePipeline(make_translator, lambda _frame: None, finalize, pytest.fail)
    session._subtitle_pipeline = pipeline
    pipeline.set_machine_translation(mode != "accurate")
    try:
        session._present_recognition("Hello.", True, "en")
        session._present_recognition("你好。", True, "zh")
        pipeline.close()
        assert [(row["source_language"], row["target_language"]) for row in finalized] == [
            ("en", "zh-hant"), ("zh", "en"),
        ]
        assert all(row["requires_translation"] for row in finalized)
        expected = [("Hello.", "zh-hant"), ("你好。", "en")]
        assert mt_calls == ([] if mode == "accurate" else expected)
        llm = session._perform_translate if mode == "accurate" else session._perform_refine
        assert [call.kwargs["target_lang"] for call in llm.await_args_list] == (
            [] if mode == "fast" else ["zh-hant", "en"]
        )
        assert [call.args[0] for call in send_osc.call_args_list] == ["你好。", "Hello."]
        frames = [call.args[0] for call in session.broadcast_callback.await_args_list]
        assert [frame["target_lang"] for frame in frames if frame["type"] == "refine_result"] == ["zh-hant", "en"]
    finally:
        pipeline.close()
        session.loop.close()


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


@pytest.mark.parametrize("code,name", [
    ("zh", "Chinese"), ("en", "English"), ("yue", "Cantonese"), ("ar", "Arabic"),
    ("de", "German"), ("fr", "French"), ("es", "Spanish"), ("pt", "Portuguese"),
    ("id", "Indonesian"), ("it", "Italian"), ("ko", "Korean"), ("ru", "Russian"),
    ("th", "Thai"), ("vi", "Vietnamese"), ("ja", "Japanese"), ("tr", "Turkish"),
    ("hi", "Hindi"), ("ms", "Malay"), ("nl", "Dutch"), ("sv", "Swedish"),
    ("da", "Danish"), ("fi", "Finnish"), ("pl", "Polish"), ("cs", "Czech"),
    ("fil", "Filipino"), ("fa", "Persian"), ("el", "Greek"), ("hu", "Hungarian"),
    ("mk", "Macedonian"), ("ro", "Romanian"),
])
def test_qwen_parses_model_detected_language_for_all_supported_languages(code, name):
    # Latin-script languages must use the model's metadata, not a script guess.
    assert _asr_qwen3_module.parse_qwen_asr_output(
        f"language {name}<asr_text>recognized speech"
    ) == (code, name, "recognized speech")


@pytest.mark.parametrize("raw,expected", [
    ("language vIeTnAmEsE\n<asr_text>  Xin chào.  ", ("vi", "Vietnamese", "Xin chào.")),
    ("language Cantonese<asr_text>你好。", ("yue", "Cantonese", "你好。")),
    ("language None<asr_text>", ("", "", "")),
    ("language None<asr_text>heard something", ("", "", "heard something")),
    ("language Unknown<asr_text>speech", ("", "", "speech")),
    ("<asr_text>speech", ("", "", "speech")),
    ("plain text without metadata", ("", "", "plain text without metadata")),
    ("language Thai", ("", "", "")),
    ("language", ("", "", "")),
    ("", ("", "", "")),
])
def test_qwen_handles_empty_incomplete_and_missing_language_metadata(raw, expected):
    assert _asr_qwen3_module.parse_qwen_asr_output(raw) == expected


def test_qwen_forced_language_keeps_plain_transcription_including_literal_language_word():
    assert _asr_qwen3_module.parse_qwen_asr_output(
        "language is important.", "en"
    ) == ("en", "English", "language is important.")


@pytest.mark.parametrize("language", [None, "Vietnamese"])
def test_qwen_prompt_requests_metadata_only_in_auto_mode(language):
    from local_inference.qwen3_asr_mtmd import MtmdQwenASR

    engine = object.__new__(MtmdQwenASR)
    engine.ID_IM_START, engine.ID_IM_END = 512, 513
    engine.ID_AUDIO_START, engine.ID_AUDIO_END, engine.ID_ASR_TEXT = 514, 515, 516
    engine.model = SimpleNamespace(tokenize=lambda text: list(text.encode("ascii")), n_embd=1)
    engine.embedding_table = np.arange(517, dtype=np.float32).reshape(-1, 1)
    # In auto mode a replayed prefix must retain its language header as well.
    prefix = "language Vietnamese<asr_text>Xin" if language is None else "Xin"
    result = engine._build_prompt_embd(np.array([[-1]], dtype=np.float32), prefix, "", language)
    suffix = [515, 513, 512] + list(b"assistant\n")
    if language:
        suffix += list(f"language {language}".encode("ascii")) + [516]
    suffix += list(prefix.encode("ascii"))
    audio_index = list(result[:, 0]).index(-1)
    assert list(result[audio_index + 1:, 0]) == suffix


def _qwen_without_weights(monkeypatch, outputs):
    engine = object.__new__(Qwen3ASREngine)
    engine._engine = SimpleNamespace(
        encoder=SimpleNamespace(encode=lambda audio: (np.zeros((2, 4), dtype=np.float32), 0.0)),
        _build_prompt_embd=Mock(return_value=np.zeros((2, 4), dtype=np.float32)),
    )
    engine._context = ""
    engine._corpus_text = ""
    engine._draft_tokens = []
    engine.language = None
    engine._decode = Mock(side_effect=outputs)
    monkeypatch.setattr(_asr_qwen3_module.config, "LOCAL_QWEN_LOG_PIPELINE_TIMING", False)
    return engine


def test_qwen_auto_returns_clean_transcript_and_rechecks_language_each_update(monkeypatch):
    first_tokens, second_tokens = [1, 2, 3], [4, 5, 6]
    engine = _qwen_without_weights(monkeypatch, [
        {"text": "language French<asr_text>Bonjour.", "tokens": first_tokens},
        {"text": "language Vietnamese<asr_text>Xin chào.", "tokens": second_tokens},
        {"text": "language Thai<asr_text>สวัสดี", "tokens": [7]},
    ])
    audio = np.ones(512, dtype=np.float32)
    assert engine.transcribe(audio, update_context=False) == {
        "text": "Bonjour.", "language": "fr", "language_name": "French",
    }
    assert engine._draft_tokens == first_tokens  # Keep the full metadata-bearing draft.
    assert engine.transcribe(audio, update_context=False) == {
        "text": "Xin chào.", "language": "vi", "language_name": "Vietnamese",
    }
    assert engine._draft_tokens == second_tokens
    assert engine.transcribe(audio) == {
        "text": "สวัสดี", "language": "th", "language_name": "Thai",
    }
    assert engine._draft_tokens == []
    assert engine.language is None  # Detection must never force the next update.
    assert all(call.kwargs["language"] is None for call in engine._engine._build_prompt_embd.call_args_list)


@pytest.mark.parametrize("raw", ["language None<asr_text>", "language Vietnamese", ""])
def test_qwen_empty_audio_or_incomplete_header_never_becomes_a_subtitle(monkeypatch, raw):
    engine = _qwen_without_weights(monkeypatch, [{"text": raw, "tokens": [1, 2]}])
    engine._draft_tokens = [3]
    assert engine.transcribe(np.ones(512, dtype=np.float32), update_context=False) is None
    assert engine._draft_tokens == []


def test_qwen_changing_forced_language_clears_incompatible_draft(monkeypatch):
    engine = _qwen_without_weights(monkeypatch, [{"text": "Bonjour.", "tokens": [1]}])
    engine._draft_tokens = [2, 3]
    engine.set_language(" FR ")
    assert engine._draft_tokens == []
    assert engine.transcribe(np.ones(512, dtype=np.float32), update_context=False) == {
        "text": "Bonjour.", "language": "fr", "language_name": "French",
    }
    assert engine._engine._build_prompt_embd.call_args.kwargs["language"] == "French"
    engine.set_language("fr")
    assert engine._draft_tokens == [1]
    engine.set_language("auto")
    assert engine.language is None and engine._draft_tokens == []


def test_qwen_detected_language_reaches_the_translation_pipeline(monkeypatch):
    engine = _qwen_without_weights(monkeypatch, [
        {"text": "language French<asr_text>Bonjour.", "tokens": [1]},
    ])
    result = engine.transcribe(np.ones(512, dtype=np.float32))
    session = _session()
    session._subtitle_pipeline = Mock()
    session._local_stop_event = threading.Event()
    session.loop = object()
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session._on_recognition_result(result["text"], True, result)
    session._subtitle_pipeline.update.assert_called_once_with(
        "Bonjour.", True, "fr", "zh", translation_enabled=True, semantic_suffix=None,
    )


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


def test_pipeline_without_machine_translation_finalizes_rows_for_the_llm():
    translator = Mock()
    finals = []
    pipeline = LocalSubtitlePipeline(lambda: translator, lambda frame: None, finals.append, Mock())
    pipeline.set_machine_translation(False)
    pipeline.update("Hello there.", True, "en", "zh")
    pipeline.close()

    translator.translate.assert_not_called()
    assert finals[0]["source"] == "Hello there."
    assert finals[0]["requires_translation"] is True
    assert finals[0]["translation"] == ""


def test_disabling_machine_translation_releases_row_translators():
    translators = []

    def make():
        translator = Mock()
        translator.translate.return_value = "你好"
        translators.append(translator)
        return translator

    pipeline = LocalSubtitlePipeline(make, lambda frame: None, lambda row: None, Mock())
    try:
        pipeline.update("Hello", False, "en", "zh")
        pipeline._executor.submit(lambda: None).result(5)
        assert len(translators) == 1
        pipeline.set_machine_translation(False)
        pipeline._executor.submit(lambda: None).result(5)
        translators[0].close.assert_called_once()
        assert pipeline._translators == {}
    finally:
        pipeline.close()


def _running_local_session(translation_mode="hybrid"):
    session = _session()
    session.translation = "one_way"
    session._local_stop_event = threading.Event()
    session._subtitle_pipeline = Mock()
    session.set_translation_mode(translation_mode)
    return session


def test_switching_to_accurate_unloads_hymt2_immediately():
    session = _running_local_session("hybrid")
    translator = Mock()
    session._translator = translator

    assert session.set_translation_mode("accurate") == (True, "accurate", False)

    assert session.get_translation_mode() == "accurate"
    assert session.get_llm_refine_mode() == "translate"
    session._subtitle_pipeline.set_machine_translation.assert_called_with(False)
    translator.close.assert_called_once()
    assert session._translator is None


def test_switching_back_to_fast_loads_hymt2_in_the_background(monkeypatch):
    session = _running_local_session("accurate")
    loaded = Mock()
    made = threading.Event()
    monkeypatch.setattr(session, "_check_models", Mock())

    def make():
        made.set()
        return loaded

    monkeypatch.setattr(session, "_make_row_translator", make)

    assert session.set_translation_mode("fast") == (True, "fast", False)

    assert made.wait(5)
    for _ in range(100):
        if session._translator is loaded:
            break
        threading.Event().wait(0.01)
    assert session._translator is loaded
    session._check_models.assert_called_once_with(True)
    session._subtitle_pipeline.set_machine_translation.assert_called_with(True)
    # 快速 <-> 混合 keeps the loaded model.
    session.set_translation_mode("hybrid")
    loaded.close.assert_not_called()


def test_load_finishing_after_a_newer_switch_is_discarded(monkeypatch):
    session = _running_local_session("accurate")
    release = threading.Event()
    loaded = Mock()
    monkeypatch.setattr(session, "_check_models", Mock())
    monkeypatch.setattr(session, "_make_row_translator", lambda: (release.wait(5), loaded)[1])

    session.set_translation_mode("fast")
    session.set_translation_mode("accurate")
    release.set()
    for _ in range(100):
        if loaded.close.called:
            break
        threading.Event().wait(0.01)

    loaded.close.assert_called_once()
    assert session._translator is None


def test_missing_hymt2_reports_an_error_instead_of_loading(monkeypatch):
    session = _running_local_session("accurate")
    errors = []
    monkeypatch.setattr(session, "_broadcast_local_error", errors.append)
    monkeypatch.setattr(session, "_check_models", Mock(side_effect=RuntimeError("Hy-MT2 翻译模型未下载")))
    monkeypatch.setattr(session, "_make_row_translator", Mock())

    session.set_translation_mode("fast")
    for _ in range(100):
        if errors:
            break
        threading.Event().wait(0.01)

    assert "未下载" in str(errors[0])
    session._make_row_translator.assert_not_called()


def test_mode_switch_while_stopped_only_records_the_mode():
    session = _session()
    session._make_row_translator = Mock()

    assert session.set_translation_mode("accurate") == (True, "accurate", False)
    assert session._machine_translation_wanted() is False
    assert session.set_translation_mode("fast") == (True, "fast", False)
    assert session._machine_translation_wanted() is True
    session.translation = "none"
    assert session._machine_translation_wanted() is False
    session._make_row_translator.assert_not_called()


def test_translation_off_releases_model_and_disables_translation_rows():
    session = _session()
    session.translation = "one_way"
    session._local_stop_event = threading.Event()
    session._subtitle_pipeline = Mock()
    translator = Mock()
    session._translator = translator
    session._make_row_translator = Mock()
    assert session.set_translation_mode("off") == (True, "off", False)
    assert session.get_translation_mode() == "off"
    assert not session._machine_translation_wanted()
    assert session.get_llm_refine_mode() == "off"
    translator.close.assert_called_once()
    session._subtitle_pipeline.set_machine_translation.assert_called_with(False)
    session.loop = object()
    session._present_recognition("Hello", False, "en")
    assert session._subtitle_pipeline.update.call_args.kwargs["translation_enabled"] is False
    session._make_row_translator.assert_not_called()


def test_translation_off_at_start_does_not_require_the_translation_model(monkeypatch):
    session = _session()
    session._check_models = Mock()
    session._start_relay_link = Mock()
    monkeypatch.setattr("local_session.threading.Thread", Mock())
    assert session.set_translation_mode("off") == (True, "off", False)
    assert session.start(None, "pcm_s16le", "one_way", object(), "zh")
    session._check_models.assert_called_once_with(False)
    assert session.set_translation_mode("fast") == (True, "fast", False)
    assert session._machine_translation_wanted()


def test_hosted_mode_opens_the_relay_link_and_routes_llm_frames_through_it(monkeypatch):
    import local_session
    session = _session()
    links = []

    class FakeLink:
        def __init__(self, on_frame, on_state, run_id):
            self.on_frame, self.on_state, self.run_id = on_frame, on_state, run_id
            self.sent, self.stopped = [], False
            links.append(self)

        def start(self):
            pass

        def send(self, text):
            self.sent.append(text)

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(local_session, "LocalRelayLink", FakeLink)
    monkeypatch.setattr(config, "RELAY_MODE", False)
    session._start_relay_link()
    assert links == []

    monkeypatch.setattr(config, "RELAY_MODE", True)
    monkeypatch.setattr(config, "RELAY_TOKEN", "ss_test")
    session._start_relay_link()
    session._start_relay_link()
    assert len(links) == 1 and links[0].run_id == session.relay_run_id
    assert links[0].on_frame == session._hosted_llm.handle_frame

    session._hosted_llm_send("frame")
    assert links[0].sent == ["frame"]
    session._stop_relay_link()
    assert links[0].stopped
    with pytest.raises(RuntimeError):
        session._hosted_llm_send("frame")


def test_prepaid_exhaustion_falls_back_to_hymt2_without_a_restart(monkeypatch):
    session = _running_local_session("accurate")
    monkeypatch.setattr(session, "_load_machine_translation", Mock())
    session.loop = Mock()
    session._on_hosted_llm_disabled("insufficient_prepaid")
    assert session.get_translation_mode() == "fast"
    session._subtitle_pipeline.set_machine_translation.assert_called_with(True)
    frame = session.broadcast_callback.call_args[0][0]
    assert frame == {"type": "translation_mode_fallback", "reason": "insufficient_prepaid", "needs_restart": False}
