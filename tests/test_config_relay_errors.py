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
