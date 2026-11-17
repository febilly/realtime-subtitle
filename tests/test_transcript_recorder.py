import asyncio
import json
import os
import sys
import time
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module_stubs import monkeypatch_module, patch_modules, pop_module, set_module
import realtime_subtitle.core.config  # noqa: F401
import realtime_subtitle.core.local_store  # noqa: F401
from realtime_subtitle.core.transcript_recorder import (
    TranscriptEntry,
    TranscriptRecorder,
    format_srt,
    format_txt,
)


def _async_run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestTranscriptFormattingAndRecorder:
    def test_srt_formatting_monotonic_non_overlapping_and_omits_empty_translation(self):
        entries = [
            TranscriptEntry(
                sentence_id="s1",
                start_ms=1250,
                end_ms=3500,
                original="Hello world.",
                source_language="en",
                translation="你好，世界。",
            ),
            # Overlaps s1 (starts at 3000 < 3500) and end <= start -> clamped & +100ms
            TranscriptEntry(
                sentence_id="s2",
                start_ms=3000,
                end_ms=3200,
                original="Only original line.",
                source_language="en",
                translation="",
            ),
        ]
        rendered = format_srt(entries)
        assert rendered == (
            "1\n"
            "00:00:01,250 --> 00:00:03,500\n"
            "Hello world.\n"
            "你好，世界。\n\n"
            "2\n"
            "00:00:03,500 --> 00:00:03,600\n"
            "Only original line.\n\n"
        )

    def test_txt_formatting_uses_bracketed_timestamps_and_omits_empty_translation(self):
        entries = [
            TranscriptEntry(
                sentence_id="s1",
                start_ms=65_400,
                end_ms=68_000,
                original="First sentence.",
                translation="第一句。",
            ),
            TranscriptEntry(
                sentence_id="s2",
                start_ms=3_661_000,
                end_ms=3_663_000,
                original="Second sentence.",
                translation="",
            ),
        ]
        rendered = format_txt(entries)
        assert rendered == (
            "[00:01:05] First sentence.\n"
            "            第一句。\n\n"
            "[01:01:01] Second sentence.\n\n"
        )

    def test_export_and_autosave_write_utf8_bom_and_crlf(self, tmp_path):
        stored = {
            "featurePacks.v1": json.dumps({
                "version": 1,
                "confirmed": True,
                "enabled": {"transcript": True},
            }),
            "transcriptSettings.v1": json.dumps({
                "autoSave": True,
                "format": "srt",
                "directory": str(tmp_path),
            }),
        }
        status_events = []
        recorder = TranscriptRecorder(stored_settings=stored, on_status_changed=status_events.append)

        recorder.begin_run()
        assert recorder.record_sentence("s1", 100, 1200, "Hello.", "en", "") is True
        assert recorder.update_translation("s1", "你好。") is True
        assert recorder.record_sentence("s2", 1500, 2500, "Bye.", "en", "再见。") is True
        assert recorder.retract_sentence("s2") is True
        recorder.end_run()

        # Entries remain exportable after stop/end_run
        assert recorder.has_entries() is True
        assert recorder.entry_count() == 1

        files = list(tmp_path.glob("transcript-*.srt"))
        assert len(files) == 1
        raw_bytes = files[0].read_bytes()
        assert raw_bytes.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw_bytes
        decoded = raw_bytes.decode("utf-8-sig")
        assert "Hello.\r\n你好。" in decoded
        assert "Bye." not in decoded

        # Manual export to TXT uses extension-inferred format and UTF-8 BOM + CRLF
        manual_txt = tmp_path / "manual_export.txt"
        result = recorder.export_to_file(manual_txt)
        assert result["format"] == "txt"
        assert result["count"] == 1
        txt_bytes = manual_txt.read_bytes()
        assert txt_bytes.startswith(b"\xef\xbb\xbf")
        assert "[00:00:00] Hello.\r\n            你好。\r\n\r\n" == txt_bytes.decode("utf-8-sig")

    def test_run_lifecycle_continue_preserves_buffer_and_begin_clears_and_dedups_filename(self, tmp_path):
        stored = {
            "featurePacks.v1": json.dumps({"enabled": {"transcript": True}}),
            "transcriptSettings.v1": json.dumps({
                "autoSave": True,
                "format": "srt",
                "directory": str(tmp_path),
            }),
        }
        recorder = TranscriptRecorder(stored_settings=stored)
        recorder.begin_run()
        recorder.record_sentence("s1", 0, 1000, "First.", "en", "第一。")
        first_file = recorder._auto_save_path
        assert first_file is not None and first_file.exists()

        # continue_run preserves entries and reuses the same auto-save file
        recorder.end_run()
        recorder.continue_run()
        recorder.record_sentence("s2", 1200, 2200, "Second.", "en", "第二。")
        assert recorder.entry_count() == 2
        assert recorder._auto_save_path == first_file

        # Next begin_run clears previous entries and does not overwrite colliding file
        recorder.begin_run()
        assert recorder.entry_count() == 0
        # Force same wall timestamp to test collision suffixing (-2)
        recorder._run_started_wall = recorder._run_started_wall.replace(
            year=first_file.stat() and recorder._run_started_wall.year
        )
        # Reconstruct wall time from first_file stem ("transcript-YYYYMMDD-HHMMSS")
        from datetime import datetime
        stem_ts = first_file.stem.replace("transcript-", "")
        recorder._run_started_wall = datetime.strptime(stem_ts, "%Y%m%d-%H%M%S")
        recorder.record_sentence("s3", 0, 500, "Fresh run.", "en", "新开始。")
        assert recorder._auto_save_path != first_file
        assert recorder._auto_save_path.name.endswith("-2.srt")
        assert recorder._auto_save_path.exists()

    def test_pack_gating_ignores_recording_when_disabled(self, tmp_path):
        recorder = TranscriptRecorder(stored_settings={})
        assert recorder.is_pack_enabled() is False
        recorder.begin_run()
        assert recorder.record_sentence("s1", 0, 1000, "Ignored.", "en", "忽略。") is False
        assert recorder.has_entries() is False

        recorder.set_pack_enabled(True)
        assert recorder.record_sentence("s1", 0, 1000, "Recorded.", "en", "已记录。") is True
        assert recorder.has_entries() is True


class TestSessionTranscriptHooks:
    def test_soniox_records_original_when_translation_empty_updates_refine_and_retracts(self, monkeypatch, tmp_path):
        from tests.test_soniox_session_response import _install_soniox_session_import_mocks

        _install_soniox_session_import_mocks(monkeypatch)
        monkeypatch.setattr("realtime_subtitle.core.local_store.load", lambda: {})
        import realtime_subtitle.sessions.soniox_session as soniox_mod

        stored = {
            "featurePacks.v1": json.dumps({"enabled": {"transcript": True}}),
            "transcriptSettings.v1": json.dumps({"autoSave": False, "format": "srt", "directory": str(tmp_path)}),
        }
        recorder = TranscriptRecorder(stored_settings=stored)
        recorder.begin_run()

        session = soniox_mod.SonioxSession(MagicMock(), AsyncMock())
        session.set_transcript_recorder(recorder)
        session.translation = "none"
        session._llm_refine_mode = "off"

        # Simulate tokens with stream rollover offset
        all_tokens = []
        session._stream_offsets[id(all_tokens)] = 5000
        seen_at = time.monotonic()
        orig_tokens = [
            {"text": "Hello", "translation_status": "original", "language": "en", "_seen_at": seen_at, "_abs_start_ms": 5100, "_abs_end_ms": 5600},
            {"text": " world.", "translation_status": "original", "language": "en", "_seen_at": seen_at, "_abs_start_ms": 5600, "_abs_end_ms": 6200},
        ]

        _async_run(session._finalize_sentence_async("1", orig_tokens, [], sentence_id="soniox-1"))
        entries = recorder.get_entries()
        assert len(entries) == 1
        assert entries[0].original == "Hello world."
        assert entries[0].translation == ""
        assert entries[0].start_ms == 5100
        assert entries[0].end_ms == 6200

        # Same-language fallback_to_source must NOT duplicate original into translation
        session.translation = "one_way"
        session.translation_target_lang = "en"
        _async_run(session._finalize_sentence_async("1", orig_tokens, [], sentence_id="soniox-same"))
        same_entry = [e for e in recorder.get_entries() if e.sentence_id == "soniox-same"][0]
        assert same_entry.original == "Hello world."
        assert same_entry.translation == ""

        # LLM refine updates existing entry's translation in place
        monkeypatch.setattr(soniox_mod, "is_llm_refine_available", lambda: True)
        session._llm_refine_mode = "refine"
        session.translation_target_lang = "zh"
        session._perform_refine = AsyncMock(return_value={
            "status": "ok",
            "no_change": False,
            "refined_translation": "你好，世界（润色）",
        })
        draft_tokens = [{"text": "你好世界", "translation_status": "translation", "language": "zh"}]
        _async_run(session._finalize_sentence_async("1", orig_tokens, draft_tokens, sentence_id="soniox-2"))
        refined_entry = [e for e in recorder.get_entries() if e.sentence_id == "soniox-2"][0]
        assert refined_entry.translation == "你好，世界（润色）"

        # Retract removes interrupted sentence
        session._retract_transcript_sentence("soniox-1")
        ids = [e.sentence_id for e in recorder.get_entries()]
        assert "soniox-1" not in ids

    def test_gemini_records_wall_timing_and_handles_empty_translation(self, monkeypatch, tmp_path):
        from tests.test_gemini_session_response import _install_gemini_session_import_mocks

        _install_gemini_session_import_mocks(monkeypatch)
        monkeypatch.setattr("realtime_subtitle.core.local_store.load", lambda: {})
        import realtime_subtitle.sessions.gemini_session as gemini_mod

        stored = {
            "featurePacks.v1": json.dumps({"enabled": {"transcript": True}}),
            "transcriptSettings.v1": json.dumps({"autoSave": False, "format": "srt", "directory": str(tmp_path)}),
        }
        recorder = TranscriptRecorder(stored_settings=stored)
        recorder.begin_run()

        session = gemini_mod.GeminiSession(MagicMock(), AsyncMock())
        session.set_transcript_recorder(recorder)
        session._run_start_monotonic = time.monotonic() - 2.0
        session.translation = "none"
        session._llm_refine_mode = "off"

        msg = session._convert_gemini_message({
            "serverContent": {
                "inputTranscription": {"text": "Testing Gemini.", "languageCode": "en"},
            },
        })
        orig_tokens = msg["tokens"]
        assert len(orig_tokens) == 1
        assert isinstance(orig_tokens[0].get("_seen_at"), float)

        _async_run(session._finalize_sentence_async("1", orig_tokens, [], sentence_id="gem-1"))
        entries = recorder.get_entries()
        assert len(entries) == 1
        assert entries[0].original == "Testing Gemini."
        assert entries[0].translation == ""
        assert entries[0].start_ms >= 1500
        assert entries[0].end_ms >= entries[0].start_ms

    def test_local_session_pipeline_timing_and_run_preservation(self, monkeypatch, tmp_path):
        from realtime_subtitle.local_inference.subtitle_pipeline import _Row
        import realtime_subtitle.sessions.realtime_session_base as base_mod

        monkeypatch.setattr("realtime_subtitle.core.local_store.load", lambda: {})

        row = _Row(
            id="loc-1",
            source="Local line.",
            source_language="en",
            target_language="zh",
            order=1.0,
            requires_translation=True,
        )
        payload = row.payload()
        assert isinstance(payload["first_seen_at"], float)

        stored = {
            "featurePacks.v1": json.dumps({"enabled": {"transcript": True}}),
            "transcriptSettings.v1": json.dumps({"autoSave": False, "format": "srt", "directory": str(tmp_path)}),
        }
        recorder = TranscriptRecorder(stored_settings=stored)

        base = base_mod.RealtimeSessionBase(MagicMock(), AsyncMock())
        base.set_transcript_recorder(recorder)
        base._run_start_monotonic = payload["first_seen_at"] - 1.5
        base._begin_transcript_run()
        start_ms, end_ms = base._extract_wall_timing_ms(
            [{"_seen_at": payload["first_seen_at"]}],
            finalized_at=payload["first_seen_at"] + 0.8,
        )
        base._record_transcript_sentence(
            payload["id"],
            start_ms,
            end_ms,
            payload["source"],
            source_language=payload["source_language"],
            translation="本地句子。",
        )
        entries = recorder.get_entries()
        assert len(entries) == 1
        assert entries[0].start_ms == 1500
        assert entries[0].end_ms == 2300
        assert entries[0].translation == "本地句子。"

        # Auto-restart preserve flag skips begin_run once
        base.mark_preserve_transcript_run_once()
        assert base._consume_preserve_transcript_run() is True
        assert base._consume_preserve_transcript_run() is False


class TestWebServerTranscriptEndpoints:
    @pytest.fixture(autouse=True)
    def restore_sys_modules(self):
        orig_config = sys.modules.get("realtime_subtitle.core.config")
        orig_web_server = pop_module("realtime_subtitle.web.web_server")
        yield
        if orig_config is not None:
            set_module("realtime_subtitle.core.config", orig_config)
        else:
            pop_module("realtime_subtitle.core.config")
        if orig_web_server is not None:
            set_module("realtime_subtitle.web.web_server", orig_web_server)
        else:
            pop_module("realtime_subtitle.web.web_server")

    def _make_server(self, module, tmp_path, stored=None):
        session = MagicMock()
        session.is_paused = False
        session.get_audio_source.return_value = "system"
        base_stored = {
            "featurePacks.v1": json.dumps({
                "version": 1,
                "confirmed": True,
                "enabled": {"transcript": True},
            }),
            "transcriptSettings.v1": json.dumps({
                "autoSave": False,
                "format": "srt",
                "directory": str(tmp_path),
            }),
        }
        if stored is not None:
            base_stored = stored
        with patch.object(module.local_store, "load", return_value=base_stored):
            server = module.WebServer(session, MagicMock())
        server.broadcast_to_clients = AsyncMock()
        return server, session

    def test_transcript_settings_export_folder_open_folder_and_restart_and_pack_gating(self, tmp_path):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            module.web.json_response.side_effect = lambda data, status=200: (data, status)
            server, session = self._make_server(module, tmp_path)

            # 1. GET /transcript/status & /transcript/settings
            req_local = AsyncMock()
            req_local.remote = "127.0.0.1"
            status_body, status_code = _async_run(server.transcript_status_get_handler(req_local))
            assert status_code == 200
            assert status_body["has_entries"] is False
            assert status_body["pack_enabled"] is True

            settings_body, status_code = _async_run(server.transcript_settings_get_handler(req_local))
            assert status_code == 200
            assert settings_body["desktop_available"] is False
            assert settings_body["settings"]["format"] == "srt"

            # 2. Export without desktop callbacks -> 403
            _, code = _async_run(server.transcript_export_handler(req_local))
            assert code == 403

            # Register mocked desktop callbacks (never invokes real OS dialog or Explorer)
            exported_path = tmp_path / "dialog_out.srt"
            picked_folder = tmp_path / "picked_dir"
            opened_dirs = []
            server.set_transcript_dialog_callbacks(
                save_callback=lambda default_dir, default_name: str(exported_path),
                folder_callback=lambda default_dir: str(picked_folder),
                open_folder_callback=lambda target_dir: opened_dirs.append(target_dir),
            )

            settings_body, _ = _async_run(server.transcript_settings_get_handler(req_local))
            assert settings_body["desktop_available"] is True

            # Export with empty buffer -> 400
            _, code = _async_run(server.transcript_export_handler(req_local))
            assert code == 400

            # Record an entry and export via mocked dialog
            server.transcript_recorder.begin_run()
            server.transcript_recorder.record_sentence("s1", 0, 1000, "Hello.", "en", "你好。")
            req_export = AsyncMock()
            req_export.remote = "127.0.0.1"
            req_export.json.return_value = {"format": "srt"}
            export_body, code = _async_run(server.transcript_export_handler(req_export))
            assert code == 200
            assert export_body["status"] == "ok"
            assert exported_path.exists()
            assert exported_path.read_bytes().startswith(b"\xef\xbb\xbf")

            # Non-loopback export rejected -> 403
            req_lan = AsyncMock()
            req_lan.remote = "192.168.1.50"
            req_lan.path = "/transcript/export"
            _, code = _async_run(server.transcript_export_handler(req_lan))
            assert code == 403

            # POST /transcript/settings updates format + auto_save
            req_post_settings = AsyncMock()
            req_post_settings.remote = "127.0.0.1"
            req_post_settings.json.return_value = {"auto_save": True, "format": "txt"}
            post_body, code = _async_run(server.transcript_settings_post_handler(req_post_settings))
            assert code == 200
            assert post_body["settings"]["auto_save"] is True
            assert post_body["settings"]["format"] == "txt"

            # POST /transcript/folder uses mocked folder dialog
            folder_body, code = _async_run(server.transcript_folder_handler(req_local))
            assert code == 200
            assert folder_body["directory"] == str(picked_folder)

            # POST /transcript/open-folder calls mocked callback
            open_body, code = _async_run(server.transcript_open_folder_handler(req_local))
            assert code == 200
            assert opened_dirs == [str(picked_folder)]

            # POST /restart with {auto: true} marks preserve once
            req_auto_restart = AsyncMock()
            req_auto_restart.remote = "127.0.0.1"
            req_auto_restart.json.return_value = {"auto": True}
            server.get_api_key = MagicMock(return_value="k")
            session.start.return_value = True
            _async_run(server.restart_handler(req_auto_restart))
            session.mark_preserve_transcript_run_once.assert_called_once_with()

            # Manual POST /restart (without auto: true) must NOT call mark_preserve_transcript_run_once
            session.mark_preserve_transcript_run_once.reset_mock()
            req_manual_restart = AsyncMock()
            req_manual_restart.remote = "127.0.0.1"
            req_manual_restart.json.return_value = {}
            _async_run(server.restart_handler(req_manual_restart))
            session.mark_preserve_transcript_run_once.assert_not_called()

            # /ui-config reports transcript_desktop_available for loopback only, plus status & settings
            module.config.RELAY_AVAILABLE = False
            module.config.RELAY_MODE = False
            module.config.TRANSLATION_PROVIDER = "soniox"
            module.config.HOSTED_SONIOX_NO_TRANSLATION_FACTOR = 1.0
            module.config.llm_context_bounds.return_value = (1, 1)
            module.config.SONIOX_CUSTOM_URL = False
            module.config.ENABLE_SPEAKER_DIARIZATION = False
            module.config.HIDE_SPEAKER_LABELS = False
            module.config.SONIOX_INTERRUPT_REPAIR_ENABLED = True
            module.config.SONIOX_REGION = "us"
            module.config.CLIENT_VERSION = "1.0.0"
            module.config.get_sleep_on_silence_enabled.return_value = False
            module.config.current_server_url.return_value = ""
            module.config.SUBTITLE_SERVER_URL = ""
            module.config.is_llm_refine_available.return_value = False
            module.get_capabilities = lambda _p: {}
            module.get_language_codes_ordered = lambda _p: ["en", "zh"]
            module.LOCK_MANUAL_CONTROLS = False
            module.ENABLE_CHROMA_THEME = False
            module.LLM_REFINE_DEFAULT_MODE = "off"
            module.LLM_REFINE_DIFF_MODE = "off"
            session.get_translation_target_lang.return_value = "zh"
            session.get_llm_refine_mode.return_value = "off"
            session.get_translation_mode.return_value = "fast"

            ui_local, code = _async_run(server.ui_config_handler(req_local))
            assert code == 200
            assert ui_local["transcript_desktop_available"] is True
            assert ui_local["transcript_status"]["has_entries"] is True
            assert ui_local["transcript_settings"]["format"] == "txt"

            ui_lan, code = _async_run(server.ui_config_handler(req_lan))
            assert code == 200
            assert ui_lan["transcript_desktop_available"] is False

            # POST /feature-packs disabling transcript disables recorder gating
            req_pack_off = AsyncMock()
            req_pack_off.remote = "127.0.0.1"
            req_pack_off.json.return_value = {"packs": {"transcript": False}}
            _, code = _async_run(server.feature_packs_post_handler(req_pack_off))
            assert code == 200
            assert server.transcript_recorder.is_pack_enabled() is False
