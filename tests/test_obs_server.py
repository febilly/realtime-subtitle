import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from module_stubs import patch_modules, pop_module, set_module


EXPECTED_DEFAULT_OBS_SETTINGS = {
    "display_content": "both",
    "font_size": 32,
    "translation_font_scale": 100,
    "font_family": "system",
    "bold": False,
    "show_lang_tag": True,
    "original_color": "#e5e7eb",
    "translation_color": "#e5e7eb",
    "outline_width": 0,
    "outline_color": "#000000",
    "drop_shadow": True,
    "bg_mode": "strip",
    "bg_color": "#000000",
    "bg_opacity": 59,
    "flow_direction": "down",
    "alignment": "left",
    "max_sentences": 0,
    "auto_hide_seconds": 0,
}


def _async_run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeWebSocket:
    def __init__(self, query=None):
        self.query = query or {}
        self.sent = []
        self.closed = False

    async def prepare(self, request):
        return None

    async def send_str(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class TestWebServerObsPack:
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

    def _make_server(self, module, stored=None):
        session = MagicMock()
        session.is_paused = False
        session.get_audio_source.return_value = "system"
        session.get_translation_target_lang.return_value = "en"
        session.get_llm_refine_mode.return_value = "off"
        session.get_translation_mode.return_value = "fast"
        session.get_target_langs.return_value = ("en", "ja")
        base_stored = {
            "featurePacks.v1": json.dumps({
                "version": 1,
                "confirmed": True,
                "enabled": {"obs": False},
            }),
        }
        if stored is not None:
            base_stored = stored
        with patch.object(module.local_store, "load", return_value=base_stored):
            server = module.WebServer(session, MagicMock())
        server.broadcast_to_clients = AsyncMock()
        return server, session

    def test_obs_page_always_returns_200_even_when_pack_disabled(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            module.get_resource_path = lambda p: os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), p
            )
            module.web.FileResponse.side_effect = lambda path, headers=None: (path, headers)
            server, _ = self._make_server(module)
            assert server.is_pack_enabled("obs") is False

            req = MagicMock()
            req.remote = "192.168.1.99"
            path, headers = _async_run(server.obs_handler(req))
            assert path.endswith(os.path.join("static", "obs.html"))
            assert os.path.exists(path)
            assert "no-store" in headers["Cache-Control"]

    def test_obs_settings_validation_loopback_clamping_and_broadcast(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            module.web.json_response.side_effect = lambda data, status=200: (data, status)
            server, _ = self._make_server(module)
            server.actual_port = 18777

            req_get = AsyncMock()
            req_get.remote = "127.0.0.1"
            req_get.host = "127.0.0.1:18777"
            body, code = _async_run(server.obs_settings_get_handler(req_get))
            assert code == 200
            assert body["settings"] == EXPECTED_DEFAULT_OBS_SETTINGS
            assert body["desktop_available"] is False
            assert body["obs_url"] == "http://127.0.0.1:18777/obs"

            # LAN client rejected on POST /obs-settings
            req_lan = AsyncMock()
            req_lan.remote = "192.168.1.44"
            req_lan.path = "/obs-settings"
            _, code = _async_run(server.obs_settings_post_handler(req_lan))
            assert code == 403

            # Invalid payloads rejected with 400
            for bad_payload in (
                {"display_content": "invalid"},
                {"font_family": "comic_sans"},
                {"bg_mode": "gradient"},
                {"flow_direction": "sideways"},
                {"alignment": "justify"},
                {"bold": "yes"},
                {"show_lang_tag": 1},
                {"drop_shadow": "true"},
                {"original_color": "red"},
                {"bg_color": "#12345"},
                {"outline_width": True},
                {"auto_hide_seconds": "NaN"},
                {"max_sentences": "unlimited"},
                {"max_sentences": False},
            ):
                req_bad = AsyncMock()
                req_bad.remote = "127.0.0.1"
                req_bad.json.return_value = bad_payload
                _, code = _async_run(server.obs_settings_post_handler(req_bad))
                assert code == 400, f"Expected 400 for {bad_payload}"

            # Clamps numeric fields, accepts camelCase & snake_case, normalizes hex, ignores legacy keys, broadcasts
            req_ok = AsyncMock()
            req_ok.remote = "127.0.0.1"
            req_ok.content_type = "application/json"
            req_ok.json.return_value = {
                "font_size": 200,
                "display_content": "translation",
                "translationFontScale": 20,
                "font_family": "noto_sans_cjk",
                "bold": True,
                "showLangTag": False,
                "original_color": "#AABBCC",
                "translationColor": "#112233",
                "outline_width": 99,
                "outline_color": "#FFFFFF",
                "drop_shadow": False,
                "bg_mode": "block",
                "bg_color": "#102030",
                "bg_opacity": -5,
                "flowDirection": "up",
                "position": "top",
                "alignment": "right",
                "max_width": 10,
                "max_sentences": 50,
                "auto_hide_seconds": 99,
            }
            expected_after = {
                "display_content": "translation",
                "font_size": 120,
                "translation_font_scale": 50,
                "font_family": "noto_sans_cjk",
                "bold": True,
                "show_lang_tag": False,
                "original_color": "#aabbcc",
                "translation_color": "#112233",
                "outline_width": 8,
                "outline_color": "#ffffff",
                "drop_shadow": False,
                "bg_mode": "block",
                "bg_color": "#102030",
                "bg_opacity": 0,
                "flow_direction": "up",
                "alignment": "right",
                "max_sentences": 10,
                "auto_hide_seconds": 30,
            }
            body, code = _async_run(server.obs_settings_post_handler(req_ok))
            assert code == 200
            assert body["settings"] == expected_after
            server.broadcast_to_clients.assert_awaited_once_with({
                "type": "obs_settings",
                "settings": expected_after,
            })

            # Partial update preserves remaining fields
            req_partial = AsyncMock()
            req_partial.remote = "127.0.0.1"
            req_partial.json.return_value = {"alignment": "center", "bg_opacity": 75}
            body, code = _async_run(server.obs_settings_post_handler(req_partial))
            assert code == 200
            assert body["settings"]["alignment"] == "center"
            assert body["settings"]["bg_opacity"] == 75
            assert body["settings"]["font_family"] == "noto_sans_cjk"
            assert body["settings"]["flow_direction"] == "up"

            # Explicit 0 (unlimited) is accepted, 11 clamps to 10, negative clamps to 0
            for raw_val, expected_val in ((0, 0), (11, 10), (-4, 0), (3, 3)):
                req_max = AsyncMock()
                req_max.remote = "127.0.0.1"
                req_max.json.return_value = {"max_sentences": raw_val}
                body, code = _async_run(server.obs_settings_post_handler(req_max))
                assert code == 200
                assert body["settings"]["max_sentences"] == expected_val

    def test_legacy_stored_obs_settings_falls_back_gracefully(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            stored = {
                "obsSettings.v1": json.dumps({
                    "fontSize": 56,
                    "displayContent": "original",
                    "position": "top",
                    "maxWidth": 65,
                    "unknownField": "ignored",
                    "bgColor": "not-a-hex",
                }),
            }
            server, _ = self._make_server(module, stored=stored)
            loaded = server.get_obs_settings()
            expected = dict(EXPECTED_DEFAULT_OBS_SETTINGS)
            expected["font_size"] = 56
            expected["display_content"] = "original"
            assert loaded == expected
            assert "position" not in loaded
            assert "max_width" not in loaded

            assert server._normalize_obs_settings({"max_sentences": 0})["max_sentences"] == 0
            assert server._normalize_obs_settings({"max_sentences": 11})["max_sentences"] == 10
            assert server._normalize_obs_settings({"max_sentences": "bad"})["max_sentences"] == 0

    def test_clipboard_post_endpoint_loopback_validation_and_callback(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            module.web.json_response.side_effect = lambda data, status=200: (data, status)
            server, _ = self._make_server(module)
            copied = []
            server.set_clipboard_callback(lambda text: copied.append(text))

            assert server.desktop_window_available() is True

            req_lan = AsyncMock()
            req_lan.remote = "10.0.0.5"
            req_lan.path = "/clipboard"
            _, code = _async_run(server.clipboard_post_handler(req_lan))
            assert code == 403

            req_empty = AsyncMock()
            req_empty.remote = "127.0.0.1"
            req_empty.content_type = "application/json"
            req_empty.json.return_value = {"text": ""}
            _, code = _async_run(server.clipboard_post_handler(req_empty))
            assert code == 400

            req_ok = AsyncMock()
            req_ok.remote = "127.0.0.1"
            req_ok.content_type = "application/json"
            req_ok.json.return_value = {"text": "http://127.0.0.1:18777/obs"}
            body, code = _async_run(server.clipboard_post_handler(req_ok))
            assert code == 200
            assert body == {"status": "ok"}
            assert copied == ["http://127.0.0.1:18777/obs"]
            req_form = AsyncMock()
            req_form.remote = "127.0.0.1"
            req_form.content_type = "text/plain"
            req_form.json.return_value = {"text": "attacker"}
            _, code = _async_run(server.clipboard_post_handler(req_form))
            assert code == 415
            assert copied == ["http://127.0.0.1:18777/obs"]

    def test_obs_ws_client_tracking_initial_frame_and_notice_exclusion(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            fake_ws = _FakeWebSocket()
            module.web.WebSocketResponse.return_value = fake_ws
            server, _ = self._make_server(module)

            # While inside websocket_handler, test that obs_ws_clients tracks it
            observed_counts = []
            orig_send_str = fake_ws.send_str

            async def spy_send_str(payload):
                observed_counts.append((server.has_obs_client(), server.obs_client_count()))
                await orig_send_str(payload)

            fake_ws.send_str = spy_send_str

            req_ws = MagicMock()
            req_ws.query = {"client": "obs"}
            orig_deliver = server._deliver_osc_sensitive_filter_notice
            server._deliver_osc_sensitive_filter_notice = AsyncMock()

            _async_run(server.websocket_handler(req_ws))

            # Notice delivery must NOT run for ?client=obs
            server._deliver_osc_sensitive_filter_notice.assert_not_awaited()
            server._deliver_osc_sensitive_filter_notice = orig_deliver
            assert any(pair == (True, 1) for pair in observed_counts)
            # Cleanup after disconnect
            assert server.has_obs_client() is False
            assert server.obs_client_count() == 0

            sent_frames = [json.loads(raw) for raw in fake_ws.sent]
            obs_frames = [f for f in sent_frames if f.get("type") == "obs_settings"]
            assert obs_frames == [{"type": "obs_settings", "settings": EXPECTED_DEFAULT_OBS_SETTINGS}]

            # Direct notice helper also skips any active obs_ws_clients
            server._osc_sensitive_filter_notice_triggered = True
            server._osc_sensitive_filter_notice_delivered = False
            obs_client = AsyncMock()
            server.websocket_clients.add(obs_client)
            server.obs_ws_clients.add(obs_client)
            delivered = _async_run(server._deliver_osc_sensitive_filter_notice())
            assert delivered is False
            obs_client.send_str.assert_not_awaited()
