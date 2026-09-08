import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.fixture
def web_module():
    # Legacy suites leave a minimal config stub in sys.modules at collection.
    # Import the actual handler against the retained real session config.
    from local_session import config
    # Restore the module cache too: later legacy suites intentionally import
    # WebServer against their own aiohttp/config stubs.
    with patch.dict(sys.modules, {"config": config}):
        sys.modules.pop("web_server", None)
        import web_server
        yield web_server


@pytest.mark.parametrize("payload", [None, [], {"server_url": "file:///private"}, {"server_url": ""}])
def test_probe_rejects_invalid_payload_before_network(monkeypatch, payload, web_module):
    module = web_module
    probe = Mock(side_effect=AssertionError("invalid endpoint reached network"))
    monkeypatch.setattr("local_inference.remote_client.probe_remote_server", probe)
    monkeypatch.setattr(module, "LOCK_MANUAL_CONTROLS", False)
    monkeypatch.setattr(module, "web", SimpleNamespace(json_response=lambda data, status=200: (data, status)))
    request = SimpleNamespace(json=AsyncMock(return_value=payload))
    instance = SimpleNamespace(_is_loopback_request=lambda request: True)
    _, status = asyncio.run(module.WebServer.local_inference_probe_handler(instance, request))
    assert status == 400
    probe.assert_not_called()


def test_probe_uses_bounded_ws_health_and_normalizes_required_model_status(monkeypatch, web_module):
    module = web_module
    probe = Mock(return_value={"ready": False, "models": {"qwen3-asr": {"ready": True}, "hymt2": {"ready": True}}})
    monkeypatch.setattr("local_inference.remote_client.probe_remote_server", probe)
    monkeypatch.setattr(module, "LOCK_MANUAL_CONTROLS", False)
    monkeypatch.setattr(module, "web", SimpleNamespace(json_response=lambda data, status=200: (data, status)))
    request = SimpleNamespace(json=AsyncMock(return_value={"server_url": "http://127.0.0.1:18775/"}))
    instance = SimpleNamespace(_is_loopback_request=lambda request: True)
    data, status = asyncio.run(module.WebServer.local_inference_probe_handler(instance, request))
    assert status == 200 and data["remote_status"]["ready"]
    probe.assert_called_once_with("ws://127.0.0.1:18775", timeout=5)
    instance._is_loopback_request = lambda request: False
    _, status = asyncio.run(module.WebServer.local_inference_probe_handler(instance, request))
    assert status == 403 and probe.call_count == 1


def test_bad_remote_config_does_not_partially_switch_backend(monkeypatch):
    from local_session import config
    monkeypatch.setattr(config, "LOCAL_INFERENCE_BACKEND", "local")
    monkeypatch.setattr(config, "LOCAL_INFERENCE_SERVER_URL", "ws://127.0.0.1:18775")
    with pytest.raises(ValueError):
        config.set_local_inference_config(backend="remote", server_url="file:///private")
    assert config.LOCAL_INFERENCE_BACKEND == "local"
    with pytest.raises(ValueError):
        config.set_local_inference_config(backend="remote", remote_timeout_seconds=float("nan"))
    assert config.LOCAL_INFERENCE_BACKEND == "local"
