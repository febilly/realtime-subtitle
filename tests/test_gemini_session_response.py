import asyncio
import concurrent.futures
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _install_gemini_session_import_mocks(monkeypatch):
    monkeypatch.delitem(sys.modules, "gemini_session", raising=False)
    config = ModuleType("config")
    config.GEMINI_STREAM_DURATION_SECONDS = None
    config.GEMINI_SLEEP_ON_SILENCE = False
    config.GEMINI_SLEEP_IDLE_SECONDS = 30.0
    config.GEMINI_SLEEP_PRE_ROLL_SECONDS = 0.5
    config.GEMINI_SLEEP_SPEECH_GRACE_SECONDS = 0.25
    config.USE_TWITCH_AUDIO_STREAM = False
    config.MUTE_MIC_WHEN_VRCHAT_SELF_MUTED = False
    config.TWITCH_CHANNEL = ""
    config.TWITCH_STREAM_QUALITY = "audio_only"
    config.FFMPEG_PATH = "ffmpeg"
    config.is_llm_refine_available = lambda: False
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


def test_fix_fullwidth_punctuation_between_cjk(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    fix = module.fix_fullwidth_punctuation_between_cjk

    # Half-width punctuation wedged between two CJK characters becomes full-width.
    assert fix("你好,世界。") == "你好，世界。"
    assert fix("好?真的!对") == "好？真的！对"
    # Consecutive occurrences each convert (look-around does not consume neighbors).
    assert fix("中,中,中") == "中，中，中"
    # At least one non-CJK neighbor leaves the punctuation untouched.
    assert fix("价格3.14元") == "价格3.14元"
    assert fix("他说hello,world") == "他说hello,world"
    assert fix("A,B") == "A,B"
    assert fix("http://a.b/c 中") == "http://a.b/c 中"
    # A full-width neighbor is not a CJK ideograph, so nothing changes.
    assert fix("中,。") == "中,。"
    # No matching pattern / empty input.
    assert fix("没标点中文") == "没标点中文"
    assert fix("") == ""
    assert fix(None) == ""


def test_fix_fullwidth_punctuation_disabled_is_noop(monkeypatch):
    _install_gemini_session_import_mocks(monkeypatch)
    import gemini_session as module

    monkeypatch.setattr(module, "GEMINI_FULLWIDTH_PUNCT_FIX", False)
    assert module.fix_fullwidth_punctuation_between_cjk("你好,世界") == "你好,世界"


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
