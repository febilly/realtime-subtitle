import importlib
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _Request:
    def __init__(self, payload=None):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture
def real_web_server_module():
    previous_config = sys.modules.pop("config", None)
    previous_web_server = sys.modules.pop("web_server", None)
    try:
        importlib.import_module("config")
        web_server = importlib.import_module("web_server")
        yield web_server
    finally:
        sys.modules.pop("web_server", None)
        sys.modules.pop("config", None)
        if previous_web_server is not None:
            sys.modules["web_server"] = previous_web_server
        if previous_config is not None:
            sys.modules["config"] = previous_config


def _json(response):
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_osc_sensitive_filter_defaults_on_and_hot_applies(real_web_server_module):
    web_server = real_web_server_module
    server = web_server.WebServer(MagicMock(), MagicMock())
    previous = web_server.osc_manager.get_sensitive_filter_enabled()
    try:
        web_server.osc_manager.set_sensitive_filter_enabled(True)
        response = await server.osc_sensitive_filter_get_handler(_Request())
        assert response.status == 200
        assert _json(response) == {"enabled": True}

        with patch.object(web_server, "LOCK_MANUAL_CONTROLS", False):
            response = await server.osc_sensitive_filter_set_handler(_Request({"enabled": False}))
        assert response.status == 200
        assert _json(response) == {"status": "ok", "enabled": False}
        assert web_server.osc_manager.get_sensitive_filter_enabled() is False
    finally:
        web_server.osc_manager.set_sensitive_filter_enabled(previous)


@pytest.mark.asyncio
async def test_osc_sensitive_filter_rejects_invalid_or_locked_changes(real_web_server_module):
    web_server = real_web_server_module
    server = web_server.WebServer(MagicMock(), MagicMock())

    with patch.object(web_server, "LOCK_MANUAL_CONTROLS", False):
        invalid = await server.osc_sensitive_filter_set_handler(_Request({"enabled": "false"}))
        assert invalid.status == 400

    with patch.object(web_server, "LOCK_MANUAL_CONTROLS", True):
        locked = await server.osc_sensitive_filter_set_handler(_Request({"enabled": False}))
        assert locked.status == 403


@pytest.mark.asyncio
async def test_osc_sensitive_filter_notice_defaults_on_and_hot_applies(real_web_server_module):
    web_server = real_web_server_module
    with patch.object(web_server.local_store, "load", return_value={}):
        server = web_server.WebServer(MagicMock(), MagicMock())
    previous = web_server.osc_manager.get_sensitive_filter_notice_enabled()
    try:
        web_server.osc_manager.set_sensitive_filter_notice_enabled(True)
        response = await server.osc_sensitive_filter_notice_get_handler(_Request())
        assert response.status == 200
        assert _json(response) == {"disabled": False}

        with patch.object(web_server, "LOCK_MANUAL_CONTROLS", False):
            response = await server.osc_sensitive_filter_notice_set_handler(
                _Request({"disabled": True})
            )
        assert response.status == 200
        assert _json(response) == {"status": "ok", "disabled": True}
        assert web_server.osc_manager.get_sensitive_filter_notice_enabled() is False
    finally:
        web_server.osc_manager.set_sensitive_filter_notice_enabled(previous)


@pytest.mark.asyncio
async def test_osc_sensitive_filter_notice_rejects_invalid_or_locked_changes(
    real_web_server_module,
):
    web_server = real_web_server_module
    with patch.object(web_server.local_store, "load", return_value={}):
        server = web_server.WebServer(MagicMock(), MagicMock())

    with patch.object(web_server, "LOCK_MANUAL_CONTROLS", False):
        invalid = await server.osc_sensitive_filter_notice_set_handler(
            _Request({"disabled": "true"})
        )
        assert invalid.status == 400

    with patch.object(web_server, "LOCK_MANUAL_CONTROLS", True):
        locked = await server.osc_sensitive_filter_notice_set_handler(
            _Request({"disabled": True})
        )
        assert locked.status == 403


@pytest.mark.asyncio
async def test_osc_sensitive_filter_notice_waits_for_regular_ui_and_delivers_once(
    real_web_server_module,
):
    web_server = real_web_server_module
    with patch.object(web_server.local_store, "load", return_value={}):
        server = web_server.WebServer(MagicMock(), MagicMock())
    server._osc_sensitive_filter_notice_triggered = True

    assert await server._deliver_osc_sensitive_filter_notice() is False

    overlay = AsyncMock()
    server.overlay_ws = overlay
    server.websocket_clients.add(overlay)
    assert await server._deliver_osc_sensitive_filter_notice() is False
    overlay.send_str.assert_not_awaited()

    main_ui = AsyncMock()
    server.websocket_clients.add(main_ui)
    assert await server._deliver_osc_sensitive_filter_notice() is True
    main_ui.send_str.assert_awaited_once_with(
        json.dumps({"type": "osc_sensitive_filter_triggered"})
    )
    overlay.send_str.assert_not_awaited()

    assert await server._deliver_osc_sensitive_filter_notice() is False
    main_ui.send_str.assert_awaited_once()


@pytest.mark.asyncio
async def test_osc_sensitive_filter_notice_retries_after_a_stale_ui_send_fails(
    real_web_server_module,
):
    web_server = real_web_server_module
    with patch.object(web_server.local_store, "load", return_value={}):
        server = web_server.WebServer(MagicMock(), MagicMock())
    server._osc_sensitive_filter_notice_triggered = True

    stale_ui = AsyncMock()
    stale_ui.send_str.side_effect = ConnectionError("closed")
    server.websocket_clients.add(stale_ui)
    assert await server._deliver_osc_sensitive_filter_notice() is False
    assert server._osc_sensitive_filter_notice_delivered is False

    live_ui = AsyncMock()
    server.websocket_clients.add(live_ui)
    assert await server._deliver_osc_sensitive_filter_notice() is True
    live_ui.send_str.assert_awaited_once()
