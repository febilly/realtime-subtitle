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
    def mock_soniox_session(self):
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

            m_session = self.mock_soniox_session()
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

            m_session = self.mock_soniox_session()
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
