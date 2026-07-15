import asyncio
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import WSMsgType

class FakeWebSocketContext:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.fixture
def ticket_notification_runtime():
    previous_config = sys.modules.pop("config", None)
    previous_web_server = sys.modules.pop("web_server", None)
    try:
        config = importlib.import_module("config")
        web_server = importlib.import_module("web_server")
        yield config, web_server.WebServer
    finally:
        sys.modules.pop("web_server", None)
        sys.modules.pop("config", None)
        if previous_web_server is not None:
            sys.modules["web_server"] = previous_web_server
        if previous_config is not None:
            sys.modules["config"] = previous_config


@pytest.mark.asyncio
async def test_ticket_notification_socket_forwards_changes(monkeypatch, ticket_notification_runtime):
    config, WebServer = ticket_notification_runtime
    server = WebServer(MagicMock(), MagicMock())
    server.provider_manager = SimpleNamespace(relay_token="ss_test")
    server.broadcast_to_clients = AsyncMock()
    event = {"type": "ticket_changed", "actor": "admin", "action": "replied"}

    upstream = MagicMock()
    upstream.closed = False
    upstream.close = AsyncMock()
    upstream.receive = AsyncMock(side_effect=[
        SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(event)),
        asyncio.CancelledError(),
    ])
    http = MagicMock()
    http.ws_connect.return_value = FakeWebSocketContext(upstream)
    server._get_http_session = AsyncMock(return_value=http)
    monkeypatch.setattr(config, "RELAY_AVAILABLE", True)
    monkeypatch.setattr(config, "relay_rest_url", lambda path: f"https://subtitle.example{path}")

    with pytest.raises(asyncio.CancelledError):
        await server._ticket_notification_loop()

    http.ws_connect.assert_called_once_with(
        "wss://subtitle.example/me/tickets/events",
        headers={"Authorization": "Bearer ss_test"},
        heartbeat=30,
        autoping=True,
    )
    server.broadcast_to_clients.assert_any_await(
        {"type": "ticket_notifications_ready", "connected": True}
    )
    server.broadcast_to_clients.assert_any_await(event)


@pytest.mark.asyncio
async def test_ticket_notification_socket_waits_locally_without_credentials(monkeypatch, ticket_notification_runtime):
    config, WebServer = ticket_notification_runtime
    server = WebServer(MagicMock(), MagicMock())
    server.provider_manager = SimpleNamespace(relay_token="")
    server._get_http_session = AsyncMock()
    monkeypatch.setattr(config, "RELAY_AVAILABLE", True)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await server._ticket_notification_loop()

    server._get_http_session.assert_not_awaited()
