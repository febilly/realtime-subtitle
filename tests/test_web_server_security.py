import json
import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, call, patch
import pytest

# Add root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def async_test(coro):
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()
    return wrapper

class TestWebServerSecurity:
    def mock_session(self):
        session = MagicMock()
        session.get_audio_source.return_value = "system"
        session.set_audio_source.return_value = (True, "ok")
        session.get_microphone_device_id.return_value = ""
        session.set_microphone_device_id.return_value = (True, "ok")
        session.get_output_device_id.return_value = ""
        session.set_output_device_id.return_value = (True, "ok")
        return session

    @async_test
    async def test_output_device_updates_provider_manager_preference(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(), "aiohttp.web": MagicMock(),
            "config": MagicMock(), "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            from web_server import WebServer
            import aiohttp.web as web

            ws_module.LOCK_MANUAL_CONTROLS = False
            session = self.mock_session()
            session.get_output_device_id.return_value = "out-1"
            ws = WebServer(session, self.mock_logger())
            ws.provider_manager = MagicMock()
            request = AsyncMock()
            request.json.return_value = {"id": "out-1"}
            with patch.object(ws_module, "list_output_devices", return_value={
                "available": True, "default": {"id": "out-0", "name": "Default"},
                "devices": [{"id": "out-1", "name": "USB"}],
            }):
                web.json_response.side_effect = lambda data, status=200: (data, status)
                response, status = await ws.output_device_set_handler(request)

            assert status == 200
            assert response["id"] == "out-1"
            session.set_output_device_id.assert_called_once_with("out-1")
            assert ws.provider_manager.output_device_id == "out-1"

    def mock_logger(self):
        return MagicMock()

    @async_test
    async def test_set_audio_source_valid(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import config
            config.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            m_session = self.mock_session()
            m_logger = self.mock_logger()
            ws = WebServer(m_session, m_logger)

            # Prepare request
            payload = {"source": "microphone"}
            request = AsyncMock()
            request.json.return_value = payload

            # Mock web.json_response
            web.json_response.side_effect = lambda data, status=200: (data, status)

            # Call handler
            response_data, status = await ws.set_audio_source_handler(request)

            # Verify
            assert status == 200
            assert response_data["status"] == "ok"
            m_session.set_audio_source.assert_called_once_with("microphone")

    @async_test
    async def test_set_audio_source_updates_provider_manager_preference(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            m_session = self.mock_session()
            m_session.get_audio_source.return_value = "microphone"
            ws = WebServer(m_session, self.mock_logger())
            ws.provider_manager = MagicMock()
            ws.provider_manager.audio_source = "system"

            request = AsyncMock()
            request.json.return_value = {"source": "microphone"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.set_audio_source_handler(request)

            assert status == 200
            assert response_data["source"] == "microphone"
            assert ws.provider_manager.audio_source == "microphone"

    def mock_manager(self):
        manager = MagicMock()
        manager.boot_id = "deadbeef"
        manager.provider = "gemini"
        manager.translation_mode = "one_way"
        manager.setup_required = True
        manager.apply_provider = AsyncMock(return_value={
            "started": False,
            "setup_required": True,
            "downgraded_two_way": False,
            "error": None,
        })
        return manager

    @async_test
    async def test_setup_rejected_from_non_loopback(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            ws = WebServer(self.mock_session(), self.mock_logger())
            ws.provider_manager = self.mock_manager()

            request = AsyncMock()
            request.remote = "10.0.0.5"
            request.json.return_value = {"provider": "gemini"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.setup_handler(request)

            assert status == 403
            assert response_data["status"] == "error"
            ws.provider_manager.apply_provider.assert_not_called()

    @async_test
    async def test_setup_rejected_when_locked(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = True
            from web_server import WebServer
            import aiohttp.web as web

            ws = WebServer(self.mock_session(), self.mock_logger())
            ws.provider_manager = self.mock_manager()

            request = AsyncMock()
            request.remote = "127.0.0.1"
            request.json.return_value = {"provider": "gemini"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.setup_handler(request)

            assert status == 403
            ws.provider_manager.apply_provider.assert_not_called()
            ws_module.LOCK_MANUAL_CONTROLS = False

    @async_test
    async def test_setup_applies_provider_from_loopback(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            ws = WebServer(self.mock_session(), self.mock_logger())
            manager = self.mock_manager()
            ws.provider_manager = manager

            request = AsyncMock()
            request.remote = "127.0.0.1"
            # No api_key -> skip validation, provider switch only.
            request.json.return_value = {"provider": "gemini"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.setup_handler(request)

            assert status == 200
            assert response_data["status"] == "ok"
            assert response_data["boot_id"] == "deadbeef"
            manager.apply_provider.assert_awaited_once()

    @async_test
    async def test_setup_invalid_provider(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            ws = WebServer(self.mock_session(), self.mock_logger())
            ws.provider_manager = self.mock_manager()

            request = AsyncMock()
            request.remote = "127.0.0.1"
            request.json.return_value = {"provider": "bogus"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.setup_handler(request)

            assert status == 400
            ws.provider_manager.apply_provider.assert_not_called()

    @async_test
    async def test_restart_reports_conflict_when_session_start_is_ignored(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            session = self.mock_session()
            session.get_translation_target_lang.return_value = "zh"
            session.get_target_langs.return_value = ("en", "zh")
            session.start.return_value = False
            ws = WebServer(session, self.mock_logger())
            ws.get_api_key = MagicMock(return_value="relay-token")
            ws.provider_manager = MagicMock()
            ws.provider_manager.translation_mode = "one_way"
            ws.provider_manager._sync_ipc = AsyncMock()
            ws.broadcast_to_clients = AsyncMock()

            request = AsyncMock()
            request.json.return_value = {"auto": True}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.restart_handler(request)

            assert status == 409
            assert response_data == {
                "status": "error",
                "message": "Recognition session is still stopping; start request was ignored",
            }
            session.stop.assert_called_once_with()
            session.start.assert_called_once()
            ws.provider_manager._sync_ipc.assert_not_awaited()

    @async_test
    async def test_account_web_login_url_uses_short_web_login_code(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import config
            config.RELAY_AVAILABLE = True
            config.SUBTITLE_SERVER_URL = "https://subtitle.example"
            config.relay_rest_url.side_effect = lambda path="": "https://subtitle.example" + (path if str(path).startswith("/") else "/" + str(path))
            from web_server import WebServer
            import aiohttp.web as web

            ws = WebServer(self.mock_session(), self.mock_logger())
            manager = self.mock_manager()
            manager.relay_token = "ss_token"
            ws.provider_manager = manager
            ws._server_request = AsyncMock(return_value=(200, {
                "web_login_code": "WEB-123",
                "expires_at": "2026-06-22T00:01:00.000Z",
            }))

            request = AsyncMock()
            request.remote = "127.0.0.1"
            request.query = {}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.account_web_login_url_handler(request)

            assert status == 200
            assert response_data["url"] == "https://subtitle.example/app/#/login?web_login_code=WEB-123"
            ws._server_request.assert_awaited_once_with("POST", "/me/web-login-code", token="ss_token")

    @async_test
    async def test_account_web_login_url_can_open_allowed_pages_after_login(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import config
            config.RELAY_AVAILABLE = True
            config.SUBTITLE_SERVER_URL = "https://subtitle.example"
            config.relay_rest_url.side_effect = lambda path="": "https://subtitle.example" + (path if str(path).startswith("/") else "/" + str(path))
            from web_server import WebServer
            import aiohttp.web as web

            ws = WebServer(self.mock_session(), self.mock_logger())
            manager = self.mock_manager()
            manager.relay_token = "ss_token"
            ws.provider_manager = manager
            ws._server_request = AsyncMock(return_value=(200, {
                "web_login_code": "WEB-123",
                "expires_at": "2026-06-22T00:01:00.000Z",
            }))

            web.json_response.side_effect = lambda data, status=200: (data, status)

            for next_path in ("/invite", "/tickets"):
                request = AsyncMock()
                request.remote = "127.0.0.1"
                request.query = {"next": next_path}

                response_data, status = await ws.account_web_login_url_handler(request)

                assert status == 200
                assert response_data["url"] == (
                    "https://subtitle.example/app/#/login?web_login_code=WEB-123"
                    f"&next=%2F{next_path.lstrip('/')}"
                )

    @async_test
    async def test_account_ticket_proxies_use_the_saved_relay_token(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import config
            config.RELAY_AVAILABLE = True
            from web_server import WebServer
            import aiohttp.web as web

            ws = WebServer(self.mock_session(), self.mock_logger())
            manager = self.mock_manager()
            manager.relay_token = "ss_token"
            ws.provider_manager = manager
            ws._server_request = AsyncMock(side_effect=[
                (200, {"tickets": [{"id": "ticket_abc"}]}),
                (200, {"unread_ticket_count": 1, "admin_initiated_count": 1}),
                (200, {"messages": [{"sender": "admin"}]}),
            ])
            web.json_response.side_effect = lambda data, status=200: (data, status)

            list_request = AsyncMock()
            list_request.remote = "127.0.0.1"
            detail_request = AsyncMock()
            detail_request.remote = "127.0.0.1"
            detail_request.match_info = {"ticket_id": "ticket_abc"}

            list_data, list_status = await ws.account_tickets_handler(list_request)
            unread_data, unread_status = await ws.account_ticket_unread_summary_handler(list_request)
            detail_data, detail_status = await ws.account_ticket_detail_handler(detail_request)

            assert list_status == 200
            assert list_data["tickets"][0]["id"] == "ticket_abc"
            assert unread_status == 200
            assert unread_data["admin_initiated_count"] == 1
            assert detail_status == 200
            assert detail_data["messages"][0]["sender"] == "admin"
            assert ws._server_request.await_args_list == [
                call("GET", "/me/tickets", token="ss_token"),
                call("GET", "/me/tickets/unread-summary", token="ss_token"),
                call("GET", "/me/tickets/ticket_abc", token="ss_token"),
            ]

    @async_test
    async def test_ui_config_exposes_client_version_policy(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import config
            config.RELAY_AVAILABLE = True
            config.SUBTITLE_SERVER_URL = "https://subtitle.example"
            config.CLIENT_VERSION = "1.2.3"
            config.TRANSLATION_PROVIDER = "soniox"
            config.SONIOX_REGION = "us"
            config.SONIOX_CUSTOM_URL = ""
            config.ENABLE_SPEAKER_DIARIZATION = True
            from web_server import WebServer
            import aiohttp.web as web

            session = self.mock_session()
            session.get_translation_target_lang.return_value = "en"
            session.get_llm_refine_mode.return_value = "off"
            ws = WebServer(session, self.mock_logger())
            ws._server_request = AsyncMock(return_value=(200, {
                "client_latest_version": "1.4.0",
                "client_minimum_version": "1.1.0",
                "client_update_url": "https://subtitle.example/download",
                "client_update_notes": "Bug fixes",
            }))

            web.json_response.side_effect = lambda data, status=200: (data, status)
            response_data, status = await ws.ui_config_handler(AsyncMock())

            assert status == 200
            assert response_data["client_version"] == "1.2.3"
            assert response_data["client_latest_version"] == "1.4.0"
            assert response_data["client_minimum_version"] == "1.1.0"
            assert response_data["client_update_url"] == "https://subtitle.example/download"
            assert response_data["client_update_notes"] == "Bug fixes"

    @async_test
    async def test_set_audio_source_invalid(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import config
            config.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            m_session = self.mock_session()
            m_logger = self.mock_logger()
            ws = WebServer(m_session, m_logger)

            # Prepare request with invalid source
            payload = {"source": "invalid_source"}
            request = AsyncMock()
            request.json.return_value = payload

            # Mock web.json_response
            web.json_response.side_effect = lambda data, status=200: (data, status)

            # After fix, it should NOT call set_audio_source
            m_session.set_audio_source.return_value = (False, "Invalid audio source")

            # Call handler
            response_data, status = await ws.set_audio_source_handler(request)

            # Verify
            assert status == 400
            assert response_data["status"] == "error"
            assert response_data["message"] == "Invalid audio source"
            m_session.set_audio_source.assert_not_called()

    @async_test
    async def test_overlay_rejected_when_locked(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = True
            from web_server import WebServer
            import aiohttp.web as web

            manager = MagicMock()
            manager.open.return_value = True
            ws = WebServer(self.mock_session(), self.mock_logger())
            ws.overlay_manager = manager

            request = AsyncMock()
            request.json.return_value = {"action": "toggle"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.overlay_post_handler(request)

            assert status == 403
            assert response_data["status"] == "error"
            manager.open.assert_not_called()
            ws_module.LOCK_MANUAL_CONTROLS = False

    @async_test
    async def test_microphone_device_rejected_when_locked(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = True
            from web_server import WebServer
            import aiohttp.web as web

            session = self.mock_session()
            ws = WebServer(session, self.mock_logger())

            request = AsyncMock()
            request.json.return_value = {"id": "mic-1"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.microphone_device_set_handler(request)

            assert status == 403
            assert response_data["status"] == "error"
            session.set_microphone_device_id.assert_not_called()
            ws_module.LOCK_MANUAL_CONTROLS = False

    @async_test
    async def test_microphone_device_updates_provider_manager_preference(self):
        with patch.dict(sys.modules, {
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "config": MagicMock(),
            "llm_client": MagicMock(),
        }):
            import web_server as ws_module
            ws_module.LOCK_MANUAL_CONTROLS = False
            from web_server import WebServer
            import aiohttp.web as web

            session = self.mock_session()
            session.get_microphone_device_id.return_value = "mic-1"
            ws = WebServer(session, self.mock_logger())
            ws.provider_manager = MagicMock()
            ws.provider_manager.microphone_device_id = ""
            ws._microphone_payload = lambda: {
                "available": True,
                "default": None,
                "devices": [{"id": "mic-1", "name": "Mic 1", "is_default": False}],
                "selected_id": "",
            }

            request = AsyncMock()
            request.json.return_value = {"id": "mic-1"}
            web.json_response.side_effect = lambda data, status=200: (data, status)

            response_data, status = await ws.microphone_device_set_handler(request)

            assert status == 200
            assert response_data["id"] == "mic-1"
            assert ws.provider_manager.microphone_device_id == "mic-1"
            session.set_microphone_device_id.assert_called_once_with("mic-1")
