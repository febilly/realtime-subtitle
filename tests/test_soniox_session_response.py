import asyncio
import concurrent.futures
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _install_soniox_session_import_mocks(monkeypatch):
    monkeypatch.delitem(sys.modules, "soniox_session", raising=False)
    # Re-import the shared LLM helper fresh so it binds to this test's mocked
    # config / llm_client rather than a version cached by an earlier test.
    monkeypatch.delitem(sys.modules, "llm_refine", raising=False)
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
    config.relay_connect_info = lambda provider=None, model=None, translation=None: {
        "url": f"wss://relay.invalid/relay/{provider or 'soniox'}?ticket=test",
        "headers": {},
    }
    config.USE_TWITCH_AUDIO_STREAM = False
    config.MICROPHONE_DEVICE_ID = ""
    config.MUTE_MIC_WHEN_VRCHAT_SELF_MUTED = False
    config.TWITCH_CHANNEL = ""
    config.TWITCH_STREAM_QUALITY = "audio_only"
    config.FFMPEG_PATH = "ffmpeg"
    config.DEFAULT_SEGMENT_MODE = "punctuation"
    config.SONIOX_INTERRUPT_REPAIR_ENABLED = True
    config.SONIOX_INTERRUPT_MAX_DURATION_MS = 800
    config.SONIOX_INTERRUPT_RESUME_GAP_MS = 1500
    config.SONIOX_INTERRUPT_FILLER_WHITELIST_ENABLED = True
    config.SONIOX_INTERRUPT_FILLER_WHITELIST = "uh,um,嗯,啊,うん,はいはい,そうそうそう,어"
    config.is_llm_refine_available = lambda: False
    config.llm_is_hosted = lambda: False
    config.llm_context_bounds = lambda: (1, 1)
    config.llm_timeout_seconds = lambda: 60.0
    config.llm_max_output_tokens = lambda: 128
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
    config.describe_target_language = lambda lang: lang
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


def test_soniox_response_capture_includes_raw_batch_and_active_modes(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    capture = MagicMock()
    monkeypatch.setattr(module.soniox_response_log, "log_response", capture)
    session = module.SonioxSession(MagicMock(), MagicMock())
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    response = {
        "error_code": 500,
        "error_message": "diagnostic stop",
        "tokens": [{"text": "First. Second.", "is_final": True}],
    }
    accumulated = [{"text": "Earlier."}]

    session._process_soniox_response(response, accumulated, 7, object())

    capture.assert_called_once()
    args, context = capture.call_args
    assert args == (response,)
    assert context == {
        "segment_mode": "punctuation",
        "translation_mode": "accurate",
        "llm_refine_mode": "translate",
        "suppress_soniox_translation": True,
        "sent_count": 7,
        "all_final_token_count": 1,
        "stream_key": f"{id(accumulated):x}",
    }


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


def test_standalone_translation_punctuation_segments_in_punctuation_mode(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"

    all_final_tokens = []
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "わからない",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "ja",
                    "source_language": "ja",
                },
                {
                    "text": "我不知道",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "translation",
                    "language": "zh",
                    "source_language": "ja",
                },
            ]
        },
        all_final_tokens,
        0,
        object(),
    )
    session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "。",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "translation",
                    "language": "zh",
                    "source_language": "ja",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = ["SEP" if t.get("is_separator") else t.get("text") for t in final_tokens]
    assert kinds == ["わからない", "我不知道", "。", "SEP"], kinds


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


def test_punctuation_does_not_split_unspaced_decimal_point(monkeypatch):
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

    _feed_original_batch(session, ["version 3.", "10", " is out."], "en")

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["version 3.", "10", " is out.", "SEP"], kinds


def test_punctuation_does_not_split_decimal_point_across_batches(monkeypatch):
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

    all_final_tokens = []
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "Back during the CS 1.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        0,
        object(),
    )
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "5.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )
    session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": " But the game is actually about",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == [
        "Back during the CS 1.",
        "5.",
        "SEP",
        " But the game is actually about",
    ], kinds


def test_punctuation_flushes_pending_numeric_period_before_next_sentence(monkeypatch):
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

    all_final_tokens = []
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "It's 21.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        0,
        object(),
    )
    session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": " Next sentence",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["It's 21.", "SEP", " Next sentence"], kinds


def test_punctuation_does_not_split_am_pm_abbreviation_across_batches(monkeypatch):
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

    all_final_tokens = []
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "Meet at 9 a.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        0,
        object(),
    )
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "m.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )
    session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": " tomorrow.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["Meet at 9 a.", "m.", " tomorrow.", "SEP"], kinds


def test_punctuation_flushes_abbreviation_prefix_when_not_continued(monkeypatch):
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

    all_final_tokens = []
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "Choose option a.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        0,
        object(),
    )
    session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": " Next sentence",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["Choose option a.", "SEP", " Next sentence"], kinds


def test_punctuation_keeps_closing_quote_with_previous_sentence(monkeypatch):
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

    _feed_original_batch(session, ["He said hello.", "\"", " Next sentence"], "en")

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["He said hello.", "\"", "SEP", " Next sentence"], kinds


def test_punctuation_keeps_closing_quote_with_previous_sentence_across_batches(monkeypatch):
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

    all_final_tokens = []
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "He said hello",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                },
                {
                    "text": ".",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                },
            ]
        },
        all_final_tokens,
        0,
        object(),
    )
    sent_count, *_ = session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "\"",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )
    session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": " Next sentence",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                }
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["He said hello", ".", "\"", "SEP", " Next sentence"], kinds


def test_punctuation_quoted_sentence_in_one_token_splits_after_quote(monkeypatch):
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

    _feed_original_batch(session, ["He said hello.\"", " Next sentence"], "en")

    final_tokens = [t for u in updates for t in u.get("final_tokens", [])]
    kinds = [
        "SEP" if t.get("is_separator") else t.get("text")
        for t in final_tokens
    ]
    assert kinds == ["He said hello.\"", "SEP", " Next sentence"], kinds


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


def test_accurate_mode_cross_language_source_segments_in_punctuation_mode(monkeypatch):
    """Regression: in 准确 (accurate) mode soniox's built-in translation is off, so
    no translation token ever arrives. Finalization must key off the source, or a
    cross-language sentence (which normally waits for a translation) never
    triggers the LLM translate at all."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "en"  # source ja != target en
    session.loop = object()
    session._segment_mode = "punctuation"
    # Accurate mode: soniox translation suppressed, LLM translates from source.
    session._suppress_soniox_translation = True
    session._llm_refine_mode = "translate"

    _feed_original(session, "こんにちは。", "ja")
    assert _separator_in_updates(updates)


def test_accurate_mode_segments_period_on_fragmented_word_suffix(monkeypatch):
    """Raw-token regression from responses_20260713_011610: Soniox emitted
    here. as " her" + "e." before the next sentence."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._suppress_soniox_translation = True
    session._llm_refine_mode = "translate"

    session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": "And there's a couple of candidate ideas her",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                },
                {
                    "text": "e.",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                },
                {
                    "text": " So",
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": "en",
                },
            ]
        },
        [],
        0,
        object(),
    )

    final_tokens = [token for update in updates for token in update.get("final_tokens", [])]
    rendered = ["SEP" if token.get("is_separator") else token.get("text") for token in final_tokens]
    assert rendered == [
        "And there's a couple of candidate ideas her",
        "e.",
        "SEP",
        " So",
    ]


def test_accurate_mode_cross_language_source_segments_in_translation_mode(monkeypatch):
    """Same as above but in translation segment mode: the source-punctuation
    fallback must fire in accurate mode even though the source is cross-language."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "en"
    session.loop = object()
    session._segment_mode = "translation"
    session._suppress_soniox_translation = True
    session._llm_refine_mode = "translate"

    _feed_original(session, "こんにちは。", "ja")
    assert _separator_in_updates(updates)


def test_accurate_translation_mode_forces_punctuation_segmentation(monkeypatch):
    """Live 2026-07-12 regression (llm_20260712_080912): set_translation_mode
    assigns the internal LLM mode directly, bypassing set_llm_refine_mode's
    punctuation-forcing guard. 准确 left in "translation" segment mode closes
    the pairer once per BATCH, so a batch carrying four complete sentences
    dispatched them as one blob (and re-translated it despite per-sentence
    speculative results)."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    session = module.SonioxSession(MagicMock(), MagicMock())
    session._segment_mode = "translation"

    ok, normalized, needs_restart = session.set_translation_mode("accurate")
    assert ok and normalized == "accurate" and needs_restart
    assert session.get_segment_mode() == "punctuation"

    # Hybrid keeps the soniox translation stream, so a translation segment
    # mode remains legal and must be left alone.
    session._segment_mode = "translation"
    ok, normalized, needs_restart = session.set_translation_mode("hybrid")
    assert ok and normalized == "hybrid"
    assert session.get_segment_mode() == "translation"


def test_outgoing_final_tokens_carry_llm_sentence_id_in_punctuation_mode(monkeypatch):
    """Regression: in interleaved punctuation mode the outgoing token copies are
    minified before finalization runs, so the sentence id must be assigned when
    the buffer opens — otherwise the frontend can never match refine_result to
    its sentence (准确 mode has no translation text to fall back on)."""
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

    _feed_original(session, "hello world.", "en")

    final_tokens = [
        t for u in updates for t in u.get("final_tokens", [])
        if not t.get("is_separator")
    ]
    assert final_tokens, "expected outgoing final tokens"
    assert all(t.get("llm_sentence_id") for t in final_tokens), final_tokens


def _feed_non_final(session, text, language):
    return session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": text,
                    "is_final": False,
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


def _feed_non_final_translation(session, text, language):
    return session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": text,
                    "is_final": False,
                    "speaker": "1",
                    "translation_status": "translation",
                    "language": language,
                }
            ]
        },
        [],
        0,
        object(),
    )


def _feed_original_and_translation(session, source, translation, source_language="en", translation_language="zh"):
    return session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": source,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": source_language,
                    "source_language": source_language,
                },
                {
                    "text": translation,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "translation",
                    "language": translation_language,
                    "source_language": source_language,
                },
            ]
        },
        [],
        0,
        object(),
    )


def _feed_original_and_translation_pair(session, source, translation, source_language="en", translation_language="zh"):
    return session._process_soniox_response(
        {
            "tokens": [
                {
                    "text": source,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": source_language,
                    "source_language": source_language,
                },
                {
                    "text": translation,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "translation",
                    "language": translation_language,
                    "source_language": source_language,
                },
            ]
        },
        [],
        0,
        object(),
    )


def _settle_pairing(monkeypatch, module, session, seconds=2.0):
    """Advance the pairing clock past the quiet-close window and feed an
    empty batch so the pairer collects. Translations without sentence-ending
    punctuation close on quiescence (sentence_pairing.QUIET_CLOSE_SECONDS)
    rather than instantly — pairing correctness over immediacy."""
    import time as _time

    base = _time.monotonic()
    monkeypatch.setattr(module.time, "monotonic", lambda: base + seconds)
    session._process_soniox_response({"tokens": []}, [], 0, object())


def _feed_original_and_translation_pair_with_endpoint(session, source, translation, source_language="en", translation_language="zh"):
    return session._process_soniox_response(
        {
            "endpoint_detected": True,
            "tokens": [
                {
                    "text": source,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "original",
                    "language": source_language,
                    "source_language": source_language,
                },
                {
                    "text": translation,
                    "is_final": True,
                    "speaker": "1",
                    "translation_status": "translation",
                    "language": translation_language,
                    "source_language": source_language,
                },
            ],
        },
        [],
        0,
        object(),
    )


def test_hybrid_finalize_without_interim_translation_refines_stt_draft(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "Refined STT draft."}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate never runs in 混合 mode; it refines the STT draft")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    _feed_original_and_translation(session, "Hello there.", "你好。")

    # 混合 refines the built-in draft even when no interim translation was shown.
    assert calls == [("refine", "Hello there.", "你好。")]
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["original_translation"] == "你好。"
    assert refined[-1]["refined_translation"] == "Refined STT draft."


def test_hybrid_immediate_finalize_after_translation_candidate_refines_stt_draft(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "Refined short draft."}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate never runs in 混合 mode; it refines the STT draft")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    _feed_non_final_translation(session, "这个", "zh")
    _feed_original_and_translation(session, "This one.", "这个。")

    assert calls == [("refine", "This one.", "这个。")]
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["refined_translation"] == "Refined short draft."


def test_hybrid_finalize_with_interim_translation_refines_final_stt_translation(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "Refined STT translation."}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate should not run after interim translation")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    _feed_non_final_translation(session, "你好", "zh")
    _feed_original_and_translation(session, "Hello there.", "你好。")

    assert calls == [("refine", "Hello there.", "你好。")]
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["original_translation"] == "你好。"
    assert refined[-1]["refined_translation"] == "Refined STT translation."


def test_hybrid_final_translation_seen_before_sentence_end_uses_refine(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "Refined STT translation."}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate should not run after displayed interim translation")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    _feed_original_and_translation_pair(session, "Hello ", "你好", source_language="en")
    _feed_original_and_translation_pair(session, "there.", "。", source_language="en")

    assert calls == [("refine", "Hello there.", "你好。")]


def test_hybrid_endpoint_finalizes_short_sentence_with_refine(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "Refined endpoint draft"}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate never runs in 混合 mode; it refines the STT draft")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    _feed_original_and_translation_pair_with_endpoint(
        session,
        "In modern times we do much the same",
        "在现代，我们也做着很多类似的事",
        source_language="en",
    )
    # The translation has no ending punctuation, so the pairer waits for
    # quiescence before treating it as complete.
    _settle_pairing(monkeypatch, module, session)

    assert calls == [("refine", "In modern times we do much the same", "在现代，我们也做着很多类似的事")]
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["refined_translation"] == "Refined endpoint draft"
    assert _separator_in_updates(updates)


def test_hybrid_source_punctuation_short_sentence_uses_refine(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "Refined punctuation draft"}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate never runs in 混合 mode; it refines the STT draft")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    _feed_original_and_translation_pair(
        session,
        "Blood.",
        "血",
        source_language="en",
    )
    # "血" has no ending punctuation: quiet close applies before refine runs.
    _settle_pairing(monkeypatch, module, session)

    assert calls == [("refine", "Blood.", "血")]
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["refined_translation"] == "Refined punctuation draft"
    assert _separator_in_updates(updates)


def test_hybrid_finalize_with_interim_translation_keeps_stt_when_refine_no_change(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    async def fake_refine(source, translation, context_items):
        return {"status": "ok", "no_change": True}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate should not run after interim translation")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    _feed_non_final_translation(session, "你好", "zh")
    _feed_original_and_translation(session, "Hello there.", "你好。")

    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined[-1]["original_translation"] == "你好。"
    assert refined[-1]["no_change"] is True
    assert refined[-1]["refined_translation"] is None


def test_perform_refine_keeps_uncited_change_as_no_change(monkeypatch):
    """A changed answer without a recognized <error> category is discarded."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    session = module.SonioxSession(MagicMock(), MagicMock())

    async def fake_llm_chat(*args, **kwargs):
        return "<check>none</check>\n<answer>更自然的译文。</answer>"

    session._llm_chat = fake_llm_chat

    result = asyncio.run(session._perform_refine("Hello there.", "你好。", []))

    assert result == {"status": "ok", "no_change": True}


def test_perform_refine_accepts_cited_error_replacement(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    session = module.SonioxSession(MagicMock(), MagicMock())

    async def fake_llm_chat(*args, **kwargs):
        return (
            "<check>mistranslation: greeting reversed</check>\n"
            "<answer>更准确的译文。</answer>\n<error>mistranslation</error>"
        )

    session._llm_chat = fake_llm_chat

    result = asyncio.run(session._perform_refine("Hello there.", "你好。", []))

    assert result == {
        "status": "ok",
        "no_change": False,
        "refined_translation": "更准确的译文。",
        "error_category": "mistranslation",
    }


def test_accurate_mode_speculative_translation_fires_instantly_and_reuses_cache(monkeypatch):
    """准确 mode speculative translation: a sentence complete in the non-final
    text fires the LLM immediately (interim text rarely changes, so no upfront
    delay), broadcasts a pending marker + the result, and finalization reuses
    the cached result without a second (double-billed) LLM call."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._suppress_soniox_translation = True
    session._llm_refine_mode = "translate"

    llm_calls = []

    async def fake_llm_chat(*args, **kwargs):
        llm_calls.append(args)
        return "你好。"

    session._llm_chat = fake_llm_chat

    # A complete sentence in the non-final text fires on the very first tick.
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    _feed_non_final(session, "Hello there.", "en")
    pending = [u for u in updates if u.get("type") == "spec_translation_pending"]
    spec = [u for u in updates if u.get("type") == "spec_translation"]
    assert pending and pending[0]["source"] == "Hello there."
    assert spec and spec[0]["source"] == "Hello there."
    assert spec[0]["translation"] == "你好。"
    assert len(llm_calls) == 1

    # Re-seeing the same sentence must not re-fire (cache dedup).
    monkeypatch.setattr(module.time, "monotonic", lambda: 200.0)
    _feed_non_final(session, "Hello there.", "en")
    assert len(llm_calls) == 1

    # The tokens finalize with the same text -> finalization must reuse the
    # cached speculative result instead of issuing a second LLM call.
    _feed_original(session, "Hello there.", "en")
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert refined and refined[-1]["refined_translation"] == "你好。"
    assert len(llm_calls) == 1


def test_accurate_mode_finalize_broadcasts_pending_placeholder(monkeypatch):
    """准确 mode: a finalize-path LLM call (no speculative cache hit) must
    broadcast a pending marker BEFORE the call so the UI shows the ZH
    placeholder immediately, not only for speculative (non-final) runs."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._suppress_soniox_translation = True
    session._llm_refine_mode = "translate"

    async def fake_llm_chat(*args, **kwargs):
        return "你好。"

    session._llm_chat = fake_llm_chat

    # Final tokens arrive directly (no speculative run happened).
    _feed_original(session, "Hello there.", "en")

    types = [u.get("type") for u in updates]
    assert "spec_translation_pending" in types
    assert "refine_result" in types
    assert types.index("spec_translation_pending") < types.index("refine_result")
    pending = [u for u in updates if u.get("type") == "spec_translation_pending"]
    assert pending[0]["source"] == "Hello there."
    assert pending[0]["target_lang"] == "zh"


def test_accurate_mode_speculative_cooldown_limits_flip_flop_requests(monkeypatch):
    """The cooldown sits AFTER a fire: a revised reading appearing within the
    cooldown must wait for it to expire (and still be present) before firing,
    so flip-flopping interim results cannot spam LLM requests."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._suppress_soniox_translation = True
    session._llm_refine_mode = "translate"

    llm_calls = []

    async def fake_llm_chat(*args, **kwargs):
        llm_calls.append(args)
        return "你好。"

    session._llm_chat = fake_llm_chat

    # First reading fires instantly and starts the cooldown.
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    _feed_non_final(session, "Hello there.", "en")
    assert len(llm_calls) == 1

    # Revised readings within the cooldown do NOT fire.
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.2)
    _feed_non_final(session, "Hello dear.", "en")
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.4)
    _feed_non_final(session, "Hello deer.", "en")
    assert len(llm_calls) == 1

    # After the cooldown expires, the reading still present fires.
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0 + module.SPEC_TRANSLATE_COOLDOWN_SECONDS + 0.1)
    _feed_non_final(session, "Hello deer.", "en")
    assert len(llm_calls) == 2


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
    assert split("等等…好") == ["等等…好"]      # ellipsis trails off, no split
    assert split("A。 B。 C") == ["A。", "B。", "C"]   # trailing partial kept, spaces trimmed
    assert split("no punct tail") == ["no punct tail"]
    assert split("版本 3.10 已发布。下一句") == ["版本 3.10 已发布。", "下一句"]
    assert split("Meet at 9 a.m. tomorrow.") == ["Meet at 9 a.m. tomorrow."]
    assert split("He said hello.\" Next") == ["He said hello.\"", "Next"]
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


def test_relay_stream_marks_translation_none_when_translation_disabled(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    relay_calls = []
    sent_payloads = []
    config_translations = []

    class FakeWs:
        def send(self, payload):
            sent_payloads.append(payload)

        def close(self):
            return None

    def relay_connect_info(provider=None, model=None, translation=None):
        relay_calls.append({
            "provider": provider,
            "model": model,
            "translation": translation,
        })
        return {
            "url": "wss://relay.invalid/relay/soniox?ticket=test",
            "headers": {"X-Test-Relay": "ok"},
        }

    def get_config(api_key, audio_format, translation, **kwargs):
        config_translations.append(translation)
        return {
            "api_key": api_key,
            "model": "stt-rt-v5",
        }

    monkeypatch.setattr(module, "sync_connect", MagicMock(return_value=FakeWs()))
    monkeypatch.setattr(module, "get_config", get_config)
    module.config.RELAY_MODE = True
    module.config.RELAY_TOKEN = "ss_test"
    module.config.relay_connect_info = relay_connect_info

    session = module.SonioxSession(MagicMock(), MagicMock())

    stream = session._open_soniox_stream_state(
        "relay-placeholder",
        1,
        "pcm_s16le",
        "none",
        "en",
    )

    assert config_translations == ["none"]
    assert relay_calls[-1]["translation"] == "none"
    module.sync_connect.assert_called_once_with(
        "wss://relay.invalid/relay/soniox?ticket=test",
        additional_headers={"X-Test-Relay": "ok"},
    )
    assert sent_payloads
    assert '"api_key": ""' in sent_payloads[-1]
    stream.ws.close()


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


def test_punctuation_speaker_change_finalizes_previous_speaker(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append(("refine", source, translation))
        return {"status": "ok", "no_change": False, "refined_translation": "Refined"}

    async def fake_translate(*args, **kwargs):
        raise AssertionError("direct translate should not run when speaker change closes displayed translation")

    session._perform_refine = fake_refine
    session._perform_translate = fake_translate

    all_final_tokens = []

    # Speaker 1 speaks with no sentence-ending punctuation and no endpoint yet:
    # the sentence stays open (nothing to finalize).
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    sent_count, _, _ = session._process_soniox_response(
        {
            "tokens": [
                {"text": "Hello there", "is_final": True, "speaker": "1",
                 "translation_status": "original", "language": "en", "source_language": "en"},
                {"text": "你好", "is_final": True, "speaker": "1",
                 "translation_status": "translation", "language": "zh", "source_language": "en"},
            ]
        },
        all_final_tokens,
        0,
        object(),
    )
    assert calls == []

    # Speaker 2 starts: speaker 1's open sentence must finalize (LLM refine)
    # instead of lingering as a provisional line. "你好" has no ending
    # punctuation, so the pairer needs the quiet-close window to have
    # elapsed before it treats the translation as complete.
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0 + 1.0)
    session._process_soniox_response(
        {
            "tokens": [
                {"text": "Goodbye", "is_final": True, "speaker": "2",
                 "translation_status": "original", "language": "en", "source_language": "en"},
                {"text": "再见", "is_final": True, "speaker": "2",
                 "translation_status": "translation", "language": "zh", "source_language": "en"},
            ]
        },
        all_final_tokens,
        sent_count,
        object(),
    )

    assert ("refine", "Hello there", "你好") in calls


def _timed_token(text, speaker, start_ms, end_ms, status="original", language="en", source_language="en"):
    token = {
        "text": text,
        "is_final": True,
        "speaker": str(speaker),
        "translation_status": status,
        "language": language,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }
    if source_language is not None:
        token["source_language"] = source_language
    return token


def _feed_timed_tokens(session, all_final_tokens, sent_count, tokens):
    return session._process_soniox_response(
        {"tokens": tokens},
        all_final_tokens,
        sent_count,
        object(),
    )[0]


async def _fake_translate_ok(*args, **kwargs):
    return {"status": "ok", "translation": "zh"}


def test_short_interrupt_retracts_first_fragment_and_llm_uses_merged_source(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True

    calls = []

    async def fake_translate(source, context_items, target_lang=None):
        calls.append(source)
        return {"status": "ok", "translation": "merged zh"}

    session._perform_translate = fake_translate

    all_final_tokens = []
    sent_count = _feed_timed_tokens(
        session,
        all_final_tokens,
        0,
        [
            _timed_token("I was saying", "1", 0, 900),
            _timed_token(".", "1", 900, 1000),
        ],
    )
    sent_count = _feed_timed_tokens(
        session,
        all_final_tokens,
        sent_count,
        [_timed_token("uh", "2", 950, 1550)],
    )
    _feed_timed_tokens(
        session,
        all_final_tokens,
        sent_count,
        [_timed_token(" that this works.", "1", 1800, 2600)],
    )

    retracts = [u for u in updates if u.get("type") == "subtitle_retract"]
    assert retracts, updates
    assert calls[-1] == "I was saying that this works."
    assert "uh that this works." not in calls

    final_texts = [
        t.get("text")
        for u in updates
        for t in u.get("final_tokens", [])
        if not t.get("is_separator")
    ]
    assert "uh" in final_texts
    assert "I was saying" in final_texts


def test_short_interrupt_whitelist_ignores_case_and_punctuation(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    session._perform_translate = _fake_translate_ok

    all_final_tokens = []
    sent_count = _feed_timed_tokens(session, all_final_tokens, 0, [_timed_token("A one.", "1", 0, 1000)])
    sent_count = _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("UH?!", "2", 1050, 1250)])
    _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token(" A two.", "1", 1300, 2000)])

    assert [u for u in updates if u.get("type") == "subtitle_retract"]


def test_short_interrupt_japanese_backchannel_whitelist_matches(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    session._perform_translate = _fake_translate_ok

    all_final_tokens = []
    sent_count = _feed_timed_tokens(session, all_final_tokens, 0, [_timed_token("A one.", "1", 0, 1000)])
    sent_count = _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("そうそうそう。", "2", 1050, 1250, language="ja")])
    _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token(" A two.", "1", 1300, 2000)])

    assert [u for u in updates if u.get("type") == "subtitle_retract"]


def test_short_interrupt_non_whitelisted_text_does_not_merge(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    session._perform_translate = _fake_translate_ok

    all_final_tokens = []
    sent_count = _feed_timed_tokens(session, all_final_tokens, 0, [_timed_token("A one.", "1", 0, 1000)])
    sent_count = _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("sure", "2", 1050, 1250)])
    _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token(" A two.", "1", 1300, 2000)])

    assert not [u for u in updates if u.get("type") == "subtitle_retract"]


def test_short_interrupt_whitelist_can_be_disabled(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    module.config.SONIOX_INTERRUPT_FILLER_WHITELIST_ENABLED = False
    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    session._perform_translate = _fake_translate_ok

    all_final_tokens = []
    sent_count = _feed_timed_tokens(session, all_final_tokens, 0, [_timed_token("A one.", "1", 0, 1000)])
    sent_count = _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("sure", "2", 1050, 1250)])
    _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token(" A two.", "1", 1300, 2000)])

    assert [u for u in updates if u.get("type") == "subtitle_retract"]


def test_short_interrupt_801ms_does_not_merge(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    session._perform_translate = _fake_translate_ok

    all_final_tokens = []
    sent_count = _feed_timed_tokens(session, all_final_tokens, 0, [_timed_token("A one.", "1", 0, 1000)])
    sent_count = _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("long uh", "2", 1050, 1851)])
    _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("A two.", "1", 1900, 2600)])

    assert not [u for u in updates if u.get("type") == "subtitle_retract"]


def test_short_interrupt_missing_timestamps_does_not_merge(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    session._perform_translate = _fake_translate_ok

    all_final_tokens = []
    sent_count = _feed_timed_tokens(session, all_final_tokens, 0, [_timed_token("A one.", "1", 0, 1000)])
    interrupt = _timed_token("uh", "2", 1050, 1650)
    interrupt.pop("start_ms")
    sent_count = _feed_timed_tokens(session, all_final_tokens, sent_count, [interrupt])
    _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("A two.", "1", 1800, 2600)])

    assert not [u for u in updates if u.get("type") == "subtitle_retract"]


def test_short_interrupt_resume_gap_too_long_does_not_merge(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True
    session._perform_translate = _fake_translate_ok

    all_final_tokens = []
    sent_count = _feed_timed_tokens(session, all_final_tokens, 0, [_timed_token("A one.", "1", 0, 1000)])
    sent_count = _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("uh", "2", 1050, 1650)])
    _feed_timed_tokens(session, all_final_tokens, sent_count, [_timed_token("A two.", "1", 3200, 4000)])

    assert not [u for u in updates if u.get("type") == "subtitle_retract"]
