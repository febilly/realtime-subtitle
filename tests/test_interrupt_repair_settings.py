import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest


class _Request:
    def __init__(self, payload=None, remote="127.0.0.1"):
        self._payload = payload
        self.remote = remote

    async def json(self):
        return self._payload


@pytest.fixture
def real_web_server_module():
    previous_config = sys.modules.pop("config", None)
    previous_web_server = sys.modules.pop("web_server", None)
    try:
        config = importlib.import_module("config")
        web_server = importlib.import_module("web_server")
        yield config, web_server
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
async def test_interrupt_repair_setting_defaults_on_and_hot_applies(real_web_server_module):
    config, web_server = real_web_server_module
    server = web_server.WebServer(MagicMock(), MagicMock())
    previous = config.SONIOX_INTERRUPT_REPAIR_ENABLED
    try:
        with (
            patch.object(web_server, "LOCK_MANUAL_CONTROLS", False),
            patch.object(config, "TRANSLATION_PROVIDER", "soniox"),
            patch.object(config, "ENABLE_SPEAKER_DIARIZATION", True),
            patch.object(config, "SONIOX_INTERRUPT_REPAIR_ENABLED", True),
        ):
            assert config.SONIOX_INTERRUPT_REPAIR_ENABLED is True
            response = await server.interrupt_repair_set_handler(_Request({"enabled": False}))
            assert response.status == 200
            assert _json(response) == {
                "status": "ok", "supported": True, "enabled": False,
            }
            response = await server.interrupt_repair_get_handler(_Request())
            assert _json(response)["enabled"] is False
    finally:
        config.SONIOX_INTERRUPT_REPAIR_ENABLED = previous


@pytest.mark.asyncio
async def test_interrupt_repair_setting_rejects_invalid_and_remote_requests(real_web_server_module):
    config, web_server = real_web_server_module
    server = web_server.WebServer(MagicMock(), MagicMock())
    with (
        patch.object(web_server, "LOCK_MANUAL_CONTROLS", False),
        patch.object(config, "TRANSLATION_PROVIDER", "soniox"),
        patch.object(config, "ENABLE_SPEAKER_DIARIZATION", True),
    ):
        invalid = await server.interrupt_repair_set_handler(_Request({"enabled": "false"}))
        assert invalid.status == 400
        forbidden = await server.interrupt_repair_set_handler(
            _Request({"enabled": False}, remote="203.0.113.10")
        )
        assert forbidden.status == 403
