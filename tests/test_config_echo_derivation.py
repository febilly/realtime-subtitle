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
    # Drop whatever is currently registered (possibly another test's mock) and
    # import a fresh, real config under the patched environment.
    sys.modules.pop("config", None)
    import config
    return importlib.reload(config)


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
