import importlib
import sys

import pytest


@pytest.fixture
def restore_config_module():
    """Reloading config replaces sys.modules['config']; other test modules
    (e.g. test_ipc_server) install their own mock config there, so snapshot and
    restore it to avoid cross-test pollution."""
    original = sys.modules.get("config")
    yield
    if original is not None:
        sys.modules["config"] = original
    else:
        sys.modules.pop("config", None)


def _reload_config(monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv("OSC_SEND_TEXT_MODE", raising=False)
    else:
        monkeypatch.setenv("OSC_SEND_TEXT_MODE", mode)
    # Neutralize .env loading so a developer's local .env (e.g. SLEEP_IDLE_SECONDS)
    # cannot leak into these reload-based config assertions.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    # Drop whatever is currently registered (possibly another test's mock) and
    # import a fresh, real config under the patched environment.
    sys.modules.pop("config", None)
    import config
    return importlib.reload(config)


def _clear_sleep_env(monkeypatch):
    for prefix in ("", "SONIOX_", "GEMINI_"):
        for suffix in (
            "SLEEP_ON_SILENCE",
            "SLEEP_IDLE_SECONDS",
            "SLEEP_PRE_ROLL_SECONDS",
            "SLEEP_SPEECH_WINDOW_SECONDS",
            "SLEEP_SPEECH_GRACE_SECONDS",
            "SLEEP_VAD_THRESHOLD",
        ):
            monkeypatch.delenv(f"{prefix}{suffix}", raising=False)


@pytest.mark.parametrize(
    "mode, expected_echo",
    [
        (None, True),              # default mode is "smart"
        ("smart", True),
        ("source_only", True),
        ("translation_only", False),
        ("bogus", True),           # invalid -> falls back to "smart"
    ],
)
def test_echo_target_language_derived_from_osc_mode(
    monkeypatch, restore_config_module, mode, expected_echo
):
    config = _reload_config(monkeypatch, mode)
    assert config.GEMINI_ECHO_TARGET_LANGUAGE is expected_echo


def test_osc_mode_drives_echo_and_is_not_a_standalone_env(
    monkeypatch, restore_config_module
):
    # Setting the old standalone env must not override the derived value.
    monkeypatch.setenv("GEMINI_ECHO_TARGET_LANGUAGE", "false")
    config = _reload_config(monkeypatch, "smart")
    assert config.GEMINI_ECHO_TARGET_LANGUAGE is True


def test_shared_sleep_tuning_overrides_provider_specific_aliases(
    monkeypatch, restore_config_module
):
    _clear_sleep_env(monkeypatch)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setenv("SLEEP_IDLE_SECONDS", "12")
    monkeypatch.setenv("GEMINI_SLEEP_IDLE_SECONDS", "99")

    config = _reload_config(monkeypatch, "smart")

    assert config.SLEEP_IDLE_SECONDS == 12
    assert config.GEMINI_SLEEP_IDLE_SECONDS == 12
    assert config.SONIOX_SLEEP_IDLE_SECONDS == 12


def test_sleep_tuning_reads_active_provider_legacy_alias(
    monkeypatch, restore_config_module
):
    _clear_sleep_env(monkeypatch)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_SLEEP_IDLE_SECONDS", "44")
    monkeypatch.setenv("SONIOX_SLEEP_IDLE_SECONDS", "55")

    config = _reload_config(monkeypatch, "smart")

    assert config.SLEEP_IDLE_SECONDS == 44
    assert config.GEMINI_SLEEP_IDLE_SECONDS == 44
    assert config.SONIOX_SLEEP_IDLE_SECONDS == 44
