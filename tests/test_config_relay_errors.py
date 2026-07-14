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
