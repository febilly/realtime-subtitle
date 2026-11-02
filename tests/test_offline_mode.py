import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.fixture
def server(monkeypatch):
    from local_session import config
    with patch.dict(sys.modules, {"config": config}):
        previous = sys.modules.pop("web_server", None)
        import web_server as module
        monkeypatch.setattr(module, "LOCK_MANUAL_CONTROLS", False)
        monkeypatch.setattr(module, "web", SimpleNamespace(json_response=lambda data, status=200: (data, status)))
        monkeypatch.setattr(config, "set_local_inference_config", Mock())
        instance = module.WebServer.__new__(module.WebServer)
        instance.provider_manager = SimpleNamespace(
            provider="local", mode="direct", relay_token="saved-token", boot_id="boot",
            setup_required=False, translation_mode="none", ui_translation_mode="accurate",
            apply_provider=AsyncMock(return_value={"error": None, "started": False}),
        )
        instance._validate_relay_token = AsyncMock(side_effect=AssertionError("offline attempted hosted access"))
        yield instance, module
        sys.modules.pop("web_server", None)
        if previous is not None:
            sys.modules["web_server"] = previous


def models(asr=True, translation=True):
    return {"asr": {"qwen3-asr": {"ready": asr}}, "translation": {"hymt2": {"ready": translation}}}


def test_simulated_hosted_outage_returns_connection_error_without_network(server, monkeypatch):
    instance, module = server
    monkeypatch.setattr(module.config, "RELAY_AVAILABLE", True)
    monkeypatch.setattr(module.config, "SIMULATE_HOSTED_OFFLINE", True)
    instance._get_http_session = AsyncMock(side_effect=AssertionError("simulation reached network"))
    data = asyncio.run(instance._server_request("GET", "/public/settings"))
    assert data[0] == 502
    assert "SIMULATE_HOSTED_OFFLINE" in data[1]["detail"]
    instance._get_http_session.assert_not_called()


@pytest.mark.parametrize("provider", ["soniox", "gemini", "local"])
def test_simulated_hosted_outage_prevents_relay_ticket_requests(server, monkeypatch, provider):
    _, module = server
    monkeypatch.setattr(module.config, "SUBTITLE_SERVER_URL", "https://relay.example")
    monkeypatch.setattr(module.config, "RELAY_TOKEN", "saved-token")
    monkeypatch.setattr(module.config, "SIMULATE_HOSTED_OFFLINE", True)
    get = Mock(side_effect=AssertionError("simulation reached network"))
    monkeypatch.setattr(module.config.requests, "get", get)
    with pytest.raises(RuntimeError, match="SIMULATE_HOSTED_OFFLINE"):
        module.config.relay_connect_info(provider)
    get.assert_not_called()


@pytest.mark.parametrize("translation,expected", [(True, "fast"), (False, "off")])
def test_offline_setup_uses_downloaded_models_without_hosted_access(server, monkeypatch, translation, expected):
    instance, module = server
    monkeypatch.setattr("local_inference.model_manager.get_all_local_models_status", lambda: models(translation=translation))
    request = SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={
        "provider": "local", "offline_mode": True, "mode": "relay",
        "local_config": {"backend": "remote"},
    }))
    _, status = asyncio.run(instance.setup_handler(request))
    assert status == 200 and instance.offline_mode
    assert instance.provider_manager.ui_translation_mode == expected
    assert instance.provider_manager.translation_mode == "one_way"
    assert instance.provider_manager.apply_provider.call_args.kwargs["mode"] == "direct"
    assert instance.provider_manager.apply_provider.call_args.kwargs["start_paused"]
    assert module.config.set_local_inference_config.call_args.kwargs["backend"] == "local"
    instance._validate_relay_token.assert_not_called()
    assert not hasattr(module.WebServer.__new__(module.WebServer), "offline_mode")


@pytest.mark.parametrize("paused", [True, False])
def test_saving_offline_settings_preserves_manual_start_state(server, paused):
    instance, _ = server
    instance.offline_mode = True
    instance.session = SimpleNamespace(is_paused=paused)
    request = SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={"provider": "local", "mode": "direct"}))
    _, status = asyncio.run(instance.setup_handler(request))
    assert status == 200
    assert instance.provider_manager.apply_provider.call_args.kwargs["start_paused"] is paused


def test_automatic_language_sync_does_not_start_paused_local_models(server, monkeypatch):
    instance, module = server
    monkeypatch.setattr(module.config, "TRANSLATION_PROVIDER", "local")
    instance.session = Mock(is_paused=True)
    instance.session.set_translation_target_lang.return_value = (True, "ok")
    instance.session.get_target_langs.return_value = ("en", "zh")
    instance.get_api_key = lambda: "local-on-device"
    instance._session_lifecycle_lock = asyncio.Lock
    request = SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={"auto": True, "translation_mode": "one_way", "target_lang": "zh"}))
    data, status = asyncio.run(instance.restart_handler(request))
    assert status == 200 and data["paused"] is True
    instance.session.stop.assert_not_called()
    instance.session.start.assert_not_called()
    assert instance.session.translation == "one_way"


def test_offline_setup_rejects_missing_asr_and_cloud_provider(server, monkeypatch):
    instance, _ = server
    monkeypatch.setattr("local_inference.model_manager.get_all_local_models_status", lambda: models(asr=False))
    request = SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={"provider": "local", "offline_mode": True}))
    _, status = asyncio.run(instance.setup_handler(request))
    assert status == 400
    instance.provider_manager.apply_provider.assert_not_called()
    instance.offline_mode = True
    request.json.return_value = {"provider": "soniox"}
    _, status = asyncio.run(instance.setup_handler(request))
    assert status == 409
    instance.provider_manager.apply_provider.assert_not_called()


def test_offline_translation_rejects_llm_modes(server):
    instance, _ = server
    instance.offline_mode = True
    instance.session = Mock()
    request = SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={"mode": "accurate"}))
    _, status = asyncio.run(instance.translation_mode_set_handler(request))
    assert status == 400
    instance.session.set_translation_mode.assert_not_called()


def test_offline_fast_translation_requires_a_downloaded_model(server, monkeypatch):
    instance, _ = server
    instance.offline_mode = True
    instance.session = Mock()
    monkeypatch.setattr("local_inference.model_manager.get_all_local_models_status", lambda: models(translation=False))
    request = SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={"mode": "fast"}))
    _, status = asyncio.run(instance.translation_mode_set_handler(request))
    assert status == 400
    instance.session.set_translation_mode.assert_not_called()


def test_startup_validation_failure_keeps_token_and_offers_local_availability(server, monkeypatch):
    instance, module = server
    monkeypatch.setattr(module.config, "RELAY_AVAILABLE", True)
    monkeypatch.setattr("local_inference.model_manager.get_all_local_models_status", lambda: models())
    instance._validate_relay_token = AsyncMock(return_value=(False, "unreachable", None, False))
    request = SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={
        "provider": "soniox", "mode": "relay", "startup": True,
    }))
    data, status = asyncio.run(instance.setup_handler(request))
    assert status == 503 and data["hosted_unreachable"]
    assert data["offline_models"] == {"asr": True, "translation": True}
    assert instance.provider_manager.relay_token == "saved-token"
    instance.provider_manager.apply_provider.assert_not_called()


@pytest.mark.parametrize("offline", [False, True])
def test_ui_config_reports_cached_models_and_skips_hosted_calls_offline(server, monkeypatch, offline):
    instance, module = server
    monkeypatch.setattr(module.config, "RELAY_AVAILABLE", True)
    monkeypatch.setattr(module.config, "RELAY_MODE", False)
    monkeypatch.setattr("local_inference.model_manager.get_all_local_models_status", lambda: models(translation=False))
    instance.offline_mode = offline
    instance.session = Mock()
    instance.check_custom_font_exists = lambda: False
    instance._server_request = AsyncMock(return_value=(503, {}))
    instance.provider_manager.key_source = lambda: "env"
    instance.provider_manager.env_key_present = lambda provider: False
    instance.provider_manager.target_lang_1 = "en"
    instance.provider_manager.target_lang_2 = "zh"
    data, status = asyncio.run(instance.ui_config_handler(SimpleNamespace()))
    assert status == 200 and data["offline_mode"] is offline
    assert data["offline_models"] == {"asr": True, "translation": False}
    if offline:
        instance._server_request.assert_not_called()
        assert data["llm_refine_available"] is False
    else:
        assert data["hosted_reachable"] is False
