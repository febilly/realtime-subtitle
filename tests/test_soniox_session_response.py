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
    config.USE_TWITCH_AUDIO_STREAM = False
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


def test_stream_rollover_prepare_age_uses_fixed_patience_window(monkeypatch):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    async def broadcast(_data):
        return None

    session = module.SonioxSession(MagicMock(), broadcast)

    assert session._stream_rollover_switch_patience(170.0) == 25.0
    assert session._stream_rollover_prepare_age(170.0) == 148.0
    assert session._stream_rollover_switch_patience(30.0) == 15.0
    assert session._stream_rollover_prepare_age(30.0) == 13.0
    assert session._stream_rollover_switch_patience(10.0) == 5.0
    assert session._stream_rollover_prepare_age(10.0) == 4.0


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
