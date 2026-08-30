import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestServer, TestClient


def async_test(coro):
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()
    return wrapper


def make_web_server():
    """mock 依赖后导入 web_server, 返回 (module, WebServer 实例)。"""
    static_dir = tempfile.mkdtemp()
    config = MagicMock()
    # create_app() 会 add_static('/', path=get_resource_path('static')),
    # 需返回真实存在的目录否则 StaticResource 构造失败。
    config.get_resource_path.return_value = static_dir
    with patch.dict(sys.modules, {
        "config": config,
        "llm_client": MagicMock(),
        "audio_capture": MagicMock(),
        "local_store": MagicMock(),
        "desktop_shortcut": MagicMock(),
    }):
        import web_server as ws_module
        ws = ws_module.WebServer(MagicMock(), MagicMock())
        ws.vr_token = "test-token"
        return ws_module, ws


class TestVrWs:
    @async_test
    async def test_auth_rejected_with_wrong_token(self):
        ws_module, ws = make_web_server()
        server = TestServer(ws.create_app())
        client = TestClient(server)
        await client.start_server()
        try:
            wsock = await client.ws_connect("/vr_ws")
            await wsock.send_str(json.dumps({"type": "auth", "session_token": "wrong"}))
            reply = await wsock.receive(timeout=2.0)
            assert reply.data == json.dumps({"type": "auth_error"})
            await wsock.close()
        finally:
            await client.close()

    @async_test
    async def test_auth_ok_receives_snapshot_on_connect(self):
        ws_module, ws = make_web_server()
        server = TestServer(ws.create_app())
        client = TestClient(server)
        await client.start_server()
        try:
            wsock = await client.ws_connect("/vr_ws")
            await wsock.send_str(json.dumps({"type": "auth", "session_token": "test-token"}))
            snapshot = json.loads((await wsock.receive(timeout=2.0)).data)
            assert snapshot["type"] == "snapshot"
            assert snapshot["payload"]["revision"] == 0
            await wsock.close()
        finally:
            await client.close()

    @async_test
    async def test_snapshot_broadcast_after_push(self):
        ws_module, ws = make_web_server()
        server = TestServer(ws.create_app())
        client = TestClient(server)
        await client.start_server()
        try:
            wsock = await client.ws_connect("/vr_ws")
            await wsock.send_str(json.dumps({"type": "auth", "session_token": "test-token"}))
            await wsock.receive(timeout=2.0)  # 初始 snapshot

            await ws.vr_overlay.push("Hello", "en", "こんにちは")
            snapshot = json.loads((await wsock.receive(timeout=2.0)).data)
            assert snapshot["payload"]["blocks"][0]["primary_text"] == "こんにちは"
            await wsock.close()
        finally:
            await client.close()

    @async_test
    async def test_status_message_keeps_connection_open(self):
        ws_module, ws = make_web_server()
        server = TestServer(ws.create_app())
        client = TestClient(server)
        await client.start_server()
        try:
            wsock = await client.ws_connect("/vr_ws")
            await wsock.send_str(json.dumps({"type": "auth", "session_token": "test-token"}))
            await wsock.receive(timeout=2.0)

            await wsock.send_str(json.dumps({"type": "status", "state": "ready"}))
            await ws.vr_overlay.push("Hello", "en", "Hi")
            snapshot = json.loads((await wsock.receive(timeout=2.0)).data)
            assert snapshot["type"] == "snapshot"  # 连接未被 status 消息破坏
            await wsock.close()
        finally:
            await client.close()
