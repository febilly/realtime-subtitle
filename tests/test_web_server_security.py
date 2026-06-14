import json
import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
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
        return session

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
