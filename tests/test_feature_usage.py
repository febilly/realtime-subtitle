import asyncio
import functools
import json
import os
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module_stubs import patch_modules, pop_module, set_module
import realtime_subtitle.core.config as config
import realtime_subtitle.core.local_store as local_store
from realtime_subtitle.core.transcript_recorder import TranscriptRecorder
from realtime_subtitle.hosted.feature_usage import (
    FEATURE_PACK_IDS,
    FeatureUsageReporter,
    PENDING_EXPORT_STORE_KEY,
)
from realtime_subtitle.hosted.local_relay import LocalRelayLink
from realtime_subtitle.outputs.osc_manager import OSCManager


def _async_test(coro):
    @functools.wraps(coro)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()

    return wrapper


class _FakeWs:
    def __init__(self, *, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, payload):
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(json.loads(payload))


def test_feature_usage_reporter_respects_relay_mode_and_dedupes_per_stream(monkeypatch, tmp_path):
    store_file = tmp_path / "local_store.json"
    monkeypatch.setattr(config, "LOCAL_SETTINGS_FILE", str(store_file))
    local_store.clear()

    packs = {"vrchat": True, "learning": False, "obs": True, "transcript": False}
    active = ["obs"]
    reporter = FeatureUsageReporter(
        get_enabled_packs=lambda: packs,
        get_active_stream_features=lambda: active,
    )

    # Direct / local / non-hosted mode: never sends telemetry
    monkeypatch.setattr(config, "RELAY_MODE", False)
    ws_direct = _FakeWs()
    reporter.attach_stream(ws_direct)
    assert ws_direct.sent == []
    assert reporter.report_packs_changed() is False
    assert reporter.report_feature_used("osc") is False
    assert reporter.report_feature_used("export") is False
    assert PENDING_EXPORT_STORE_KEY not in local_store.load()

    # Hosted mode: sends feature_packs + active features on attach, deduplicates per stream
    monkeypatch.setattr(config, "RELAY_MODE", True)
    ws1 = _FakeWs()
    reporter.attach_stream(ws1)
    assert ws1.sent == [
        {"type": "feature_packs", "enabled": ["vrchat", "obs"]},
        {"type": "feature_used", "feature": "obs"},
    ]

    assert reporter.report_feature_used("osc") is True
    assert reporter.report_feature_used("osc") is False  # deduplicated on same stream
    assert reporter.report_feature_used("obs") is False  # already sent at attach
    assert reporter.report_feature_used("unknown_feature") is False

    packs["learning"] = True
    assert reporter.report_packs_changed() is True
    assert ws1.sent[-2:] == [
        {"type": "feature_used", "feature": "osc"},
        {"type": "feature_packs", "enabled": ["vrchat", "learning", "obs"]},
    ]

    # Detach (sleep / stop) clears per-stream state; new stream / wake re-emits packs & active features
    reporter.detach_stream(ws1)
    assert reporter.report_feature_used("osc") is False
    ws2 = _FakeWs()
    reporter.attach_stream(ws2)
    assert ws2.sent == [
        {"type": "feature_packs", "enabled": ["vrchat", "learning", "obs"]},
        {"type": "feature_used", "feature": "obs"},
    ]
    assert reporter.report_feature_used("osc") is True


def test_export_while_offline_persists_pending_and_flushes_on_next_hosted_stream(monkeypatch, tmp_path):
    store_file = tmp_path / "local_store.json"
    monkeypatch.setattr(config, "LOCAL_SETTINGS_FILE", str(store_file))
    local_store.clear()
    monkeypatch.setattr(config, "RELAY_MODE", True)

    reporter = FeatureUsageReporter(get_enabled_packs=lambda: {"transcript": True})

    # No active stream when export completes -> persisted as pending
    assert reporter.report_feature_used("export") is False
    assert local_store.load().get(PENDING_EXPORT_STORE_KEY) == "true"

    # Next hosted stream attaches -> flushes export once and clears pending flag
    ws = _FakeWs()
    reporter.attach_stream(ws)
    assert ws.sent == [
        {"type": "feature_packs", "enabled": ["transcript"]},
        {"type": "feature_used", "feature": "export"},
    ]
    assert PENDING_EXPORT_STORE_KEY not in local_store.load()


def test_osc_and_transcript_recorder_trigger_feature_usage_on_real_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RELAY_MODE", True)
    store_file = tmp_path / "local_store.json"
    monkeypatch.setattr(config, "LOCAL_SETTINGS_FILE", str(store_file))
    local_store.clear()

    ws = _FakeWs()
    reporter = FeatureUsageReporter(get_enabled_packs=lambda: {"vrchat": True, "transcript": True})
    reporter.attach_stream(ws)

    # OSC: only triggers when /chatbox/input actually sends
    mgr = OSCManager()
    fake_udp = MagicMock()
    orig_get_udp = mgr.get_udp_client
    orig_cb = mgr._on_message_sent_callback
    try:
        mgr.get_udp_client = lambda: fake_udp
        mgr.set_message_sent_callback(lambda: reporter.report_feature_used("osc"))
        mgr._send_message_immediately("hello vrchat", False)
        assert fake_udp.send_message.called
        assert {"type": "feature_used", "feature": "osc"} in ws.sent
    finally:
        mgr.get_udp_client = orig_get_udp
        mgr.set_message_sent_callback(orig_cb)

    # TranscriptRecorder: autosave triggers on real non-empty write, export triggers on export_to_file
    rec = TranscriptRecorder(
        stored_settings={
            "transcriptSettings.v1": json.dumps({
                "auto_save": True,
                "format": "txt",
                "directory": str(tmp_path),
            }),
        },
        on_auto_saved=lambda: reporter.report_feature_used("autosave"),
        on_exported=lambda: reporter.report_feature_used("export"),
    )
    rec.set_pack_enabled(True)
    rec.begin_run()
    rec.record_sentence("s1", 1000, 2000, "hello", "en", "你好")
    assert {"type": "feature_used", "feature": "autosave"} in ws.sent

    export_path = tmp_path / "manual_export.txt"
    rec.export_to_file(str(export_path), "txt")
    assert export_path.exists()
    assert {"type": "feature_used", "feature": "export"} in ws.sent


def test_local_relay_link_send_uses_lock_safely():
    link = LocalRelayLink(lambda data: None, lambda ok, tag: None)
    fake_ws = MagicMock()
    link._ws = fake_ws
    link.send(json.dumps({"type": "feature_packs", "enabled": list(FEATURE_PACK_IDS)}))
    fake_ws.send.assert_called_once()


class _FakeWSMsg:
    def __init__(self, msg_type, data):
        self.type = msg_type
        self.data = data


class _FakeServerWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.prepare = AsyncMock()
        self.send_str = AsyncMock(side_effect=lambda s: self.sent.append(json.loads(s)))
        self.exception = MagicMock(return_value=None)

    def __aiter__(self):
        self._iter = iter(self.messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


@_async_test
async def test_web_server_reports_obs_vr_overlay_packs_and_ws_reading_features(monkeypatch):
    orig_config = sys.modules.get("realtime_subtitle.core.config")
    orig_web_server = pop_module("realtime_subtitle.web.web_server")
    try:
        mock_ws_type = SimpleNamespace(TEXT="TEXT", ERROR="ERROR")
        fake_web = MagicMock()
        fake_web.WebSocketResponse = MagicMock()
        fake_web.json_response.side_effect = lambda data, status=200: (data, status)

        with patch_modules({
            "aiohttp": MagicMock(web=fake_web, WSMsgType=mock_ws_type),
            "aiohttp.web": fake_web,
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module
            import realtime_subtitle.hosted.feature_usage as fu_module

            monkeypatch.setattr(module.config, "RELAY_MODE", True)
            monkeypatch.setattr(fu_module.config, "RELAY_MODE", True)

            session = MagicMock()
            session.is_paused = False
            session.get_audio_source.return_value = "system"
            session.get_translation_target_lang.return_value = "en"
            session.get_target_langs.return_value = ("en", "ja")
            with patch.object(module.local_store, "load", return_value={}):
                server = module.WebServer(session, MagicMock())
            server.broadcast_to_clients = AsyncMock()

            relay_ws = _FakeWs()
            server.feature_usage.attach_stream(relay_ws)
            assert relay_ws.sent == [{"type": "feature_packs", "enabled": []}]

            # feature-packs POST updates packs and pushes feature_packs frame
            packs_req = AsyncMock()
            packs_req.remote = "127.0.0.1"
            packs_req.json = AsyncMock(return_value={"packs": {"vrchat": True, "learning": True, "obs": True}})
            data, status = await server.feature_packs_post_handler(packs_req)
            assert status == 200
            assert relay_ws.sent[-1] == {
                "type": "feature_packs",
                "enabled": ["vrchat", "learning", "obs"],
            }

            # vr-overlay open + report connected -> reports vr_overlay once
            vr_mgr = MagicMock()
            vr_mgr.is_visible = False
            vr_mgr.connected = False
            vr_mgr.is_open.return_value = True
            vr_mgr.attach_mode = "body"
            server.vr_overlay_manager = vr_mgr

            vr_open_req = AsyncMock()
            vr_open_req.remote = "127.0.0.1"
            vr_open_req.json = AsyncMock(return_value={"action": "open"})
            await server.vr_overlay_post_handler(vr_open_req)
            assert {"type": "feature_used", "feature": "vr_overlay"} in relay_ws.sent

            vr_report_req = AsyncMock()
            vr_report_req.remote = "127.0.0.1"
            vr_report_req.json = AsyncMock(return_value={"action": "report", "connected": True})
            await server.vr_overlay_post_handler(vr_report_req)
            assert sum(1 for m in relay_ws.sent if m == {"type": "feature_used", "feature": "vr_overlay"}) == 1

            # OBS WS client connect -> reports obs
            obs_ws = _FakeServerWebSocket([])
            fake_web.WebSocketResponse.return_value = obs_ws
            obs_req = MagicMock()
            obs_req.query = {"client": "obs"}
            await server.websocket_handler(obs_req)
            assert {"type": "feature_used", "feature": "obs"} in relay_ws.sent

            # Main UI / overlay WS client sends reading feature_used frames -> forwarded (and unknown ignored)
            ui_ws = _FakeServerWebSocket([
                _FakeWSMsg("TEXT", json.dumps({"type": "feature_used", "feature": "furigana_ja"})),
                _FakeWSMsg("TEXT", json.dumps({"type": "feature_used", "feature": "pinyin"})),
                _FakeWSMsg("TEXT", json.dumps({"type": "feature_used", "feature": "ko_roman"})),
                _FakeWSMsg("TEXT", json.dumps({"type": "feature_used", "feature": "bogus"})),
            ])
            fake_web.WebSocketResponse.return_value = ui_ws
            ui_req = MagicMock()
            ui_req.query = {}
            await server.websocket_handler(ui_req)
            assert {"type": "feature_used", "feature": "furigana_ja"} in relay_ws.sent
            assert {"type": "feature_used", "feature": "pinyin"} in relay_ws.sent
            assert {"type": "feature_used", "feature": "ko_roman"} in relay_ws.sent
            assert not any(m.get("feature") == "bogus" for m in relay_ws.sent)
    finally:
        if orig_config is not None:
            set_module("realtime_subtitle.core.config", orig_config)
        else:
            pop_module("realtime_subtitle.core.config")
        if orig_web_server is not None:
            set_module("realtime_subtitle.web.web_server", orig_web_server)
        else:
            pop_module("realtime_subtitle.web.web_server")


def test_overlay_window_reports_reading_features_once_per_stream():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import realtime_subtitle.outputs.overlay_window as ow

    app = QApplication.instance() or QApplication([])
    assert app is not None

    with patch.object(ow.OverlayWindow, "_init_ws", lambda self: None):
        win = ow.OverlayWindow("http://127.0.0.1:8123")
    try:
        sent = []
        win.ws_client = SimpleNamespace(send_json=lambda payload: (sent.append(payload) or True))

        # Calling _furigana_line_html reports once per language per stream
        win._furigana_line_html([("日本", "にほん")], [{"text": "日本", "is_final": True}], "ja", 20, 4, 11)
        win._furigana_line_html([("語", "ご")], [{"text": "語", "is_final": True}], "ja", 20, 4, 11)
        win._furigana_line_html([("你", "nǐ")], [{"text": "你", "is_final": True}], "cmn", 20, 4, 11)
        win._furigana_line_html([("한", "han")], [{"text": "한", "is_final": True}], "ko", 20, 4, 11)
        assert sent == [
            {"type": "feature_used", "feature": "furigana_ja"},
            {"type": "feature_used", "feature": "pinyin"},
            {"type": "feature_used", "feature": "ko_roman"},
        ]

        # session_connected clears dedup so next relay stream reports again
        win._on_message(json.dumps({"type": "session_connected"}))
        win._furigana_line_html([("日本", "にほん")], [{"text": "日本", "is_final": True}], "ja", 20, 4, 11)
        assert sent[-1] == {"type": "feature_used", "feature": "furigana_ja"}
    finally:
        win.close()
