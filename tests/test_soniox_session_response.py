import asyncio
import concurrent.futures
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _install_soniox_session_import_mocks(monkeypatch):
    monkeypatch.delitem(sys.modules, "soniox_session", raising=False)
    config = ModuleType("config")
    config.SONIOX_WEBSOCKET_URL = "wss://example.invalid"
    config.SONIOX_STREAM_DURATION_SECONDS = None
    config.SONIOX_SLEEP_ON_SILENCE = False
    config.SLEEP_IDLE_SECONDS = 30.0
    config.SLEEP_PRE_ROLL_SECONDS = 1.0
    config.SLEEP_SPEECH_GRACE_SECONDS = 0.5
    config.SLEEP_SPEECH_WINDOW_SECONDS = 0.75
    config.SLEEP_VAD_THRESHOLD = 0.2
    config.ROLLOVER_VAD_THRESHOLD = 0.6
    config.SONIOX_USES_TEMP_API_KEY = False
    config.SONIOX_TEMP_KEY_URL = None
    config.RELAY_MODE = False
    config.RELAY_TOKEN = ""
    config.relay_ws_url = lambda provider=None, model=None: f"wss://relay.invalid/relay/{provider or 'soniox'}"
    config.USE_TWITCH_AUDIO_STREAM = False
    config.MICROPHONE_DEVICE_ID = ""
    config.MUTE_MIC_WHEN_VRCHAT_SELF_MUTED = False
    config.TWITCH_CHANNEL = ""
    config.TWITCH_STREAM_QUALITY = "audio_only"
    config.FFMPEG_PATH = "ffmpeg"
    config.DEFAULT_SEGMENT_MODE = "punctuation"
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
    config.normalize_language_code = lambda value: (value or "").strip().lower()
    config.OSC_SEND_TEXT_MODE = "smart"
    config.LLM_REFINE_DEFAULT_ENABLED = False
    config.LLM_REFINE_DEFAULT_MODE = "off"
    config.TARGET_LANG_1 = "en"
    config.TARGET_LANG_2 = "zh"
    monkeypatch.setitem(sys.modules, "config", config)

    soniox_client = ModuleType("soniox_client")
    soniox_client.get_config = lambda *args, **kwargs: {"api_key": args[0] if args else ""}
    monkeypatch.setitem(sys.modules, "soniox_client", soniox_client)

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


def test_soniox_response_uses_per_stream_sent_count(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    def run_immediately(coro, _loop):
        asyncio.run(coro)
        future = concurrent.futures.Future()
        future.set_result(None)
        return future

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", run_immediately)

    logger = MagicMock()
    session = module.SonioxSession(logger, broadcast)
    session.last_sent_count = 99

    first_count, should_end, reason = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "first",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                }
            ]
        },
        [],
        0,
        object(),
    )
    assert first_count == 1
    assert should_end is False
    assert reason is None
    assert updates[-1]["final_tokens"][0]["text"] == "first"

    second_count, should_end, reason = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "second",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                }
            ]
        },
        [],
        0,
        object(),
    )
    assert second_count == 1
    assert should_end is False
    assert reason is None
    assert updates[-1]["final_tokens"][0]["text"] == "second"
    assert session.last_sent_count == 1


def _run_immediately(coro, _loop):
    asyncio.run(coro)
    future = concurrent.futures.Future()
    future.set_result(None)
    return future


def _separator_in_updates(updates):
    return any(
        token.get("is_separator")
        for update in updates
        for token in update.get("final_tokens", [])
    )


def _feed_original(session, text, language):
    return session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": text,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": language,
                }
            ]
        },
        [],
        0,
        object(),
    )


def test_same_language_source_punctuation_segments_in_punctuation_mode(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.loop = object()
    session._segment_mode = "punctuation"

    # Speech already in the target language (en): no translation token, but the
    # source ends with sentence punctuation -> it must still segment.
    _feed_original(session, "hello world.", "en")
    assert _separator_in_updates(updates)


def _feed_original_batch(session, texts, language):
    return session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": text,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": language,
                }
                for text in texts
            ]
        },
        [],
        0,
        object(),
    )


def test_punctuation_separator_lands_at_period_within_batch(monkeypatch):
    """Regression: when Soniox confirms the ending punctuation together with the
    words that follow it in a single batch, the line break must be inserted right
    after the period, not at the end of the batch."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.loop = object()
    session._segment_mode = "punctuation"

    # The period and the next sentence's first words are all confirmed at once.
    _feed_original_batch(session, ["foo.", "and", "you"], "en")

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    # Expected order: "foo.", <separator>, "and", "you"
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["foo.", "SEP", "and", "you"], kinds


def _feed_tokens(session, tokens):
    return session._process_soniox_response(
        {
            "tokens": [
                {"is_final": True, "speaker": "1", **token}
                for token in tokens
            ]
        },
        [],
        0,
        object(),
    )


def test_endpoint_split_keeps_late_translation_with_its_own_sentence(monkeypatch):
    """Regression: when an utterance is split by <end> (a pause) rather than a
    sentence-ending period, the cross-language translation streams in *after*
    <end>. The line break must still land before the next sentence so the next
    sentence's translation does not run onto the previous translation."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "en"  # source ja != target en -> needs translation
    session.loop = object()
    session._segment_mode = "punctuation"

    # Batch 1: sentence 1 original (ja) finalized, then <end>. Translation for
    # sentence 1 has not arrived yet, and the utterance has no ending period.
    _feed_tokens(session, [
        {"text": "こんにちは", "translation_status": "original", "language": "ja"},
        {"text": "<end>", "translation_status": "original"},
    ])

    # Batch 2: sentence 1's translation (en, no ending punctuation) arrives,
    # immediately followed by sentence 2's first original token.
    _feed_tokens(session, [
        {"text": "Hello", "translation_status": "translation",
         "language": "en", "source_language": "ja"},
        {"text": "またね", "translation_status": "original", "language": "ja"},
    ])

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    # The separator must split sentence 1 (こんにちは + Hello) from sentence 2
    # (またね), not leave them merged.
    assert kinds == ["こんにちは", "Hello", "SEP", "またね"], kinds


def test_cross_language_source_punctuation_does_not_segment_in_punctuation_mode(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.loop = object()
    session._segment_mode = "punctuation"

    # Source is Japanese (!= target en); translation pending, so source
    # punctuation must not segment yet.
    _feed_original(session, "こんにちは。", "ja")
    assert not _separator_in_updates(updates)


def test_same_language_source_punctuation_segments_in_translation_mode(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.loop = object()
    session._segment_mode = "translation"

    # Translation mode has no translation token for same-language speech; it
    # should fall back to source punctuation instead of growing unbounded.
    _feed_original(session, "hello world.", "en")
    assert _separator_in_updates(updates)


def test_split_into_sentence_lines(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    session = module.SonioxSession(MagicMock(), lambda *_: None)
    split = session._split_into_sentence_lines

    assert split("看得人是脊背发凉。事发江西") == ["看得人是脊背发凉。", "事发江西"]
    assert split("甲。乙丙！丁") == ["甲。", "乙丙！", "丁"]
    assert split("在睡觉。") == ["在睡觉。"]          # complete sentence, no trailing partial
    assert split("等等…好") == ["等等…", "好"]
    assert split("A。 B。 C") == ["A。", "B。", "C"]   # trailing partial kept, spaces trimmed
    assert split("no punct tail") == ["no punct tail"]
    assert split("") == []


def test_stream_key_refresh_skips_temp_key_fetch_in_relay_mode(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    get_api_key = MagicMock(side_effect=AssertionError("should not fetch temp key"))
    sys.modules["soniox_client"].get_api_key = get_api_key
    module.config.RELAY_MODE = True
    module.config.SONIOX_USES_TEMP_API_KEY = True
    module.config.SONIOX_TEMP_KEY_URL = None
    session = module.SonioxSession(MagicMock(), MagicMock())

    assert session._fetch_api_key_for_next_stream("relay-placeholder") == "relay-placeholder"
    get_api_key.assert_not_called()


def test_stream_key_refresh_skips_missing_temp_key_url(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    get_api_key = MagicMock(side_effect=AssertionError("should not fetch temp key"))
    sys.modules["soniox_client"].get_api_key = get_api_key
    module.config.RELAY_MODE = False
    module.config.SONIOX_USES_TEMP_API_KEY = True
    module.config.SONIOX_TEMP_KEY_URL = None
    session = module.SonioxSession(MagicMock(), MagicMock())

    assert session._fetch_api_key_for_next_stream("current-key") == "current-key"
    get_api_key.assert_not_called()


def test_stream_rollover_prepare_age_uses_fixed_patience_window(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    async def broadcast(_data):
        return None

    session = module.SonioxSession(MagicMock(), broadcast)

    assert session._stream_rollover_switch_patience(170.0) == 25.0
    assert session._stream_rollover_prepare_age(170.0) == 143.0
    assert session._stream_rollover_switch_patience(30.0) == 15.0
    assert session._stream_rollover_prepare_age(30.0) == 13.0
    assert session._stream_rollover_switch_patience(10.0) == 5.0
    assert session._stream_rollover_prepare_age(10.0) == 4.0


def test_stream_rollover_disabled_in_relay_mode(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    module.config.RELAY_MODE = True
    module.config.SONIOX_USES_TEMP_API_KEY = True
    monkeypatch.setattr(module, "SONIOX_STREAM_DURATION_SECONDS", 30.0)

    async def broadcast(_data):
        return None

    session = module.SonioxSession(MagicMock(), broadcast)

    assert session._stream_rollover_seconds() is None


def test_stream_rollover_only_uses_direct_temp_soniox_keys(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    module.config.RELAY_MODE = False
    monkeypatch.setattr(module, "SONIOX_STREAM_DURATION_SECONDS", 30.0)

    async def broadcast(_data):
        return None

    session = module.SonioxSession(MagicMock(), broadcast)

    module.config.SONIOX_USES_TEMP_API_KEY = False
    assert session._stream_rollover_seconds() is None

    module.config.SONIOX_USES_TEMP_API_KEY = True
    assert session._stream_rollover_seconds() == 30.0


def test_manual_finalize_fin_token_is_consumed_but_not_displayed(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    def run_immediately(coro, _loop):
        asyncio.run(coro)
        future = concurrent.futures.Future()
        future.set_result(None)
        return future

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", run_immediately)

    logger = MagicMock()
    session = module.SonioxSession(logger, broadcast)

    sent_count, should_end, reason = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "hello",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                },
                {
                    "text": "<fin>",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                },
            ]
        },
        [],
        0,
        object(),
    )

    assert sent_count == 2
    assert should_end is False
    assert reason is None
    assert [token["text"] for token in updates[-1]["final_tokens"]] == ["hello"]
    logger.write_to_log.assert_called_once()
    logged_tokens = logger.write_to_log.call_args.args[0]
    assert [token["text"] for token in logged_tokens] == ["hello"]


def test_rollover_warmup_does_not_finalize_frontend_non_final(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    module.config.SONIOX_USES_TEMP_API_KEY = True
    monkeypatch.setattr(module, "SONIOX_STREAM_DURATION_SECONDS", 30.0)

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

    session = module.SonioxSession(MagicMock(), broadcast)
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
        return module._SonioxStreamState(
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

    session._open_soniox_stream_state = MagicMock(side_effect=open_stream)
    session._drain_warmup_stream = MagicMock(side_effect=drain_warmup)

    session._run_session("key", "pcm_s16le", "two_way", "zh", object())

    assert opened_warming_flags == [False, True]
    session._broadcast_preserve_existing_subtitles.assert_not_called()
