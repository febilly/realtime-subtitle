import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@contextmanager
def loaded_web_server():
    # Several legacy tests replace config at collection time. Match the existing
    # web-server test isolation pattern and avoid importing this module globally.
    with patch.dict(sys.modules, {
        "aiohttp": MagicMock(),
        "aiohttp.web": MagicMock(),
        "config": MagicMock(),
        "llm_client": MagicMock(),
    }):
        import web_server

        yield web_server


@pytest.mark.asyncio
async def test_desktop_shortcut_status_is_loopback_only():
    with loaded_web_server() as module:
        server = module.WebServer(MagicMock(), MagicMock())
        request = AsyncMock()
        request.remote = "10.0.0.8"
        with patch.object(module.web, "json_response", side_effect=lambda data, status=200: (data, status)), \
                patch.object(module.desktop_shortcut, "get_shortcut_status") as shortcut_status:
            response, status = await server.desktop_shortcut_get_handler(request)
    assert status == 403
    assert response["message"] == "localhost only"
    shortcut_status.assert_not_called()


@pytest.mark.asyncio
async def test_desktop_shortcut_status_reports_packaged_client_state():
    with loaded_web_server() as module:
        server = module.WebServer(MagicMock(), MagicMock())
        request = AsyncMock()
        request.remote = "127.0.0.1"
        expected = {"available": True, "exists": False, "matched": 0}
        with patch.object(module.web, "json_response", side_effect=lambda data, status=200: (data, status)), \
                patch.object(module.desktop_shortcut, "get_shortcut_status", return_value=expected):
            response, status = await server.desktop_shortcut_get_handler(request)
    assert status == 200
    assert response == expected


@pytest.mark.asyncio
async def test_desktop_shortcut_creation_calls_native_helper():
    with loaded_web_server() as module:
        server = module.WebServer(MagicMock(), MagicMock())
        request = AsyncMock()
        request.remote = "::1"
        request.json.return_value = {"action": "create"}
        expected = {"available": True, "exists": True, "created": True}
        with patch.object(module.web, "json_response", side_effect=lambda data, status=200: (data, status)), \
                patch.object(module.desktop_shortcut, "create_desktop_shortcut", return_value=expected) as create:
            response, status = await server.desktop_shortcut_post_handler(request)
    assert status == 200
    assert response == expected
    create.assert_called_once_with()
