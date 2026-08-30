import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

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
    with patch.dict(sys.modules, {
        "config": MagicMock(),
        "llm_client": MagicMock(),
        "audio_capture": MagicMock(),
        "local_store": MagicMock(),
        "desktop_shortcut": MagicMock(),
    }):
        import web_server as ws_module
        ws = ws_module.WebServer(MagicMock(), MagicMock())
        ws.config = ws_module.config
        # create_app() 会 add_static('/', path=get_resource_path('static')),
        # 需返回真实存在的目录否则 StaticResource 构造失败。
        ws.config.get_resource_path.return_value = tempfile.mkdtemp()
        return ws_module, ws


class TestVrOverlayEndpoint:
    @async_test
    async def test_get_state_defaults_to_disabled(self):
        ws_module, ws = make_web_server()
        server = TestServer(ws.create_app())
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.get("/vr-overlay")
            assert resp.status == 200
            data = await resp.json()
            assert data["enabled"] is False
            assert data["status"] == "stopped"
        finally:
            await client.close()

    @async_test
    async def test_post_enable_calls_manager_start(self):
        ws_module, ws = make_web_server()
        mgr = MagicMock()
        mgr.status = "starting"
        ws.vr_overlay_manager = mgr
        server = TestServer(ws.create_app())
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/vr-overlay", json={"enabled": True})
            assert resp.status == 200
            data = await resp.json()
            assert data["enabled"] is True
            mgr.start.assert_called_once()
        finally:
            await client.close()

    @async_test
    async def test_post_enable_prefers_orchestration_closures(self):
        ws_module, ws = make_web_server()
        mgr = MagicMock()
        mgr.status = "starting"
        ws.vr_overlay_manager = mgr
        called = []
        ws.vr_overlay_start = lambda: called.append("start")
        ws.vr_overlay_stop = lambda: called.append("stop")
        server = TestServer(ws.create_app())
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/vr-overlay", json={"enabled": True})
            assert resp.status == 200
            assert called == ["start"]  # 编排闭包优先, 不走 manager
            mgr.start.assert_not_called()

            resp = await client.post("/vr-overlay", json={"enabled": False})
            assert resp.status == 200
            assert called == ["start", "stop"]
            mgr.close.assert_not_called()
        finally:
            await client.close()
