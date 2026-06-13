import json
from pathlib import Path

import gemini_key_setup
from gemini_key_setup import save_gemini_api_key, validate_gemini_api_key


def test_save_gemini_api_key_appends_to_existing_env(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text('OTHER="value"\n', encoding="utf-8")

    save_gemini_api_key("test-key", env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert 'OTHER="value"' in content
    assert 'GEMINI_API_KEY="test-key"' in content
    assert content.endswith("\n")


def test_save_gemini_api_key_creates_file(tmp_path):
    env_path = tmp_path / ".env"

    save_gemini_api_key('key"with"quotes', env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert content == 'GEMINI_API_KEY="key\\"with\\"quotes"\n'


def test_validate_gemini_api_key_accepts_valid_key():
    def fake_validate(api_key):
        assert api_key == "good-key"
        return True, None

    is_valid, error = validate_gemini_api_key("good-key", validate_func=fake_validate)
    assert is_valid is True
    assert error is None


def test_validate_gemini_api_key_rejects_invalid_key():
    def fake_validate(api_key):
        return False, "HTTP 400: API key not valid"

    is_valid, error = validate_gemini_api_key("bad-key", validate_func=fake_validate)
    assert is_valid is False
    assert "API key not valid" in error


def test_ensure_gemini_key_available_skips_when_key_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "existing")
    # Should return without prompting.
    gemini_key_setup.ensure_gemini_key_available()


def test_ensure_gemini_key_available_skips_when_temp_url_present(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_TEMP_KEY_URL", "https://example.com/key")
    gemini_key_setup.ensure_gemini_key_available()
