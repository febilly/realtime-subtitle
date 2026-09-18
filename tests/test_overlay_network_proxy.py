import asyncio
import contextlib
import urllib.request
import pytest

import overlay_window


def test_ws_connect_kwargs_disables_proxy():
    """WsClient 必须显式指定 proxy=None，防止被 Windows 系统代理劫持。"""
    kwargs = overlay_window._ws_connect_kwargs()
    assert kwargs.get("proxy") is None, (
        "overlay 悬浮窗是本机 127.0.0.1 通信，必须显式传递 proxy=None 避免系统代理拦截"
    )


def test_ws_client_passes_proxy_none_to_connect(monkeypatch):
    """WsClient._main 实际调用 websockets.connect 时必须带上 proxy=None。"""
    recorded_kwargs = []

    bridge = overlay_window.WsBridge()
    client = overlay_window.WsClient("ws://127.0.0.1:12345/ws?client=overlay", bridge)

    @contextlib.asynccontextmanager
    async def fake_connect(uri, **kwargs):
        recorded_kwargs.append(kwargs)
        client.stop()
        yield None

    monkeypatch.setattr(overlay_window.websockets, "connect", fake_connect)

    asyncio.run(client._main())

    assert len(recorded_kwargs) > 0, "websockets.connect 应该被调用至少一次"
    assert recorded_kwargs[0].get("proxy") is None, (
        f"实际调用参数为 {recorded_kwargs[0]}，缺少 proxy=None"
    )


def test_overlay_post_uses_proxy_free_opener(monkeypatch):
    """_post 和 _post_json 请求本机 server_url 时不能走系统代理。"""
    opened_urls = []

    class FakeResponse:
        def read(self):
            return b'{"status": "ok"}'

    class FakeOpener:
        def open(self, req, timeout=None):
            opened_urls.append(req.full_url)
            return FakeResponse()

    fake_opener = FakeOpener()
    monkeypatch.setattr(overlay_window, "_local_http_opener", fake_opener, raising=False)

    class DummyOverlay:
        server_url = "http://127.0.0.1:12345"
        _post = overlay_window.OverlayWindow._post
        _post_json = overlay_window.OverlayWindow._post_json

    dummy = DummyOverlay()
    assert dummy._post("/overlay", {"action": "close"}) is True
    res = dummy._post_json("/furigana", {"text": "test"})
    assert res == {"status": "ok"}
    assert opened_urls == [
        "http://127.0.0.1:12345/overlay",
        "http://127.0.0.1:12345/furigana",
    ]
