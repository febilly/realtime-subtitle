import asyncio
import concurrent.futures
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

def _install_gemini_session_import_mocks(monkeypatch):
    monkeypatch.delitem(sys.modules, "gemini_session", raising=False)
    # Re-import the shared LLM helper fresh so it binds to this test's mocked
    # config / llm_client rather than a version cached by an earlier test.
    monkeypatch.delitem(sys.modules, "llm_refine", raising=False)
    config = ModuleType("config")
    config.GEMINI_STREAM_DURATION_SECONDS = None
    config.GEMINI_SLEEP_ON_SILENCE = False
    config.SLEEP_IDLE_SECONDS = 30.0
    config.SLEEP_PRE_ROLL_SECONDS = 1.0
    config.SLEEP_SPEECH_GRACE_SECONDS = 0.5
    config.SLEEP_SPEECH_WINDOW_SECONDS = 0.75
    config.SLEEP_VAD_THRESHOLD = 0.2
    config.ROLLOVER_VAD_THRESHOLD = 0.6
    config.USE_TWITCH_AUDIO_STREAM = False
    config.MICROPHONE_DEVICE_ID = ""
    config.OUTPUT_DEVICE_ID = ""
    config.MUTE_MIC_WHEN_VRCHAT_SELF_MUTED = False
    config.TWITCH_CHANNEL = ""
    config.TWITCH_STREAM_QUALITY = "audio_only"
    config.FFMPEG_PATH = "ffmpeg"
    config.is_llm_refine_available = lambda: False
    config.llm_is_hosted = lambda: False
    config.llm_context_bounds = lambda: (1, 1)
    config.llm_timeout_seconds = lambda: 60.0
    config.llm_max_output_tokens = lambda: 128
    config.RELAY_MODE = False
    config.LLM_REFINE_CONTEXT_MIN_COUNT = 1
    config.LLM_REFINE_CONTEXT_MAX_COUNT = 1
    config.LLM_PROMPT_SUFFIX = ""
    config.LLM_REFINE_MAX_TOKENS = 128
    config.LLM_BASE_URL = ""
    config.LLM_MODEL = ""
    config.LLM_TEMPERATURE = 0.2
    config.LLM_REQUEST_HEADERS = {}
    config.LLM_REQUEST_JSON = {}
    config.get_llm_api_key = lambda: ""
    config.normalize_language_code = lambda value: (value or "").strip().lower().split("-")[0].split("_")[0]
    config.GEMINI_FULLWIDTH_PUNCT_FIX = True
    config.OSC_SEND_TEXT_MODE = "smart"
    config.LLM_REFINE_DEFAULT_ENABLED = False
    config.LLM_REFINE_DEFAULT_MODE = "off"
    config.TARGET_LANG_1 = "en"
    config.TARGET_LANG_2 = "zh"
    config.TRANSLATION_TARGET_LANG = "zh"
    config.describe_target_language = lambda lang: lang
    monkeypatch.setitem(sys.modules, "config", config)

    gemini_client = ModuleType("gemini_client")
    gemini_client.connect_live = MagicMock()
    gemini_client.get_api_key = MagicMock(return_value="key")
    monkeypatch.setitem(sys.modules, "gemini_client", gemini_client)

    audio_capture = ModuleType("audio_capture")
    audio_capture.AudioStreamer = MagicMock()
    monkeypatch.setitem(sys.modules, "audio_capture", audio_capture)

    osc_manager = ModuleType("osc_manager")
    osc_manager.osc_manager = MagicMock()
    monkeypatch.setitem(sys.modules, "osc_manager", osc_manager)

    llm_client = ModuleType("llm_client")
    llm_client.LlmConfig = MagicMock()
    llm_client.chat_completion = MagicMock()
    llm_client.extract_answer_tag = lambda text: text
    llm_client.LlmError = RuntimeError
    monkeypatch.setitem(sys.modules, "llm_client", llm_client)


def _run_immediately_factory():
    def run_immediately(coro, _loop):
        asyncio.run(coro)
        future = concurrent.futures.Future()
        future.set_result(None)
        return future

    return run_immediately


def test_gemini_input_transcription_becomes_original_token(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    logger = MagicMock()
    session = module.GeminiSession(logger, broadcast)
    session.translation = "one_way"

    count, should_end, reason = session._process_stream_response(
        {
            "serverContent": {
                "inputTranscription": {"text": "hello ", "languageCode": "en-US"},
            }
        },
        [],
        0,
        object(),
    )
    assert count == 1
    assert should_end is False
    assert reason is None
    token = updates[-1]["final_tokens"][0]
    assert token["text"] == "hello "
    assert token["translation_status"] == "original"
    assert token["language"] == "en"
    assert token["is_final"] is True


def test_gemini_output_transcription_becomes_translation_token(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"

    all_final = []
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "hello", "languageCode": "en"}}},
        all_final,
        0,
        object(),
    )
    count, should_end, _ = session._process_stream_response(
        {"serverContent": {"outputTranscription": {"text": "你好"}}},
        all_final,
        1,
        object(),
    )
    assert count == 2
    assert should_end is False
    token = updates[-1]["final_tokens"][0]
    assert token["text"] == "你好"
    assert token["translation_status"] == "translation"
    assert token["source_language"] == "en"


def test_gemini_turn_complete_emits_internal_end_marker_not_displayed(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    logger = MagicMock()
    session = module.GeminiSession(logger, broadcast)
    session.translation = "one_way"

    count, should_end, reason = session._process_stream_response(
        {
            "serverContent": {
                "inputTranscription": {"text": "hello", "languageCode": "en"},
                "turnComplete": True,
            }
        },
        [],
        0,
        object(),
    )
    assert count == 2  # "hello" + internal "<end>"
    assert should_end is False
    assert reason is None
    assert [t["text"] for t in updates[-1]["final_tokens"]] == ["hello"]
    logger.write_to_log.assert_called_once()
    logged_tokens = logger.write_to_log.call_args.args[0]
    assert [t["text"] for t in logged_tokens] == ["hello"]


def test_split_into_sentence_lines(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    session = module.GeminiSession(MagicMock(), lambda *_: None)
    split = session._split_into_sentence_lines

    assert split("看得人是脊背发凉。事发江西") == ["看得人是脊背发凉。", "事发江西"]
    assert split("甲。乙丙！丁") == ["甲。", "乙丙！", "丁"]
    assert split("在睡觉。") == ["在睡觉。"]
    assert split("等等…好") == ["等等…好"]      # ellipsis trails off, no split
    assert split("no punct tail") == ["no punct tail"]
    assert split("版本 3.10 已发布。下一句") == ["版本 3.10 已发布。", "下一句"]
    assert split("Meet at 9 a.m. tomorrow.") == ["Meet at 9 a.m. tomorrow."]
    assert split("He said hello.\" Next") == ["He said hello.\"", "Next"]
    assert split("") == []


def test_gemini_go_away_requests_stream_end(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    async def broadcast(_data):
        return None

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"

    _count, should_end, reason = session._process_stream_response(
        {"goAway": {"timeLeft": "10s"}},
        [],
        0,
        object(),
    )
    assert should_end is True
    assert reason == "server goAway"


@pytest.mark.parametrize("relay_mode", [False, True])
def test_gemini_go_away_switches_to_replacement_stream_in_direct_and_relay_modes(monkeypatch, relay_mode):
    _install_gemini_session_import_mocks(monkeypatch)
    import config
    import gemini_session as module

    config.RELAY_MODE = relay_mode
    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    async def broadcast(_data):
        return None

    class FakeRouter:
        def __init__(self, *args, **kwargs):
            self.target = None

        def set_target(self, ws):
            self.target = ws
            return True

        def clear_target(self, expected_current=None):
            if self.target is expected_current:
                self.target = None
            return True

        def close(self):
            return None

    class FirstWs:
        def recv(self, timeout=None):
            return '{"goAway":{"timeLeft":"10s"}}'

        def close(self):
            return None

    class ReplacementWs:
        def __init__(self, session):
            self.session = session

        def recv(self, timeout=None):
            self.session.stop_event.set()
            raise TimeoutError()

        def close(self):
            return None

    monkeypatch.setattr(module, "AudioSendRouter", FakeRouter)

    session = module.GeminiSession(MagicMock(), broadcast)
    session._start_audio_streamer = MagicMock()
    session._stop_audio_streamer = MagicMock()
    session._close_stream_state = MagicMock()

    first = module._StreamState(
        ws=FirstWs(),
        index=1,
        api_key="key",
        started_at=module.time.monotonic(),
        ready_at=module.time.monotonic(),
        all_final_tokens=[],
    )
    replacement = module._StreamState(
        ws=ReplacementWs(session),
        index=2,
        api_key="key",
        started_at=module.time.monotonic(),
        ready_at=module.time.monotonic(),
        all_final_tokens=[],
    )

    session._open_stream_state = MagicMock(return_value=first)
    session._open_and_switch_to_replacement_stream = MagicMock(return_value=(replacement, "key", 2))

    session._run_session("key", "pcm_s16le", "one_way", "zh", object())

    session._open_and_switch_to_replacement_stream.assert_called_once()


def test_translation_none_mode_drops_output_transcription(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "none"

    count, _should_end, _reason = session._process_stream_response(
        {"serverContent": {"outputTranscription": {"text": "ignored"}}},
        [],
        0,
        object(),
    )
    assert count == 0
    assert not updates


def test_stream_rollover_prepare_age_uses_fixed_patience_window(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    async def broadcast(_data):
        return None

    session = module.GeminiSession(MagicMock(), broadcast)

    assert session._stream_rollover_switch_patience(170.0) == 25.0
    assert session._stream_rollover_prepare_age(170.0) == 143.0
    assert session._stream_rollover_switch_patience(30.0) == 15.0
    assert session._stream_rollover_prepare_age(30.0) == 13.0
    assert session._stream_rollover_switch_patience(10.0) == 5.0
    assert session._stream_rollover_prepare_age(10.0) == 4.0


def test_rollover_warmup_does_not_finalize_frontend_non_final(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    monkeypatch.setattr(module, "GEMINI_STREAM_DURATION_SECONDS", 30.0)

    async def broadcast(_data):
        return None

    class FakeWs:
        def recv(self, timeout=None):
            raise TimeoutError()

        def close(self):
            return None

    class FakeRouter:
        def __init__(self, *args, **kwargs):
            self.target = None

        def set_target(self, ws):
            self.target = ws
            return True

        def switch_target(self, ws, expected_current=None):
            self.target = ws
            return True

        def silence_ready(self, min_observed_at=None):
            return False

        def consecutive_silence_seconds(self):
            return 0.0

        def close(self):
            return None

    class FakeSilenceSender:
        error = None

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(module, "AudioSendRouter", FakeRouter)

    session = module.GeminiSession(MagicMock(), broadcast)
    session._start_audio_streamer = MagicMock()
    session._stop_audio_streamer = MagicMock()
    session._fetch_api_key_for_next_stream = MagicMock(return_value="next-key")
    session._make_rollover_silence_sender = MagicMock(return_value=FakeSilenceSender())
    session._should_prepare_rollover_stream = MagicMock(return_value=True)
    session._should_force_rollover_switch = MagicMock(return_value=False)
    session._broadcast_preserve_existing_subtitles = MagicMock()

    opened_warming_flags = []

    def open_stream(api_key, stream_index, audio_format, translation, translation_target_lang, *, warming=False):
        opened_warming_flags.append(warming)
        return module._StreamState(
            ws=FakeWs(),
            index=stream_index,
            api_key=api_key,
            started_at=module.time.monotonic(),
            ready_at=module.time.monotonic(),
            all_final_tokens=[],
        )

    def drain_warmup(_stream):
        session.stop_event.set()
        return True

    session._open_stream_state = MagicMock(side_effect=open_stream)
    session._drain_warmup_stream = MagicMock(side_effect=drain_warmup)

    session._run_session("key", "pcm_s16le", "one_way", "zh", object())

    assert opened_warming_flags == [False, True]
    session._broadcast_preserve_existing_subtitles.assert_not_called()


def test_fix_fullwidth_punctuation_after_cjk_ja(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    fix = module.fix_fullwidth_punctuation_after_cjk_ja

    # Half-width punctuation preceded by CJK/JA character becomes full-width.
    assert fix("你好,世界。") == "你好，世界。"
    assert fix("好?真的!对") == "好？真的！对"
    # Consecutive occurrences each convert
    assert fix("中,中,中") == "中，中，中"
    # Japanese Hiragana / Katakana
    assert fix("ごめん,") == "ごめん，"
    assert fix("ゲーム.") == "ゲーム。"
    # At least one non-CJK/JA neighbor leaves the punctuation untouched (or if not preceded by CJK/JA).
    assert fix("价格3.14元") == "价格3.14元"
    assert fix("他说hello,world") == "他说hello,world"
    assert fix("A,B") == "A,B"
    assert fix("http://a.b/c 中") == "http://a.b/c 中"
    # The following character is ignored, so "中,。" becomes "中，。" because ',' is preceded by '中'
    assert fix("中,。") == "中，。"
    # Boundary case using prev_char
    assert fix(",世界", prev_char="中") == "，世界"
    assert fix(",世界", prev_char="A") == ",世界"
    # No matching pattern / empty input.
    assert fix("没标点中文") == "没标点中文"
    assert fix("") == ""
    assert fix(None) == ""


def test_fix_fullwidth_punctuation_disabled_is_noop(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    monkeypatch.setattr(module, "GEMINI_FULLWIDTH_PUNCT_FIX", False)
    assert module.fix_fullwidth_punctuation_after_cjk_ja("你好,世界") == "你好,世界"


def test_boundary_punctuation_conversion_success_streaming(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []
    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"

    # Chunk 1: Ends with CJK character "你好"
    tokens = []
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "你好", "languageCode": "zh"}}},
        tokens,
        0,
        object(),
    )
    # Chunk 2: Starts with a half-width comma "," and followed by CJK "世界"
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": ",世界", "languageCode": "zh"}}},
        tokens,
        1,
        object(),
    )

    emitted_texts = []
    for update in updates:
        for t in update.get("final_tokens", []):
            if t.get("text") and t.get("text") != "<end>":
                emitted_texts.append(t["text"])

    assert "你好" in emitted_texts
    assert "，世界" in emitted_texts


def test_strip_space_before_east_asian_punctuation(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    strip = module.strip_space_before_east_asian_punctuation

    # Gemini streams the period as its own " 。" chunk; the leading space goes.
    assert strip(" 。") == "。"
    assert strip("ごめん 。") == "ごめん。"
    assert strip("その時寝てるぴ 。") == "その時寝てるぴ。"
    assert strip("中文 ，下一句") == "中文，下一句"
    assert strip("x  。") == "x。"          # collapse multiple spaces
    assert strip("ぴ　。") == "ぴ。"          # full-width space too
    # Spaces not before East Asian punctuation are untouched.
    assert strip("英文 word") == "英文 word"
    assert strip("a . b") == "a . b"
    assert strip("正常。无空格") == "正常。无空格"
    assert strip("") == ""
    assert strip(None) == ""


def test_normalize_gemini_text_combines_both_fixes(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    # Space-before-punct strip and half-width->full-width fix both apply.
    assert module.normalize_gemini_text("ごめん 。") == "ごめん。"
    assert module.normalize_gemini_text("你好,世界") == "你好，世界"

    # The disable flag only gates the full-width conversion; space strip stays on.
    monkeypatch.setattr(module, "GEMINI_FULLWIDTH_PUNCT_FIX", False)
    assert module.normalize_gemini_text("你好,世界 。") == "你好,世界。"


def test_input_transcription_applies_fullwidth_punct_fix(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"

    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "你好,世界 。", "languageCode": "zh"}}},
        [],
        0,
        object(),
    )
    token = updates[-1]["final_tokens"][0]
    assert token["translation_status"] == "original"
    # Half-width comma -> full-width, and the spurious space before 。 is stripped.
    assert token["text"] == "你好，世界。"


def _separator_in_updates(updates):
    return any(
        token.get("is_separator")
        for update in updates
        for token in update.get("final_tokens", [])
    )


def test_same_language_source_punctuation_triggers_segmentation(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.loop = object()  # required by _trigger_sentence_finalization

    # Speech is already in the target language (zh): there is no translation
    # token, but the source ends with 。 -> it must still segment.
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "你好世界。", "languageCode": "zh"}}},
        [],
        0,
        object(),
    )
    assert _separator_in_updates(updates)


def test_same_language_decimal_does_not_segment_across_gemini_batches(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "en"
    session.loop = object()

    all_final_tokens = []
    sent_count, *_ = session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "Back during the CS 1.", "languageCode": "en"}}},
        all_final_tokens,
        0,
        object(),
    )
    sent_count, *_ = session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "5.", "languageCode": "en"}}},
        all_final_tokens,
        sent_count,
        object(),
    )
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": " Next sentence", "languageCode": "en"}}},
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = ["SEP" if t.get("is_separator") else t.get("text") for t in final_tokens]
    assert kinds == ["Back during the CS 1.", "5.", "SEP", " Next sentence"], kinds


def test_same_language_am_pm_does_not_segment_across_gemini_batches(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "en"
    session.loop = object()

    all_final_tokens = []
    sent_count, *_ = session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "Meet at 9 a.", "languageCode": "en"}}},
        all_final_tokens,
        0,
        object(),
    )
    sent_count, *_ = session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "m.", "languageCode": "en"}}},
        all_final_tokens,
        sent_count,
        object(),
    )
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": " tomorrow.", "languageCode": "en"}}},
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = ["SEP" if t.get("is_separator") else t.get("text") for t in final_tokens]
    assert kinds == ["Meet at 9 a.", "m.", " tomorrow.", "SEP"], kinds


def test_same_language_closing_quote_stays_with_previous_gemini_sentence(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "en"
    session.loop = object()

    all_final_tokens = []
    sent_count, *_ = session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "He said hello", "languageCode": "en"}}},
        all_final_tokens,
        0,
        object(),
    )
    sent_count, *_ = session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": ".", "languageCode": "en"}}},
        all_final_tokens,
        sent_count,
        object(),
    )
    sent_count, *_ = session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "\"", "languageCode": "en"}}},
        all_final_tokens,
        sent_count,
        object(),
    )
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": " Next sentence", "languageCode": "en"}}},
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = ["SEP" if t.get("is_separator") else t.get("text") for t in final_tokens]
    assert kinds == ["He said hello", ".", "\"", "SEP", " Next sentence"], kinds


def test_translation_ahead_of_source_keeps_full_source_together(monkeypatch):
    """Regression: Gemini often emits the sentence's final source tokens a beat
    after the translation. The line break must wait for the source to reach its
    own sentence end, otherwise the trailing source characters spill onto the
    next line (and the LLM gets a truncated source)."""
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.loop = object()

    def _refine_sources():
        return [u.get("source") for u in updates if u.get("type") == "refine_result"]

    # 1) Partial source + partial translation (no sentence end yet).
    session._process_stream_response(
        {"serverContent": {
            "inputTranscription": {"text": "これを作りまし", "languageCode": "ja"},
            "outputTranscription": {"text": "制作这个"},
        }},
        [], 0, object(),
    )
    assert not _separator_in_updates(updates)

    # 2) Translation reaches its sentence end BEFORE the source does. The cut must
    #    be deferred — no separator, no finalized sentence yet.
    session._process_stream_response(
        {"serverContent": {"outputTranscription": {"text": "吧。"}}},
        [], 0, object(),
    )
    assert not _separator_in_updates(updates), "cut fired before the source was complete"
    assert _refine_sources() == []

    # 3) The trailing source tokens arrive and complete the sentence -> now cut,
    #    and the finalized source must contain the WHOLE sentence.
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "ょう。", "languageCode": "ja"}}},
        [], 0, object(),
    )
    assert _separator_in_updates(updates)
    sources = _refine_sources()
    assert sources, "sentence was never finalized"
    assert sources[-1].endswith("。")
    assert "作りまし" in sources[-1] and sources[-1].endswith("ょう。"), sources[-1]


def test_cross_language_source_punctuation_does_not_segment(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.loop = object()

    # Source is English (!= target zh) so a real translation is still pending;
    # punctuation in the source must NOT trigger segmentation yet.
    session._process_stream_response(
        {"serverContent": {"inputTranscription": {"text": "hello world.", "languageCode": "en"}}},
        [],
        0,
        object(),
    )
    assert not _separator_in_updates(updates)


def test_output_transcription_applies_fullwidth_punct_fix(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"

    session._process_stream_response(
        {"serverContent": {"outputTranscription": {"text": "这是,翻译 。", "languageCode": "zh"}}},
        [],
        0,
        object(),
    )
    token = updates[-1]["final_tokens"][0]
    assert token["translation_status"] == "translation"
    assert token["text"] == "这是，翻译。"


def test_gemini_translation_mode_maps_onto_internal_llm_mode(monkeypatch):
    # Gemini mirrors soniox's 3-mode model: 混合→refine, 准确→translate, 快速→off.
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    session = module.GeminiSession(MagicMock(), MagicMock())

    ok, normalized, needs_restart = session.set_translation_mode("hybrid")
    assert (ok, normalized, needs_restart) == (True, "hybrid", False)
    assert session._llm_refine_mode == "refine"
    assert session.get_translation_mode() == "hybrid"

    session.set_translation_mode("accurate")
    assert session._llm_refine_mode == "translate"
    assert session.get_translation_mode() == "accurate"

    session.set_translation_mode("fast")
    assert session._llm_refine_mode == "off"
    assert session.get_translation_mode() == "fast"

    # Legacy 改进 value folds into 混合.
    ok, normalized, _ = session.set_translation_mode("refine")
    assert normalized == "hybrid"
    assert session._llm_refine_mode == "refine"

    assert session.set_translation_mode("nonsense")[0] is False


def test_gemini_hybrid_finalize_refines_draft_and_updates_osc(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session.set_translation_mode("hybrid")
    session.set_osc_translation_enabled(True)

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "改进后的译文。"}

    session._perform_refine = fake_refine

    original_tokens = [{"text": "Hello there.", "translation_status": "original",
                        "language": "en", "is_final": True, "speaker": "0"}]
    translation_tokens = [{"text": "你好。", "translation_status": "translation",
                           "language": "zh", "is_final": True, "speaker": "0"}]

    asyncio.run(session._finalize_sentence_async("0", original_tokens, translation_tokens, "sid-1"))

    assert calls == [("refine", "Hello there.", "你好。")]
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["refined_translation"] == "改进后的译文。"
    # 混合: the fast draft goes out first, then the refine replaces it in place.
    assert module.osc_manager.add_message_and_send.call_count == 1
    assert module.osc_manager.update_message_and_send.call_count == 1


def test_gemini_hybrid_finalize_keeps_draft_when_refine_no_change(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately_factory())
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.GeminiSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session.set_translation_mode("hybrid")
    session.set_osc_translation_enabled(True)

    async def fake_refine(source, translation, context_items):
        return {"status": "ok", "no_change": True}

    session._perform_refine = fake_refine

    original_tokens = [{"text": "Hello there.", "translation_status": "original",
                        "language": "en", "is_final": True, "speaker": "0"}]
    translation_tokens = [{"text": "你好。", "translation_status": "translation",
                           "language": "zh", "is_final": True, "speaker": "0"}]

    asyncio.run(session._finalize_sentence_async("0", original_tokens, translation_tokens, "sid-1"))

    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["no_change"] is True
    # Draft sent once; no update because the LLM left it unchanged.
    assert module.osc_manager.add_message_and_send.call_count == 1
    assert module.osc_manager.update_message_and_send.call_count == 0
