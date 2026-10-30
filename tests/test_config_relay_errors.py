import pytest

import config
from relay_errors import RelayConnectionRequestError


def test_relay_connect_info_preserves_http_status(monkeypatch):
    class Response:
        status_code = 402
        text = '{"detail":"Insufficient credits"}'
        reason = "Payment Required"

    monkeypatch.setattr(config, "SUBTITLE_SERVER_URL", "https://relay.example")
    monkeypatch.setattr(config, "RELAY_TOKEN", "ss_test")
    monkeypatch.setattr(config.requests, "get", lambda *args, **kwargs: Response())

    with pytest.raises(RelayConnectionRequestError) as caught:
        config.relay_connect_info("soniox", model="stt-rt-v5")

    assert caught.value.status_code == 402
    assert caught.value.detail == '{"detail":"Insufficient credits"}'


def test_relay_mode_prefers_server_context_bounds(monkeypatch):
    monkeypatch.setattr(config, "RELAY_MODE", True)
    monkeypatch.setattr(config, "LLM_REFINE_CONTEXT_MIN_COUNT", 0)
    monkeypatch.setattr(config, "LLM_REFINE_CONTEXT_MAX_COUNT", 0)
    monkeypatch.setattr(config, "HOSTED_LLM_CONTEXT_MIN", 10)
    monkeypatch.setattr(config, "HOSTED_LLM_CONTEXT_MAX", 30)

    assert config.llm_context_bounds() == (10, 30)


def _stub_connect(monkeypatch, body):
    calls = []

    class Response:
        status_code = 200
        text = ""
        reason = "OK"

        def json(self):
            return body

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(config, "SUBTITLE_SERVER_URL", "https://relay.example")
    monkeypatch.setattr(config, "RELAY_TOKEN", "ss_test")
    monkeypatch.setattr(config.requests, "get", fake_get)
    config.take_relay_notices()
    return calls


def test_non_soniox_connect_opts_into_notices_and_queues_them_once(monkeypatch):
    notice = {"id": "soniox_quota", "action": {"type": "switch_provider", "provider": "soniox"}}
    calls = _stub_connect(monkeypatch, {"url": "wss://relay/x", "notices": [notice, {"id": "unknown"}, "junk"]})

    config.relay_connect_info("gemini", model="models/gemini")

    assert "notices=soniox_quota" in calls[0]
    assert config.take_relay_notices() == [notice]
    assert config.take_relay_notices() == []


def test_soniox_connect_does_not_ask_for_the_soniox_reminder(monkeypatch):
    calls = _stub_connect(monkeypatch, {"url": "wss://relay/x"})

    config.relay_connect_info("soniox", model="stt-rt-v5")

    assert "notices=" not in calls[0]
    assert config.take_relay_notices() == []
