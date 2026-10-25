import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp import web


# Execute just the handler source with a narrow fake server; importing the whole
# application would initialize optional audio/device dependencies unrelated to REST.
source = Path('web_server.py').read_text(encoding='utf-8')
start = source.index('    async def account_announcements_handler(')
end = source.index('    async def account_registration_info_handler(', start)
scope = {'web': web}
exec('class Handler:\n' + source[start:end], scope)


def test_announcements_proxy_public_route_and_preserves_error_status():
    server = scope['Handler']()
    server._is_loopback_request = lambda request: True
    server._server_request = AsyncMock(return_value=(200, {'announcements': []}))
    response = asyncio.run(server.account_announcements_handler(SimpleNamespace(remote='127.0.0.1')))
    assert response.status == 200
    assert json.loads(response.text) == {'announcements': []}
    assert response.headers['Cache-Control'] == 'no-store'
    server._server_request.assert_awaited_once_with('GET', '/public/announcements')
    server._server_request = AsyncMock(return_value=(503, {'detail': 'unavailable'}))
    response = asyncio.run(server.account_announcements_handler(SimpleNamespace(remote='127.0.0.1')))
    assert response.status == 503


def test_announcements_proxy_rejects_non_loopback_before_network_access():
    server = scope['Handler']()
    server._is_loopback_request = lambda request: False
    server._server_request = AsyncMock()
    response = asyncio.run(server.account_announcements_handler(SimpleNamespace(remote='198.51.100.1')))
    assert response.status == 403
    server._server_request.assert_not_awaited()
