import json
from pathlib import Path

import soniox_key_setup
from soniox_key_setup import save_soniox_api_key, validate_soniox_api_key


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


def test_save_soniox_api_key_appends_new_line(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER=value", encoding="utf-8")

    save_soniox_api_key('abc"def', env_path)

    content = env_path.read_text(encoding="utf-8")
    assert content == 'OTHER=value\nSONIOX_API_KEY="abc\\"def"\n'


def test_ensure_soniox_key_available_retries_and_saves(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    monkeypatch.delenv("SONIOX_TEMP_KEY_URL", raising=False)

    entered_keys = iter(["bad-key", "good-key"])
    monkeypatch.setattr(soniox_key_setup.getpass, "getpass", lambda prompt: next(entered_keys))

    checked = []

    def validate(api_key):
        checked.append(api_key)
        return (api_key == "good-key", "bad key")

    monkeypatch.setattr(soniox_key_setup, "validate_soniox_api_key", validate)

    soniox_key_setup.ensure_soniox_key_available()

    assert checked == ["bad-key", "good-key"]
    assert soniox_key_setup.os.environ["SONIOX_API_KEY"] == "good-key"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == 'SONIOX_API_KEY="good-key"\n'
