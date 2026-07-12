import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def web_server_class():
    from web_server import WebServer

    yield WebServer
    # Some legacy security tests import this module under patched dependencies.
    # Do not let this integration-style import make those tests order-dependent.
    sys.modules.pop("web_server", None)


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
