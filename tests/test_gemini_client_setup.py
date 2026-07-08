import importlib
import sys

import pytest


@pytest.fixture
def gemini_client():
    """Provide a gemini_client bound to the real config.

    Other test modules (test_ipc_server) install a mock config into
    sys.modules at import time, so we deliberately avoid importing config or
    gemini_client at module top (that would fail during collection if this file
    is imported after the mock is installed). Instead, ensure the real config is
    active, import a fresh gemini_client bound to it, and restore the previous
    config module afterward.
    """
    original_config = sys.modules.get("config")
    sys.modules.pop("config", None)
    import config  # fresh real config
    importlib.reload(config)
    import gemini_client as module
    importlib.reload(module)
    try:
        yield module
    finally:
        if original_config is not None:
            sys.modules["config"] = original_config
        else:
            sys.modules.pop("config", None)


def _translation_config(message):
    return message["setup"]["generationConfig"]["translationConfig"]


def _gemini_lang(code):
    from config import to_gemini_language_code
    return to_gemini_language_code(code)


def test_one_way_uses_translation_target_lang_param(gemini_client):
    config = _translation_config(gemini_client.get_setup_message("one_way", "zh-hans"))
    assert config["targetLanguageCode"] == _gemini_lang("zh-hans")


def test_two_way_falls_back_to_target_lang_1(gemini_client, monkeypatch):
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "ja")
    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    monkeypatch.setattr(gemini_client, "_warned_two_way", False)

    # The translation_target_lang param is ignored for two_way; TARGET_LANG_1 wins.
    config = _translation_config(gemini_client.get_setup_message("two_way", "zh-hans"))
    assert config["targetLanguageCode"] == _gemini_lang("ja")


def test_two_way_echo_follows_derived_flag(gemini_client, monkeypatch):
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "en")
    monkeypatch.setattr(gemini_client, "_warned_two_way", True)  # silence warning

    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    assert _translation_config(gemini_client.get_setup_message("two_way"))["echoTargetLanguage"] is True

    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", False)
    assert _translation_config(gemini_client.get_setup_message("two_way"))["echoTargetLanguage"] is False


def test_two_way_matches_one_way_after_warning(gemini_client, monkeypatch):
    # Apart from the fixed TARGET_LANG_1 target and the one-time warning, two_way
    # must produce the same translationConfig as one_way into the same language.
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "en")
    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    monkeypatch.setattr(gemini_client, "_warned_two_way", True)

    two_way = _translation_config(gemini_client.get_setup_message("two_way"))
    one_way = _translation_config(gemini_client.get_setup_message("one_way", "en"))
    assert two_way == one_way


def test_two_way_warns_once(gemini_client, monkeypatch, capsys):
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "en")
    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    monkeypatch.setattr(gemini_client, "_warned_two_way", False)

    gemini_client.get_setup_message("two_way")
    gemini_client.get_setup_message("two_way")

    out = capsys.readouterr().out
    assert out.count("does not support two-way translation") == 1


def test_relay_connect_live_uses_server_minted_ws_url(gemini_client, monkeypatch):
    import config

    connect_calls = []
    sent_payloads = []
    relay_calls = []

    class FakeWs:
        def send(self, payload):
            sent_payloads.append(payload)

        def recv(self, timeout=None):
            return '{"setupComplete": {}}'

        def close(self):
            return None

    def relay_connect_info(provider=None, model=None, translation=None):
        relay_calls.append({
            "provider": provider,
            "model": model,
            "translation": translation,
        })
        return {
            "url": "wss://relay-2.example.invalid/?ticket=test",
            "headers": {"X-Test-Relay": "ok"},
        }

    def sync_connect(url, **kwargs):
        connect_calls.append((url, kwargs))
        return FakeWs()

    monkeypatch.setattr(config, "RELAY_MODE", True)
    monkeypatch.setattr(config, "relay_connect_info", relay_connect_info)
    monkeypatch.setattr(gemini_client, "sync_connect", sync_connect)

    stream = gemini_client.connect_live("local-key", "one_way", "en")
    stream.close()

    assert relay_calls == [{
        "provider": "gemini",
        "model": f"models/{gemini_client.GEMINI_MODEL}",
        "translation": None,
    }]
    assert connect_calls == [(
        "wss://relay-2.example.invalid/?ticket=test",
        {"max_size": None, "additional_headers": {"X-Test-Relay": "ok"}},
    )]
    assert "Authorization" not in connect_calls[0][1].get("additional_headers", {})
    assert sent_payloads
