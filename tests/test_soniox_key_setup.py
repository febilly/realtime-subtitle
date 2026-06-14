import json

from soniox_key_setup import validate_soniox_api_key


class _AcceptedWebSocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def send(self, payload):
        self.payload = payload

    def recv(self, timeout=None):
        raise TimeoutError()


class _RejectedWebSocket(_AcceptedWebSocket):
    def recv(self, timeout=None):
        return json.dumps({"error_code": "unauthorized", "error_message": "bad key"})


def test_validate_soniox_api_key_accepts_quiet_open_stream():
    calls = []

    def connect(url):
        calls.append(url)
        return _AcceptedWebSocket()

    ok, error = validate_soniox_api_key("test-key", "wss://example.invalid", connect_func=connect)

    assert ok is True
    assert error is None
    assert calls == ["wss://example.invalid"]


def test_validate_soniox_api_key_rejects_error_response():
    ok, error = validate_soniox_api_key(
        "bad-key",
        "wss://example.invalid",
        connect_func=lambda url: _RejectedWebSocket(),
    )

    assert ok is False
    assert error == "bad key"
