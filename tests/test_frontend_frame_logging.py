import json
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def web_server_class():
    previous_config = sys.modules.pop("config", None)
    previous_web_server = sys.modules.pop("web_server", None)
    try:
        # Several legacy unit modules install a minimal top-level `config` stub.
        # This integration fixture needs the real module regardless of test order.
        importlib.import_module("config")
        WebServer = importlib.import_module("web_server").WebServer
        yield WebServer
    finally:
        sys.modules.pop("web_server", None)
        sys.modules.pop("config", None)
        if previous_web_server is not None:
            sys.modules["web_server"] = previous_web_server
        if previous_config is not None:
            sys.modules["config"] = previous_config


@pytest.mark.asyncio
async def test_frontend_frame_log_records_every_broadcast(monkeypatch, tmp_path, web_server_class):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRONTEND_FRAME_LOG", "1")
    server = web_server_class(None, MagicMock())

    await server.broadcast_to_clients({"type": "clear", "preserve_existing": False})
    server._frontend_frame_log.close()

    files = list((tmp_path / "logs" / "frontend-frames").glob("frames_*.jsonl"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == {
        "type": "clear",
        "preserve_existing": False,
    }


def test_frontend_frame_logs_use_unique_files(monkeypatch, tmp_path, web_server_class):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRONTEND_FRAME_LOG", "1")

    first = web_server_class(None, MagicMock())
    second = web_server_class(None, MagicMock())
    first._frontend_frame_log.close()
    second._frontend_frame_log.close()

    files = list((tmp_path / "logs" / "frontend-frames").glob("frames_*.jsonl"))
    assert len(files) == 2
    assert files[0].name != files[1].name


def test_frontend_frame_log_open_failure_does_not_break_server(monkeypatch, tmp_path, web_server_class):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRONTEND_FRAME_LOG", "1")
    logger = MagicMock()

    def fail_open(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("builtins.open", fail_open)
    server = web_server_class(None, logger)

    assert server._frontend_frame_log is None


@pytest.mark.asyncio
async def test_frontend_frame_log_write_failure_does_not_block_broadcast(monkeypatch, web_server_class):
    monkeypatch.delenv("FRONTEND_FRAME_LOG", raising=False)
    logger = MagicMock()
    server = web_server_class(None, logger)
    client = MagicMock()
    client.send_str = AsyncMock()
    server.websocket_clients.add(client)

    broken_log = MagicMock()
    broken_log.write.side_effect = OSError("disk full")
    server._frontend_frame_log = broken_log

    await server.broadcast_to_clients({"type": "clear"})

    client.send_str.assert_awaited_once_with('{"type": "clear"}')
    broken_log.close.assert_called_once()
    assert server._frontend_frame_log is None
