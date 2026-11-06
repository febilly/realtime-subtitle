"""Validated capture settings and opt-in persistence across app launches."""
import json

import config
import local_store
import window_audio

STORE_KEY = "audioCapturePreferences.v1"


def defaults():
    return dict(desktop_enabled=True, microphone_enabled=False, desktop_mode="output",
                output_device_id=str(config.OUTPUT_DEVICE_ID or ""),
                microphone_device_id=str(config.MICROPHONE_DEVICE_ID or ""),
                window=None, remember=False)


def source(settings):
    if settings["desktop_enabled"]:
        return "mix" if settings["microphone_enabled"] else "system"
    return "microphone"


def normalize(value, *, validate_window=True):
    if not isinstance(value, dict):
        raise ValueError("Invalid audio capture settings")
    result = defaults()
    for key in ("desktop_enabled", "microphone_enabled", "remember"):
        if not isinstance(value.get(key), bool):
            raise ValueError(f"'{key}' must be a boolean")
        result[key] = value[key]
    if not result["desktop_enabled"] and not result["microphone_enabled"]:
        raise ValueError("At least one audio source must be enabled")
    mode = value.get("desktop_mode")
    if mode not in ("output", "window"):
        raise ValueError("Invalid desktop capture mode")
    result["desktop_mode"] = mode
    for key in ("output_device_id", "microphone_device_id"):
        if not isinstance(value.get(key, ""), str) or len(value.get(key, "")) > 4096:
            raise ValueError(f"Invalid {key}")
        result[key] = value.get(key, "").strip()
    target = value.get("window")
    if isinstance(target, dict):
        result["window"] = {key: target.get(key) for key in ("pid", "hwnd", "created", "executable", "title")}
    if result["desktop_enabled"] and mode == "window" and validate_window:
        if not window_audio.supported():
            raise ValueError("Window audio capture requires Windows build 20348 or later")
        result["window"] = window_audio.resolve_window(target)
        if result["window"] is None:
            raise ValueError("Selected audio window is unavailable")
    return result


def load():
    try:
        raw = local_store.load().get(STORE_KEY)
        saved = normalize(json.loads(raw), validate_window=False) if raw else defaults()
        if not saved["remember"]:
            return defaults()
        if saved["desktop_mode"] == "window":
            # Persist executable/title, never trust a PID from a previous boot.
            saved["window"] = window_audio.resolve_window(saved["window"], restore=True)
        return saved
    except (OSError, ValueError, TypeError):
        return defaults()


def save(settings):
    if settings["remember"]:
        local_store.merge({STORE_KEY: json.dumps(settings, ensure_ascii=False)})
    else:
        local_store.merge(removals=[STORE_KEY])
