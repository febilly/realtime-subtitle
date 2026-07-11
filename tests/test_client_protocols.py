import sys
from unittest.mock import MagicMock

import pytest
import requests

import soniox_client


@pytest.fixture(autouse=True)
def expose_real_config(monkeypatch):
    # test_osc_manager installs a narrow config stub during collection.
    monkeypatch.setitem(sys.modules, "config", soniox_client.config)


def test_temp_key_headers_are_optional(monkeypatch):
    monkeypatch.delenv("SONIOX_TEMP_KEY_HEADERS", raising=False)
    assert soniox_client._get_temp_key_request_headers() is None


def test_temp_key_headers_are_parsed_and_blank_entries_removed(monkeypatch):
    monkeypatch.setenv(
        "SONIOX_TEMP_KEY_HEADERS",
        '{" Authorization ": " Bearer value ", "empty": "", "": "ignored", "number": 3}',
    )
    assert soniox_client._get_temp_key_request_headers() == {
        "Authorization": "Bearer value",
        "number": "3",
    }


@pytest.mark.parametrize("value", ["[]", '"text"', "1"])
def test_temp_key_headers_require_json_object(monkeypatch, value):
    monkeypatch.setenv("SONIOX_TEMP_KEY_HEADERS", value)
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        soniox_client._get_temp_key_request_headers()


def test_temp_key_headers_reject_invalid_json(monkeypatch):
    monkeypatch.setenv("SONIOX_TEMP_KEY_HEADERS", "{")
    with pytest.raises(RuntimeError, match="Invalid SONIOX_TEMP_KEY_HEADERS JSON"):
        soniox_client._get_temp_key_request_headers()


def test_get_api_key_prefers_environment(monkeypatch):
    monkeypatch.setenv("SONIOX_API_KEY", "own-key")
    get = MagicMock()
    monkeypatch.setattr(soniox_client.requests, "get", get)
    assert soniox_client.get_api_key() == "own-key"
    get.assert_not_called()


def test_get_api_key_fetches_temporary_key_with_headers(monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    monkeypatch.setenv("SONIOX_TEMP_KEY_HEADERS", '{"X-Token":"abc"}')
    response = MagicMock()
    response.text = " temp-key\n"
    get = MagicMock(return_value=response)
    monkeypatch.setattr(soniox_client.requests, "get", get)

    assert soniox_client.get_api_key() == "temp-key"
    get.assert_called_once_with(
        soniox_client.SONIOX_TEMP_KEY_URL,
        timeout=10,
        headers={"X-Token": "abc"},
    )
    response.raise_for_status.assert_called_once_with()


def test_get_api_key_wraps_http_failure(monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    monkeypatch.delenv("SONIOX_TEMP_KEY_HEADERS", raising=False)
    monkeypatch.setattr(
        soniox_client.requests,
        "get",
        MagicMock(side_effect=requests.RequestException("offline")),
    )
    with pytest.raises(RuntimeError, match="Failed to fetch temporary API Key: offline"):
        soniox_client.get_api_key()


def test_get_api_key_rejects_empty_temporary_response(monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    response = MagicMock()
    response.text = "  "
    monkeypatch.setattr(soniox_client.requests, "get", MagicMock(return_value=response))
    with pytest.raises(RuntimeError, match="Temporary key response is empty"):
        soniox_client.get_api_key()


@pytest.mark.parametrize(
    ("audio_format", "expected"),
    [
        ("auto", {"audio_format": "auto"}),
        (
            "pcm_s16le",
            {"audio_format": "pcm_s16le", "sample_rate": 16000, "num_channels": 1},
        ),
    ],
)
def test_get_config_audio_formats(monkeypatch, audio_format, expected):
    monkeypatch.setattr(soniox_client.config, "ENABLE_SPEAKER_DIARIZATION", False)
    result = soniox_client.get_config("key", audio_format, "none")
    for key, value in expected.items():
        assert result[key] == value
    assert result["enable_speaker_diarization"] is False
    assert "translation" not in result


def test_get_config_one_way_normalizes_override(monkeypatch):
    monkeypatch.setattr(soniox_client.config, "TRANSLATION_TARGET_LANG", "ja")
    result = soniox_client.get_config(
        "key", "auto", "one_way", translation_target_lang="ZH-CN"
    )
    assert result["translation"] == {"type": "one_way", "target_language": "zh"}


def test_get_config_one_way_uses_default_and_rejects_unsupported(monkeypatch):
    monkeypatch.setattr(soniox_client.config, "TRANSLATION_TARGET_LANG", "ko")
    assert soniox_client.get_config("key", "auto", "one_way")["translation"][
        "target_language"
    ] == "ko"
    with pytest.raises(ValueError, match="Unsupported translation target language"):
        soniox_client.get_config(
            "key", "auto", "one_way", translation_target_lang="unsupported-language"
        )


def test_get_config_two_way_normalizes_languages(monkeypatch):
    result = soniox_client.get_config(
        "key", "auto", "two_way", target_lang_1="EN-us", target_lang_2="ZH-CN"
    )
    assert result["translation"] == {
        "type": "two_way",
        "language_a": "en",
        "language_b": "zh",
    }


@pytest.mark.parametrize(
    ("audio_format", "translation", "message"),
    [
        ("mp3", "none", "Unsupported audio_format"),
        ("auto", "three_way", "Unsupported translation"),
    ],
)
def test_get_config_rejects_unsupported_modes(audio_format, translation, message):
    with pytest.raises(ValueError, match=message):
        soniox_client.get_config("key", audio_format, translation)
