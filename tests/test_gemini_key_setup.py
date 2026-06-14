from gemini_key_setup import validate_gemini_api_key


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
