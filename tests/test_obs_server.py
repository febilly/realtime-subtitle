import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from module_stubs import patch_modules, pop_module, set_module


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
            file_responses = []
            module.web.FileResponse.side_effect = lambda path, headers=None: (path, headers)
            server, _ = self._make_server(module)
            assert server.is_pack_enabled("obs") is False

            req = MagicMock()
            req.remote = "192.168.1.99"
            path, headers = _async_run(server.obs_handler(req))
            file_responses.append(path)
            assert path.endswith(os.path.join("static", "obs.html"))
            assert os.path.exists(path)
            assert "no-store" in headers["Cache-Control"]

    def test_obs_settings_validation_loopback_and_broadcast(self):
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
            assert body["settings"] == {"font_size": 42, "display_content": "both"}
            assert body["desktop_available"] is False
            assert body["obs_url"] == "http://127.0.0.1:18777/obs"

            # LAN client rejected on POST /obs-settings
            req_lan = AsyncMock()
            req_lan.remote = "192.168.1.44"
            req_lan.path = "/obs-settings"
            _, code = _async_run(server.obs_settings_post_handler(req_lan))
            assert code == 403

            # Invalid display_content rejected
            req_bad = AsyncMock()
            req_bad.remote = "127.0.0.1"
            req_bad.json.return_value = {"display_content": "invalid"}
            _, code = _async_run(server.obs_settings_post_handler(req_bad))
            assert code == 400

            # Clamps font size and broadcasts obs_settings frame
            req_ok = AsyncMock()
            req_ok.remote = "127.0.0.1"
            req_ok.json.return_value = {"font_size": 200, "display_content": "translation"}
            body, code = _async_run(server.obs_settings_post_handler(req_ok))
            assert code == 200
            assert body["settings"] == {"font_size": 120, "display_content": "translation"}
            server.broadcast_to_clients.assert_awaited_once_with({
                "type": "obs_settings",
                "settings": {"font_size": 120, "display_content": "translation"},
            })

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
            req_empty.json.return_value = {"text": ""}
            _, code = _async_run(server.clipboard_post_handler(req_empty))
            assert code == 400

            req_ok = AsyncMock()
            req_ok.remote = "127.0.0.1"
            req_ok.json.return_value = {"text": "http://127.0.0.1:18777/obs"}
            body, code = _async_run(server.clipboard_post_handler(req_ok))
            assert code == 200
            assert body == {"status": "ok"}
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
            assert obs_frames == [{"type": "obs_settings", "settings": {"font_size": 42, "display_content": "both"}}]

            # Direct notice helper also skips any active obs_ws_clients
            server._osc_sensitive_filter_notice_triggered = True
            server._osc_sensitive_filter_notice_delivered = False
            obs_client = AsyncMock()
            server.websocket_clients.add(obs_client)
            server.obs_ws_clients.add(obs_client)
            delivered = _async_run(server._deliver_osc_sensitive_filter_notice())
            assert delivered is False
            obs_client.send_str.assert_not_awaited()
