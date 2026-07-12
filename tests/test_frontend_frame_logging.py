import json
from unittest.mock import MagicMock

import pytest

from web_server import WebServer


@pytest.mark.asyncio
async def test_frontend_frame_log_records_every_broadcast(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRONTEND_FRAME_LOG", "1")
    server = WebServer(None, MagicMock())

    await server.broadcast_to_clients({"type": "clear", "preserve_existing": False})
    server._frontend_frame_log.close()

    files = list((tmp_path / "logs" / "frontend-frames").glob("frames_*.jsonl"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == {
        "type": "clear",
        "preserve_existing": False,
    }
