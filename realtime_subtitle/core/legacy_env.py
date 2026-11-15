"""Startup warnings for environment variables and CLI flags that no longer work.

Old names are not honoured anywhere else in the program; this module only tells
the user what to rename. Add an entry here instead of keeping an alias in config.
"""

from __future__ import annotations

import os
from typing import Iterable, Mapping

_SLEEP_SUFFIXES = (
    "IDLE_SECONDS",
    "PRE_ROLL_SECONDS",
    "SPEECH_GRACE_SECONDS",
    "SPEECH_WINDOW_SECONDS",
    "WAKE_SPEECH_SECONDS",
    "WAKE_SPEECH_WINDOW_SECONDS",
    "VAD_THRESHOLD",
    "WAKE_VAD_THRESHOLD",
)


def _build_env_table() -> dict[str, str]:
    table: dict[str, str] = {}
    for provider in ("SONIOX", "GEMINI"):
        for suffix in _SLEEP_SUFFIXES:
            table[f"{provider}_SLEEP_{suffix}"] = f"use SLEEP_{suffix}"
        table[f"{provider}_ROLLOVER_VAD_THRESHOLD"] = "use ROLLOVER_VAD_THRESHOLD"
    table["LLM_REFINE_DEFAULT_ENABLED"] = "use LLM_REFINE_DEFAULT_MODE (off | refine | translate)"
    table["FUNASR_LOCAL_MODELS_DIR"] = (
        "no longer used; local models live in local_models "
        "(or REALTIME_SUBTITLE_LOCAL_MODELS_DIR)"
    )
    return table


# Old environment variable -> what to do instead.
LEGACY_ENV: dict[str, str] = _build_env_table()

# Old command-line flag -> what to do instead.
LEGACY_CLI_FLAGS: dict[str, str] = {
    f"--{provider}-sleep-{name}": f"use --sleep-{name}"
    for provider in ("soniox", "gemini")
    for name in ("idle-seconds", "pre-roll-seconds", "speech-grace-seconds")
}


def find_legacy_settings(
    environ: Mapping[str, str] | None = None,
    argv: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """Return (old name, advice) for every legacy env var or CLI flag in use."""
    environ = os.environ if environ is None else environ
    found = [(name, advice) for name, advice in LEGACY_ENV.items() if name in environ]
    for arg in argv:
        flag = str(arg).split("=", 1)[0]
        if flag in LEGACY_CLI_FLAGS:
            found.append((flag, LEGACY_CLI_FLAGS[flag]))
    return found


def warn_legacy_settings(argv: Iterable[str] = ()) -> list[tuple[str, str]]:
    """Print one console line per legacy setting (the .env file is already loaded)."""
    found = find_legacy_settings(argv=argv)
    for name, advice in found:
        print(f"⚠️  {name} is no longer supported and is ignored: {advice}")
    return found
