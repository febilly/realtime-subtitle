import importlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

# Some legacy test modules install a deliberately minimal ``config`` stub at
# collection time. Load this integration test against the real application
# config without changing the module seen by those tests.
_previous_config = sys.modules.pop("config", None)
try:
    config = importlib.import_module("config")
    from local_inference.asr_qwen3 import Qwen3ASREngine
    from local_inference.recognizer import LocalQwenRecognizer
    from local_session import LocalInferenceSession
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
        "asr_device": "vulkan:2",
        "encoder_device": "gpu",
        "translation_device": "auto",
    }


def test_local_session_emits_source_and_hymt_translation_tokens():
    session = _session()
    session.loop = object()
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session._translator = Mock()
    session._translator.translate.return_value = "你好，世界。"
    captured = []
    session._process_soniox_response = Mock(
        side_effect=lambda response, *_args: (captured.append(response) or (0, False, None))
    )

    session._present_recognition("Hello, world.", True, "en")

    session._translator.translate.assert_called_once_with(
        "Hello, world.",
        source_language="en",
        target_language="zh",
        is_partial=False,
    )
    assert captured == [{
        "tokens": [
            {
                "text": "Hello, world.",
                "is_final": True,
                "speaker": "0",
                "translation_status": "original",
                "language": "en",
                "source_language": "en",
            },
            {
                "text": "你好，世界。",
                "is_final": True,
                "speaker": "0",
                "translation_status": "translation",
                "language": "zh",
                "source_language": "en",
            },
        ],
        "endpoint_detected": True,
    }]


def test_local_session_skips_translation_for_target_language():
    session = _session()
    session.loop = object()
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session._translator = Mock()
    session._process_soniox_response = Mock(return_value=(0, False, None))

    session._present_recognition("你好。", False, "zh")

    session._translator.translate.assert_not_called()
    response = session._process_soniox_response.call_args.args[0]
    assert len(response["tokens"]) == 1
    assert response["tokens"][0]["translation_status"] == "original"
    assert response["endpoint_detected"] is False


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
