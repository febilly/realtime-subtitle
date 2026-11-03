from legacy_env import LEGACY_ENV, find_legacy_settings, warn_legacy_settings


def test_reports_old_env_names_with_the_replacement():
    found = dict(find_legacy_settings(
        {"GEMINI_SLEEP_IDLE_SECONDS": "5", "SLEEP_IDLE_SECONDS": "5", "LLM_REFINE_DEFAULT_ENABLED": "1"},
    ))

    assert found == {
        "GEMINI_SLEEP_IDLE_SECONDS": "use SLEEP_IDLE_SECONDS",
        "LLM_REFINE_DEFAULT_ENABLED": "use LLM_REFINE_DEFAULT_MODE (off | refine | translate)",
    }


def test_sleep_on_off_switches_are_still_supported():
    assert "SONIOX_SLEEP_ON_SILENCE" not in LEGACY_ENV
    assert "GEMINI_SLEEP_ON_SILENCE" not in LEGACY_ENV


def test_reports_removed_cli_flags_with_or_without_a_value():
    found = find_legacy_settings(
        {}, ["--soniox-sleep-idle-seconds=10", "--gemini-sleep-pre-roll-seconds", "0.3", "--sleep-idle-seconds", "1"],
    )

    assert found == [
        ("--soniox-sleep-idle-seconds", "use --sleep-idle-seconds"),
        ("--gemini-sleep-pre-roll-seconds", "use --sleep-pre-roll-seconds"),
    ]


def test_warning_goes_to_the_console(monkeypatch, capsys):
    monkeypatch.setenv("FUNASR_LOCAL_MODELS_DIR", "D:/old")

    warn_legacy_settings([])

    assert "FUNASR_LOCAL_MODELS_DIR is no longer supported" in capsys.readouterr().out
