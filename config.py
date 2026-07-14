"""
Configuration file - stores all configuration options and constants.
"""
import json
import os
import sys
import locale
import time
import threading
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv


# ======================== Supported languages (per provider) ========================
# Two providers are supported and each accepts a different set of target languages.
# The active set/list is selected by TRANSLATION_PROVIDER (resolved further below).

# Soniox-supported languages (ISO 639-1). Source: docs/supported-languages.mdx
SONIOX_SUPPORTED_LANGUAGE_CODES = {
    "af", "sq", "ar", "az", "eu", "be", "bn", "bs", "bg", "ca",
    "zh", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "gl",
    "de", "el", "gu", "he", "hi", "hu", "id", "it", "ja", "kn",
    "kk", "ko", "lv", "lt", "mk", "ms", "ml", "mr", "no", "fa",
    "pl", "pt", "pa", "ro", "ru", "sr", "sk", "sl", "es", "sw",
    "sv", "tl", "ta", "te", "th", "tr", "uk", "ur", "vi", "cy",
}

# Gemini Live Translation supported languages (normalized to primary subtag).
# Source: https://ai.google.dev/gemini-api/docs/live-api/live-translate
GEMINI_SUPPORTED_LANGUAGE_CODES = {
    "af", "ak", "sq", "am", "ar", "hy", "az", "eu", "be", "bn",
    "bg", "my", "ca", "zh", "hr", "cs", "da", "nl", "en", "et",
    "fil", "fi", "fr", "gl", "ka", "de", "el", "gu", "ha", "he",
    "hi", "hu", "is", "id", "it", "ja", "jv", "kn", "kk", "km",
    "rw", "ko", "lo", "lv", "lt", "mk", "ms", "ml", "mr", "mn",
    "ne", "no", "nb", "fa", "pl", "pt", "pa", "ro", "ru", "sr",
    "sd", "si", "sk", "sl", "es", "su", "sw", "sv", "ta", "te",
    "th", "tr", "uk", "ur", "uz", "vi", "zu",
    # Legacy alias kept for backward compatibility (mapped to "fil" for Gemini)
    "tl",
}

# Ordered language-code lists used to drive the frontend dropdown. The display
# codes can carry meaningful BCP-47 variants (e.g. Gemini splits zh into
# zh-hans/zh-hant); validation still normalizes them to the primary subtag.
SONIOX_LANGUAGE_CODES_ORDERED = [
    "af", "sq", "ar", "az", "eu", "be", "bn", "bs", "bg", "ca",
    "zh", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "gl",
    "de", "el", "gu", "he", "hi", "hu", "id", "it", "ja", "kn",
    "kk", "ko", "lv", "lt", "mk", "ms", "ml", "mr", "no", "fa",
    "pl", "pt", "pa", "ro", "ru", "sr", "sk", "sl", "es", "sw",
    "sv", "tl", "ta", "te", "th", "tr", "uk", "ur", "vi", "cy",
]

GEMINI_LANGUAGE_CODES_ORDERED = [
    "af", "ak", "sq", "am", "ar", "hy", "az", "eu", "be", "bn",
    "bg", "my", "ca", "zh-hans", "zh-hant", "hr", "cs", "da", "nl", "en",
    "et", "fil", "fi", "fr", "gl", "ka", "de", "el", "gu", "ha",
    "he", "hi", "hu", "is", "id", "it", "ja", "jv", "kn", "kk",
    "km", "rw", "ko", "lo", "lv", "lt", "mk", "ms", "ml", "mr",
    "mn", "ne", "no", "fa", "pl", "pt", "pa", "ro", "ru", "sr",
    "sd", "si", "sk", "sl", "es", "su", "sw", "sv", "ta", "te",
    "th", "tr", "uk", "ur", "uz", "vi", "zu",
]

# ISO 639-1 (normalized) -> Gemini BCP-47 target language code mapping.
# Codes not listed here are passed through unchanged.
GEMINI_LANGUAGE_CODE_MAP = {
    "zh": "zh-Hans",   # default to Simplified Chinese; use TARGET_LANG="zh-Hant" for Traditional
    "pt": "pt-BR",
    "tl": "fil",
}

# Full BCP-47 overrides: if the raw configured language (before normalization)
# matches one of these, use it as-is for Gemini.
GEMINI_BCP47_PASSTHROUGH = {"zh-hans": "zh-Hans", "zh-hant": "zh-Hant", "pt-br": "pt-BR", "pt-pt": "pt-PT"}

# Locales that should resolve to Traditional Chinese
_TRADITIONAL_CHINESE_LOCALES = {"zh_tw", "zh_hk", "zh_mo", "zh-tw", "zh-hk", "zh-mo"}


def normalize_language_code(lang: str) -> str:
    """Normalize language code to ISO 639-1 lowercase where possible.

    Examples:
    - 'zh_CN' -> 'zh'
    - 'en-US' -> 'en'
    - ' JA '  -> 'ja'
    """
    if lang is None:
        return ""
    value = str(lang).strip().lower()
    if not value:
        return ""

    # common separators
    for sep in ("_", "-"):
        if sep in value:
            value = value.split(sep, 1)[0]
            break

    return value


def canonicalize_language_code(lang: str) -> str:
    """Normalize a language code while preserving meaningful BCP-47 variants.

    Examples: 'zh-Hant' -> 'zh-hant', 'zh_TW' -> 'zh-hant', 'zh_CN'/'zh' -> 'zh-hans',
    'pt-BR' -> 'pt-br', 'en-US' -> 'en'. (Used by the Gemini provider.)
    """
    raw = str(lang or "").strip().lower()
    if raw in GEMINI_BCP47_PASSTHROUGH:
        return raw
    if raw in _TRADITIONAL_CHINESE_LOCALES:
        return "zh-hant"
    code = normalize_language_code(raw)
    if code == "zh":
        return "zh-hans"
    return code


def to_gemini_language_code(lang: str) -> str:
    """Convert a configured/normalized language code into a Gemini BCP-47 code."""
    raw = str(lang or "").strip()
    passthrough = GEMINI_BCP47_PASSTHROUGH.get(raw.lower())
    if passthrough:
        return passthrough
    code = normalize_language_code(raw)
    return GEMINI_LANGUAGE_CODE_MAP.get(code, code or "en")


def get_supported_language_codes(provider: str | None = None) -> set:
    """Return the validation set of supported (normalized) codes for a provider."""
    p = (provider or globals().get("TRANSLATION_PROVIDER") or "soniox")
    return GEMINI_SUPPORTED_LANGUAGE_CODES if p == "gemini" else SONIOX_SUPPORTED_LANGUAGE_CODES


def get_language_codes_ordered(provider: str | None = None) -> list:
    """Return the ordered list of display language codes for the frontend dropdown."""
    p = (provider or globals().get("TRANSLATION_PROVIDER") or "soniox")
    return list(GEMINI_LANGUAGE_CODES_ORDERED if p == "gemini" else SONIOX_LANGUAGE_CODES_ORDERED)


def is_supported_language_code(lang: str, provider: str | None = None) -> bool:
    code = normalize_language_code(lang)
    return bool(code) and code in get_supported_language_codes(provider)


def canonicalize_target_lang(lang: str, provider: str | None = None) -> str:
    """Canonicalize a target language for the given provider (variant-aware for Gemini)."""
    p = (provider or globals().get("TRANSLATION_PROVIDER") or "soniox")
    return canonicalize_language_code(lang) if p == "gemini" else normalize_language_code(lang)


# English display names for language codes, used to enrich LLM translate/refine
# prompts so the model is told the full language name, not just the code.
# Mirrors the English names in the frontend LANGUAGE_NAME_MAP (static/app.js).
LANGUAGE_ENGLISH_NAMES = {
    "af": "Afrikaans", "ak": "Akan", "sq": "Albanian", "am": "Amharic",
    "ar": "Arabic", "hy": "Armenian", "az": "Azerbaijani", "eu": "Basque",
    "be": "Belarusian", "bn": "Bengali", "bs": "Bosnian", "bg": "Bulgarian",
    "my": "Burmese", "ca": "Catalan", "zh": "Chinese",
    "zh-hans": "Chinese (Simplified)", "zh-hant": "Chinese (Traditional)",
    "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch",
    "en": "English", "et": "Estonian", "fil": "Filipino", "fi": "Finnish",
    "fr": "French", "gl": "Galician", "ka": "Georgian", "de": "German",
    "el": "Greek", "gu": "Gujarati", "ha": "Hausa", "he": "Hebrew",
    "hi": "Hindi", "hu": "Hungarian", "is": "Icelandic", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "jv": "Javanese", "kn": "Kannada",
    "kk": "Kazakh", "km": "Khmer", "rw": "Kinyarwanda", "ko": "Korean",
    "lo": "Lao", "lv": "Latvian", "lt": "Lithuanian", "mk": "Macedonian",
    "ms": "Malay", "ml": "Malayalam", "mr": "Marathi", "mn": "Mongolian",
    "ne": "Nepali", "no": "Norwegian", "fa": "Persian", "pl": "Polish",
    "pt": "Portuguese", "pa": "Punjabi", "ro": "Romanian", "ru": "Russian",
    "sr": "Serbian", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak",
    "sl": "Slovenian", "es": "Spanish", "su": "Sundanese", "sw": "Swahili",
    "sv": "Swedish", "tl": "Tagalog", "ta": "Tamil", "te": "Telugu",
    "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "uz": "Uzbek", "vi": "Vietnamese", "cy": "Welsh", "zu": "Zulu",
}


def language_english_name(lang: str) -> str:
    """Return the English name for a language code, or '' when unknown.

    Tries the raw code first (so BCP-47 variants like 'zh-hant' resolve to
    'Chinese (Traditional)'), then falls back to the normalized primary subtag.
    """
    raw = str(lang or "").strip().lower()
    if not raw:
        return ""
    if raw in LANGUAGE_ENGLISH_NAMES:
        return LANGUAGE_ENGLISH_NAMES[raw]
    return LANGUAGE_ENGLISH_NAMES.get(normalize_language_code(raw), "")


def describe_target_language(lang: str) -> str:
    """Human-readable target-language description for LLM prompts.

    Returns e.g. 'zh (Chinese)' so the model gets both the code and the name.
    Falls back to just the code, or 'unknown' when empty.
    """
    raw = str(lang or "").strip()
    if not raw:
        return "unknown"
    name = language_english_name(raw)
    return f"{raw} ({name})" if name else raw

# Load .env here so env vars are available when other modules import this config.
load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    value = str(value).strip().lower()
    if value in ("1", "true", "yes", "y", "on"):
        return True
    if value in ("0", "false", "no", "n", "off"):
        return False
    return default


def _env_optional_bool(name: str) -> bool | None:
    """Parse an optional boolean env var. Returns None when unset/unrecognized."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = str(value).strip().lower()
    if value in ("1", "true", "yes", "y", "on"):
        return True
    if value in ("0", "false", "no", "n", "off"):
        return False
    return None


def _derive_sleep_on_silence(uses_temp: bool, override: bool | None) -> bool:
    """Effective silence-sleep flag for a given key type.

    Temporary (dispenser) keys never sleep on silence: their streams have a hard
    lifetime managed by the rollover mechanism, so the cost saver is reserved for
    persistent/real keys. The explicit env/CLI override only applies to real keys.
    """
    if uses_temp:
        return False
    return override if override is not None else True


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _env_optional_float(name: str) -> float | None:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except Exception:
        print(f"⚠️  {name} is not a valid number, ignoring")
        return None


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None else str(value)


def _env_json(name: str, default: dict | None = None) -> dict:
    """Parse an environment variable as a JSON object (dict).

    Returns the default (or empty dict) if the env var is unset, empty, or invalid.
    """
    if default is None:
        default = {}
    value = os.environ.get(name)
    if not value:
        return default
    try:
        parsed = json.loads(str(value).strip())
        if isinstance(parsed, dict):
            return parsed
        print(f"⚠️  {name} is not a JSON object, ignoring")
        return default
    except json.JSONDecodeError:
        print(f"⚠️  {name} is not valid JSON, ignoring")
        return default

# ======================== Translation provider selection ========================
# Which STT/translation backend to use: "soniox" | "gemini".
# Normally resolved interactively at startup (see provider_setup.py) and persisted
# to .env as TRANSLATION_PROVIDER. If unset/invalid here, default to "soniox".
_TRANSLATION_PROVIDER_RAW = _env_str("TRANSLATION_PROVIDER", "")
TRANSLATION_PROVIDER = str(_TRANSLATION_PROVIDER_RAW).strip().lower()
if TRANSLATION_PROVIDER not in ("soniox", "gemini"):
    if _TRANSLATION_PROVIDER_RAW.strip():
        print(f"⚠️  Invalid TRANSLATION_PROVIDER: {_TRANSLATION_PROVIDER_RAW}, fallback to: soniox")
    TRANSLATION_PROVIDER = "soniox"


def _provider_sleep_env_names(suffix: str) -> tuple[str, str, str]:
    active_prefix = TRANSLATION_PROVIDER.upper()
    fallback_prefix = "GEMINI" if active_prefix == "SONIOX" else "SONIOX"
    return (
        f"SLEEP_{suffix}",
        f"{active_prefix}_SLEEP_{suffix}",
        f"{fallback_prefix}_SLEEP_{suffix}",
    )


def _provider_rollover_env_names(suffix: str) -> tuple[str, str, str]:
    active_prefix = TRANSLATION_PROVIDER.upper()
    fallback_prefix = "GEMINI" if active_prefix == "SONIOX" else "SONIOX"
    return (
        f"ROLLOVER_{suffix}",
        f"{active_prefix}_ROLLOVER_{suffix}",
        f"{fallback_prefix}_ROLLOVER_{suffix}",
    )


def _env_float_any(names: tuple[str, ...], default: float) -> float:
    for name in names:
        if os.environ.get(name) is not None:
            return _env_float(name, default)
    return default


def _env_optional_bool_any(names: tuple[str, ...]) -> bool | None:
    for name in names:
        value = _env_optional_bool(name)
        if value is not None:
            return value
    return None


# Active validation set (selected by the resolved provider).
SUPPORTED_LANGUAGE_CODES = get_supported_language_codes(TRANSLATION_PROVIDER)

# Shared local VAD/silence-sleep tuning. These values feed the same
# AudioSendRouter for both Soniox and Gemini. Prefer SLEEP_* names; provider
# specific *_SLEEP_* names remain accepted as compatibility aliases.
SLEEP_IDLE_SECONDS = max(1.0, _env_float_any(_provider_sleep_env_names("IDLE_SECONDS"), 30.0))
SLEEP_PRE_ROLL_SECONDS = max(0.0, _env_float_any(_provider_sleep_env_names("PRE_ROLL_SECONDS"), 1.0))
SLEEP_SPEECH_GRACE_SECONDS = max(
    0.0,
    _env_float_any(_provider_sleep_env_names("SPEECH_GRACE_SECONDS"), 0.45),
)
SLEEP_SPEECH_WINDOW_SECONDS = max(
    SLEEP_SPEECH_GRACE_SECONDS,
    _env_float_any(_provider_sleep_env_names("SPEECH_WINDOW_SECONDS"), 1.2),
)
SLEEP_WAKE_SPEECH_SECONDS = max(
    SLEEP_SPEECH_GRACE_SECONDS,
    _env_float_any(_provider_sleep_env_names("WAKE_SPEECH_SECONDS"), 0.65),
)
SLEEP_WAKE_SPEECH_WINDOW_SECONDS = max(
    SLEEP_WAKE_SPEECH_SECONDS,
    _env_float_any(_provider_sleep_env_names("WAKE_SPEECH_WINDOW_SECONDS"), 1.5),
)
SLEEP_VAD_THRESHOLD = min(
    1.0,
    max(0.0, _env_float_any(_provider_sleep_env_names("VAD_THRESHOLD"), 0.5)),
)
SLEEP_WAKE_VAD_THRESHOLD = min(
    1.0,
    max(0.0, _env_float_any(_provider_sleep_env_names("WAKE_VAD_THRESHOLD"), 0.6)),
)
ROLLOVER_VAD_THRESHOLD = min(
    1.0,
    max(0.0, _env_float_any(_provider_rollover_env_names("VAD_THRESHOLD"), 0.8)),
)

# Backwards-compatible constant aliases for code/tests that still import the
# provider-specific names.
SONIOX_SLEEP_IDLE_SECONDS = SLEEP_IDLE_SECONDS
SONIOX_SLEEP_PRE_ROLL_SECONDS = SLEEP_PRE_ROLL_SECONDS
SONIOX_SLEEP_SPEECH_GRACE_SECONDS = SLEEP_SPEECH_GRACE_SECONDS
SONIOX_SLEEP_SPEECH_WINDOW_SECONDS = SLEEP_SPEECH_WINDOW_SECONDS
SONIOX_SLEEP_WAKE_SPEECH_SECONDS = SLEEP_WAKE_SPEECH_SECONDS
SONIOX_SLEEP_WAKE_SPEECH_WINDOW_SECONDS = SLEEP_WAKE_SPEECH_WINDOW_SECONDS
SONIOX_SLEEP_VAD_THRESHOLD = SLEEP_VAD_THRESHOLD
SONIOX_SLEEP_WAKE_VAD_THRESHOLD = SLEEP_WAKE_VAD_THRESHOLD
SONIOX_ROLLOVER_VAD_THRESHOLD = ROLLOVER_VAD_THRESHOLD
GEMINI_SLEEP_IDLE_SECONDS = SLEEP_IDLE_SECONDS
GEMINI_SLEEP_PRE_ROLL_SECONDS = SLEEP_PRE_ROLL_SECONDS
GEMINI_SLEEP_SPEECH_GRACE_SECONDS = SLEEP_SPEECH_GRACE_SECONDS
GEMINI_SLEEP_SPEECH_WINDOW_SECONDS = SLEEP_SPEECH_WINDOW_SECONDS
GEMINI_SLEEP_WAKE_SPEECH_SECONDS = SLEEP_WAKE_SPEECH_SECONDS
GEMINI_SLEEP_WAKE_SPEECH_WINDOW_SECONDS = SLEEP_WAKE_SPEECH_WINDOW_SECONDS
GEMINI_SLEEP_VAD_THRESHOLD = SLEEP_VAD_THRESHOLD
GEMINI_SLEEP_WAKE_VAD_THRESHOLD = SLEEP_WAKE_VAD_THRESHOLD
GEMINI_ROLLOVER_VAD_THRESHOLD = ROLLOVER_VAD_THRESHOLD

# Soniox API configuration
SONIOX_WEBSOCKET_URL = _env_str("SONIOX_WEBSOCKET_URL", "wss://stt-rt.soniox.com/transcribe-websocket")

# Soniox regional endpoints (UI-selectable). Default region is United States.
SONIOX_REGION_URLS = {
    "us": "wss://stt-rt.soniox.com/transcribe-websocket",
    "eu": "wss://stt-rt.eu.soniox.com/transcribe-websocket",
    "jp": "wss://stt-rt.jp.soniox.com/transcribe-websocket",
}


def _infer_soniox_region(url: str) -> str:
    for region, region_url in SONIOX_REGION_URLS.items():
        if region_url == url:
            return region
    return "us"


# Active region; inferred from any explicit URL override so a custom endpoint is preserved.
SONIOX_REGION = _infer_soniox_region(SONIOX_WEBSOCKET_URL)

# True when SONIOX_WEBSOCKET_URL is explicitly set in the environment (.env),
# i.e. the backend pins a Soniox address. The UI uses this to disable region
# selection and show a "custom" label, regardless of which URL was provided.
SONIOX_CUSTOM_URL = bool(os.environ.get("SONIOX_WEBSOCKET_URL", "").strip())

SONIOX_TEMP_KEY_URL = os.environ.get("SONIOX_TEMP_KEY_URL")
SONIOX_USES_TEMP_API_KEY = not bool(os.environ.get("SONIOX_API_KEY", "").strip())

# Optional stream rollover for temporary API keys whose websocket streams have
# a hard lifetime. Set this in .env to proactively start a fresh stream.
_SONIOX_STREAM_DURATION_SECONDS_RAW = _env_optional_float("SONIOX_STREAM_DURATION_SECONDS")
if _SONIOX_STREAM_DURATION_SECONDS_RAW is not None and _SONIOX_STREAM_DURATION_SECONDS_RAW <= 0:
    print("⚠️  SONIOX_STREAM_DURATION_SECONDS must be greater than 0, ignoring")
    _SONIOX_STREAM_DURATION_SECONDS_RAW = None
SONIOX_STREAM_DURATION_SECONDS = _SONIOX_STREAM_DURATION_SECONDS_RAW

# Optional cost saver: close the Soniox websocket after long local silence and
# reopen it when local VAD sees speech again. Only enabled for persistent/real
# keys (temporary dispenser keys keep their streams open). The active key type
# can change at runtime via provider/key hot-switch, so this is recomputed in
# set_uses_temp_api_key().
_SONIOX_SLEEP_ON_SILENCE_OVERRIDE = _env_optional_bool_any(("SONIOX_SLEEP_ON_SILENCE", "SLEEP_ON_SILENCE"))
_RUNTIME_SLEEP_ON_SILENCE_OVERRIDE: bool | None = None
SONIOX_SLEEP_ON_SILENCE = _derive_sleep_on_silence(
    SONIOX_USES_TEMP_API_KEY, _SONIOX_SLEEP_ON_SILENCE_OVERRIDE
)

# Gemini Live API configuration
GEMINI_WEBSOCKET_URL = _env_str(
    "GEMINI_WEBSOCKET_URL",
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent",
)
GEMINI_MODEL = _env_str("GEMINI_MODEL", "gemini-3.5-live-translate-preview")
GEMINI_TEMP_KEY_URL = os.environ.get("GEMINI_TEMP_KEY_URL")
GEMINI_USES_TEMP_API_KEY = not bool(os.environ.get("GEMINI_API_KEY", "").strip())

# GEMINI_ECHO_TARGET_LANGUAGE is derived from OSC_SEND_TEXT_MODE further below
# (it depends on that value), see the definition after the OSC mode block.

# Optional stream rollover: proactively start a fresh Live API stream before the
# configured lifetime is reached (Gemini Live sessions also have hard limits).
_GEMINI_STREAM_DURATION_SECONDS_RAW = _env_optional_float("GEMINI_STREAM_DURATION_SECONDS")
if _GEMINI_STREAM_DURATION_SECONDS_RAW is not None and _GEMINI_STREAM_DURATION_SECONDS_RAW <= 0:
    print("⚠️  GEMINI_STREAM_DURATION_SECONDS must be greater than 0, ignoring")
    _GEMINI_STREAM_DURATION_SECONDS_RAW = None
GEMINI_STREAM_DURATION_SECONDS = _GEMINI_STREAM_DURATION_SECONDS_RAW

# Optional cost saver: close the Gemini websocket after long local silence and
# reopen it when local VAD sees speech again. By default, sleep only when a
# persistent GEMINI_API_KEY is configured.
_GEMINI_SLEEP_ON_SILENCE_OVERRIDE = _env_optional_bool_any(("GEMINI_SLEEP_ON_SILENCE", "SLEEP_ON_SILENCE"))
GEMINI_SLEEP_ON_SILENCE = _derive_sleep_on_silence(
    GEMINI_USES_TEMP_API_KEY, _GEMINI_SLEEP_ON_SILENCE_OVERRIDE
)


def get_sleep_on_silence_enabled(provider: str | None = None) -> bool:
    """Return the requested auto-sleep preference before key-type gating."""
    if _RUNTIME_SLEEP_ON_SILENCE_OVERRIDE is not None:
        return _RUNTIME_SLEEP_ON_SILENCE_OVERRIDE
    p = str(provider or TRANSLATION_PROVIDER or "soniox").strip().lower()
    override = (
        _GEMINI_SLEEP_ON_SILENCE_OVERRIDE
        if p == "gemini"
        else _SONIOX_SLEEP_ON_SILENCE_OVERRIDE
    )
    return override if override is not None else True


def set_sleep_on_silence_enabled(enabled: bool) -> bool:
    """Hot-apply the shared auto-sleep preference to both providers."""
    global _RUNTIME_SLEEP_ON_SILENCE_OVERRIDE
    global SONIOX_SLEEP_ON_SILENCE, GEMINI_SLEEP_ON_SILENCE

    _RUNTIME_SLEEP_ON_SILENCE_OVERRIDE = bool(enabled)
    SONIOX_SLEEP_ON_SILENCE = _derive_sleep_on_silence(
        SONIOX_USES_TEMP_API_KEY, _RUNTIME_SLEEP_ON_SILENCE_OVERRIDE
    )
    GEMINI_SLEEP_ON_SILENCE = _derive_sleep_on_silence(
        GEMINI_USES_TEMP_API_KEY, _RUNTIME_SLEEP_ON_SILENCE_OVERRIDE
    )
    return _RUNTIME_SLEEP_ON_SILENCE_OVERRIDE

# ============ Subtitle-server relay (hosted mode) ============
# When a server URL is configured, the app can run in "relay" (hosted) mode:
# instead of connecting directly to Soniox/Gemini with the user's own upstream
# key, it authenticates to a subtitle-server instance (VRChat profile proof) and
# relays all STT/translation traffic through it. The server URL is read only from
# the environment (.env) and is never editable from the UI.
SUBTITLE_SERVER_URL = _env_str("SUBTITLE_SERVER_URL", "").strip().rstrip("/")
RELAY_AVAILABLE = bool(SUBTITLE_SERVER_URL)
CLIENT_VERSION = "4.1.3"

# Optional pre-configured account token (long-lived ss_ key). Read-only fallback;
# the UI login flow / localStorage override take priority at runtime.
SUBTITLE_SERVER_TOKEN = _env_str("SUBTITLE_SERVER_TOKEN", "").strip()

# Runtime relay state (hot-switchable, like provider/key). RELAY_MODE selects
# whether sessions connect through the relay; RELAY_TOKEN is the active account
# token used only when requesting a short-lived relay connect ticket.
RELAY_MODE = False
RELAY_TOKEN = SUBTITLE_SERVER_TOKEN

# When enabled, a half-width , . ? or ! in Gemini's returned text (source or
# translation) is converted to its full-width form (，。？！) whenever it sits
# directly between two CJK (Han) characters, e.g. "你好,世界" -> "你好，世界".
# Requiring CJK on both sides avoids touching decimals, URLs, abbreviations, etc.
GEMINI_FULLWIDTH_PUNCT_FIX = _env_bool("GEMINI_FULLWIDTH_PUNCT_FIX", True)

# When enabled, Arabic/Hebrew text is reshaped and bidi-reordered before being
# sent to the VRChat OSC chatbox. The browser subtitle UI keeps the original
# logical text and uses CSS direction handling instead.
ENABLE_ARABIC_RESHAPER = _env_bool("ENABLE_ARABIC_RESHAPER", True)

# Auto use system language
# True: detect system locale and use it as translation target language
# False: use manually specified TARGET_LANG below
USE_SYSTEM_LANGUAGE = _env_bool("USE_SYSTEM_LANGUAGE", True)

# Manually specified target language (used when USE_SYSTEM_LANGUAGE=False)
TARGET_LANG = _env_str("TARGET_LANG", "ja")
TARGET_LANG_1 = _env_str("TARGET_LANG_1", "en")
TARGET_LANG_2 = _env_str("TARGET_LANG_2", "zh")

# Translation mode: none | one_way | two_way
# - none: disable translation
# - one_way: one-way translation (target language decided by TRANSLATION_TARGET_LANG)
# - two_way: two-way translation (language pair decided by TARGET_LANG_1/TARGET_LANG_2)
_TRANSLATION_MODE_RAW = _env_str("TRANSLATION_MODE", "one_way")
TRANSLATION_MODE = str(_TRANSLATION_MODE_RAW).strip().lower()
if TRANSLATION_MODE not in ("none", "one_way", "two_way"):
    print(f"⚠️  Invalid TRANSLATION_MODE: {_TRANSLATION_MODE_RAW}, fallback to: one_way")
    TRANSLATION_MODE = "one_way"

# OSC text selection mode for translation sending
# - smart: send translation when available; if translation is unavailable and source language equals target language, send source text
# - translation_only: send translation text only
# - source_only: send source text only (regardless of language)
_OSC_SEND_TEXT_MODE_RAW = _env_str("OSC_SEND_TEXT_MODE", "smart")
OSC_SEND_TEXT_MODE = str(_OSC_SEND_TEXT_MODE_RAW).strip().lower()
if OSC_SEND_TEXT_MODE not in ("smart", "translation_only", "source_only"):
    print(f"⚠️  Invalid OSC_SEND_TEXT_MODE: {_OSC_SEND_TEXT_MODE_RAW}, fallback to: smart")
    OSC_SEND_TEXT_MODE = "smart"

# Whether Gemini should echo (parrot) audio that is already in the target
# language. Derived strictly from OSC_SEND_TEXT_MODE: true iff "smart" or
# "source_only".
# - smart / source_only need a transcript even for same-language speech so the
#   sentence-finalization gates (which require a translation token or a
#   source-as-translation fallback) always fire and the chosen text can be sent
#   as the final OSC message; without echo, same-language sentences would be
#   dropped before reaching OSC output.
# - translation_only wants genuine translations only, so echoes are suppressed.
GEMINI_ECHO_TARGET_LANGUAGE = OSC_SEND_TEXT_MODE in ("smart", "source_only")

# Auto-open built-in WebView (enabled by default)
# True: create embedded webview window on startup
# False: only print URL in console; open browser manually, and closing webpage won't exit app
AUTO_OPEN_WEBVIEW = _env_bool("AUTO_OPEN_WEBVIEW", True)

# UI lock: hide manual-control buttons and disable related backend operations
# True: frontend hides restart/pause/auto-restart/audio-source/OSC translation controls;
#       backend rejects /pause, /resume, manual /restart, /audio-source toggle,
#       and /osc-translation toggle; frontend also forces auto-restart-on-disconnect
# False: show and allow manual controls as normal
LOCK_MANUAL_CONTROLS = _env_bool("LOCK_MANUAL_CONTROLS", False)

# Twitch audio streaming transcription (disabled by default)
# True: use streamlink to pull Twitch stream, then ffmpeg extracts audio to 16kHz mono PCM for STT
# False: use local system audio/microphone capture
USE_TWITCH_AUDIO_STREAM = _env_bool("USE_TWITCH_AUDIO_STREAM", False)

# Whether to mute the microphone component in outgoing audio when VRChat MuteSelf=true
# - True (default):
#   - microphone mode: send silent frames (keep cadence, do not pause)
#   - mix mode: zero out mic component only; system component continues
# - False: ignore VRChat MuteSelf state
MUTE_MIC_WHEN_VRCHAT_SELF_MUTED = _env_bool("MUTE_MIC_WHEN_VRCHAT_SELF_MUTED", True)

# Optional microphone device ID. Empty means "current system default".
MICROPHONE_DEVICE_ID = _env_str("MICROPHONE_DEVICE_ID", "").strip()

# Optional speaker/output device ID used for system loopback capture.
# Empty means "current system default".
OUTPUT_DEVICE_ID = _env_str("OUTPUT_DEVICE_ID", "").strip()

# Mix audio weights (effective only when audio source is mix)
# Convention:
# - "self" = microphone
# - "others" = system/speaker loopback
#
# You only need to set one variable; the other is auto-calculated as 1 - value.
_MIX_OWN_VOLUME_RAW = _env_float("MIX_OWN_VOLUME", 0.5)
MIX_OWN_VOLUME = min(1.0, max(0.0, _MIX_OWN_VOLUME_RAW))
MIX_OTHER_VOLUME = 1.0 - MIX_OWN_VOLUME

# Speaker diarization switch (Soniox only; enabled by default for Soniox).
# True: enable diarization (frontend shows speaker labels, OSC output gets S0/S1 prefixes)
# False: disable diarization (frontend hides speaker labels)
# Gemini Live Translation does not support diarization, so it is forced off there.
if TRANSLATION_PROVIDER == "gemini":
    if _env_bool("ENABLE_SPEAKER_DIARIZATION", False):
        print("⚠️  ENABLE_SPEAKER_DIARIZATION is not supported by Gemini Live Translation; ignoring")
    ENABLE_SPEAKER_DIARIZATION = False
else:
    ENABLE_SPEAKER_DIARIZATION = _env_bool("ENABLE_SPEAKER_DIARIZATION", True)

# Hide speaker labels (disabled by default)
# True: frontend hides speaker index labels (even if diarization is enabled)
# False: show speaker labels normally
HIDE_SPEAKER_LABELS = _env_bool("HIDE_SPEAKER_LABELS", False)

# Enable chroma (green) theme (disabled by default)
# When enabled: chroma theme appears in the theme cycle; switching to it also
#               removes window always-on-top, switching away restores it.
# When disabled: chroma theme is hidden from the theme cycle entirely.
ENABLE_CHROMA_THEME = _env_bool("ENABLE_CHROMA_THEME", False)

# Default sentence segmentation mode: 'translation' | 'endpoint' | 'punctuation'
# - translation: based on Soniox <end> marker
# - endpoint: based on Soniox endpoint_detected flag
# - punctuation: based on sentence-ending punctuation (default)
DEFAULT_SEGMENT_MODE = _env_str("DEFAULT_SEGMENT_MODE", "punctuation")

# Repair short speaker interruptions in noisy VRChat rooms. Soniox real-time
# diarization can temporarily split A's sentence as A1 -> short B -> A2; when
# timestamps prove B was brief and A resumes quickly, the UI retracts A1 and the
# LLM receives A1+A2 as one source sentence.
SONIOX_INTERRUPT_REPAIR_ENABLED = _env_bool("SONIOX_INTERRUPT_REPAIR_ENABLED", True)
SONIOX_INTERRUPT_MAX_DURATION_MS = max(0, _env_int("SONIOX_INTERRUPT_MAX_DURATION_MS", 800))
SONIOX_INTERRUPT_RESUME_GAP_MS = max(0, _env_int("SONIOX_INTERRUPT_RESUME_GAP_MS", 1500))
SONIOX_INTERRUPT_FILLER_WHITELIST_ENABLED = _env_bool("SONIOX_INTERRUPT_FILLER_WHITELIST_ENABLED", True)
SONIOX_INTERRUPT_FILLER_WHITELIST = _env_str(
    "SONIOX_INTERRUPT_FILLER_WHITELIST",
    (
        "啊,阿,呀,哎,唉,诶,欸,嗯,恩,唔,呃,额,呜,哦,噢,喔,嗷,哼,嗯嗯,呃呃,"
        "哦哦,啊啊,哎呀,哎哟,"
        "uh,uhh,uh-huh,um,umm,erm,er,ah,oh,o,ooh,oooh,eh,huh,hm,hmm,mm,mmm,"
        "yeah,yep,ya,yes,mhm,mhmm,"
        "うん,ううん,うーん,うわ,え,ええ,あ,ああ,お,おお,ほう,へえ,ふむ,ん,あの,えっと,まあ,えへ,えへへ,"
        "はい,はいはい,そう,そうそう,そうそうそう,そっか,なるほど,"
        "어,어어,음,응,응응,아,아아,오,에,으음,흠,네,네네,맞아,맞아맞아"
    ),
)

# Twitch channel name (without https://www.twitch.tv/ prefix)
TWITCH_CHANNEL = _env_str("TWITCH_CHANNEL", "")

# Preferred stream quality (usually: audio_only / best)
TWITCH_STREAM_QUALITY = _env_str("TWITCH_STREAM_QUALITY", "audio_only")

# ffmpeg executable path (default expects ffmpeg in PATH)
FFMPEG_PATH = _env_str("FFMPEG_PATH", "ffmpeg")

# Server configuration
# When SERVER_PORT=0, an available port is selected automatically
# When AUTO_OPEN_WEBVIEW=True, force bind to 127.0.0.1;
# when disabled, default bind to 0.0.0.0 for LAN access
SERVER_HOST = _env_str("SERVER_HOST", "0.0.0.0")
SERVER_PORT = _env_int("SERVER_PORT", 8080)

# LLM (OpenAI-compatible) config: used for minimal edits on completed translated segments.
# Notes:
# - LLM_BASE_URL example: https://openrouter.ai/api/v1
# - LLM_API_KEY is used for authentication
# - LLM_MODEL example: openai/gpt-oss-120b:google-vertex
LLM_BASE_URL = _env_str("LLM_BASE_URL", "")
LLM_API_KEY = _env_str("LLM_API_KEY", "")
LLM_MODEL = _env_str("LLM_MODEL", "openai/gpt-oss-120b:google-vertex")

# Default LLM refine switch (startup default; user can toggle unless frontend is locked)
LLM_REFINE_DEFAULT_ENABLED = _env_bool("LLM_REFINE_DEFAULT_ENABLED", True)

# Default LLM translation mode: off | refine | translate
# Effective only when browser has no stored history, or when LOCK_MANUAL_CONTROLS is enabled
_LLM_DEFAULT_MODE_RAW = _env_str("LLM_REFINE_DEFAULT_MODE", "")
_LLM_DEFAULT_MODE = str(_LLM_DEFAULT_MODE_RAW).strip().lower()
if _LLM_DEFAULT_MODE not in ("off", "refine", "translate"):
    _LLM_DEFAULT_MODE = "refine" if LLM_REFINE_DEFAULT_ENABLED else "off"
LLM_REFINE_DEFAULT_MODE = _LLM_DEFAULT_MODE

# Optional suffix appended to the end of the LLM prompt.
# Default: empty string (no suffix). Example: "/no_think"
LLM_PROMPT_SUFFIX = _env_str("LLM_PROMPT_SUFFIX", "")

# LLM temperature (0.0-2.0). Lower is more deterministic.
LLM_TEMPERATURE = min(2.0, max(0.0, _env_float("LLM_TEMPERATURE", 0.2)))

# Context item range used for LLM refine / translate (completed pairs: source + translation).
# Strategy:
# - each request starts from min count and increases by +1 up to max count
# - after reaching max, next request resets to min and then grows again
# This keeps prefixes relatively stable while periodically controlling context length.
_LLM_REFINE_CONTEXT_MIN_COUNT_RAW = _env_int("LLM_REFINE_CONTEXT_MIN_COUNT", 5)
_LLM_REFINE_CONTEXT_MAX_COUNT_RAW = _env_int("LLM_REFINE_CONTEXT_MAX_COUNT", 5)

LLM_REFINE_CONTEXT_MIN_COUNT = max(1, _LLM_REFINE_CONTEXT_MIN_COUNT_RAW)
LLM_REFINE_CONTEXT_MAX_COUNT = max(LLM_REFINE_CONTEXT_MIN_COUNT, _LLM_REFINE_CONTEXT_MAX_COUNT_RAW)

# Maximum output tokens for LLM refine.
# Note: max_tokens limits vary across providers/models.
LLM_REFINE_MAX_TOKENS = min(8192, max(1, _env_int("LLM_REFINE_MAX_TOKENS", 1024)))

# Optional extra HTTP headers to include in every LLM request.
# Set as a JSON object string, e.g.: {"X-Custom-Header": "value", "X-API-Version": "2"}
# These are merged into (and may override) the default Authorization/Content-Type headers.
LLM_REQUEST_HEADERS = _env_json("LLM_REQUEST_HEADERS")

# Optional extra JSON body fields to merge into every LLM request payload.
# Set as a JSON object string, e.g.: {"provider": {"order": ["google-vertex"]}}
# These are merged into the top-level request body (may override standard fields).
LLM_REQUEST_JSON = _env_json("LLM_REQUEST_JSON")


def _parse_llm_api_keys(raw: str) -> list[str]:
        """Parse LLM_API_KEY which may contain multiple keys separated by commas.

        Example:
            "keyA, keyB,keyC" -> ["keyA", "keyB", "keyC"]
        """
        if raw is None:
                return []
        parts = [p.strip() for p in str(raw).split(",")]
        # Filter empties and common placeholder values.
        keys = [p for p in parts if p and p != "LLM_API_KEY"]
        return keys


_LLM_API_KEYS: list[str] = _parse_llm_api_keys(LLM_API_KEY)
_LLM_API_KEY_LOCK = threading.Lock()
_LLM_API_KEY_INDEX = 0


def get_llm_api_key() -> str:
    """Return one configured LLM API key.

    Supports multiple keys via comma-separated LLM_API_KEY and returns them in round-robin.
    """
    global _LLM_API_KEY_INDEX

    keys = _LLM_API_KEYS
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]

    with _LLM_API_KEY_LOCK:
        key = keys[_LLM_API_KEY_INDEX % len(keys)]
        _LLM_API_KEY_INDEX += 1
        return key


def get_llm_api_keys() -> list[str]:
    """Return all configured LLM API keys (after parsing/stripping)."""
    return list(_LLM_API_KEYS)


# ======================== Hosted (relay) LLM tuning ========================
# In relay/hosted mode the LLM upstream config, context window, timeout and
# input cap are delivered by the subtitle-server (GET /billing/llm-config) rather
# than read from the local .env. Populated via set_hosted_llm_config().
HOSTED_LLM_AVAILABLE = False
HOSTED_LLM_CONTEXT_MIN = int(LLM_REFINE_CONTEXT_MIN_COUNT)
HOSTED_LLM_CONTEXT_MAX = int(LLM_REFINE_CONTEXT_MAX_COUNT)
HOSTED_LLM_TIMEOUT_SECONDS = 8.0
HOSTED_LLM_MAX_INPUT_TOKENS = 10000
HOSTED_LLM_MAX_OUTPUT_TOKENS = 200
# STT billing factor the server applies to soniox 准确 (accurate) mode, where
# soniox's built-in translation is disabled. Defaults to 1.0 (no discount) until
# the server delivers the real value, so the client never under-estimates cost.
HOSTED_SONIOX_NO_TRANSLATION_FACTOR = 1.0


def set_hosted_llm_config(cfg) -> None:
    """Store the server-delivered hosted LLM tuning (relay mode)."""
    global HOSTED_LLM_AVAILABLE, HOSTED_LLM_CONTEXT_MIN, HOSTED_LLM_CONTEXT_MAX
    global HOSTED_LLM_TIMEOUT_SECONDS, HOSTED_LLM_MAX_INPUT_TOKENS, HOSTED_LLM_MAX_OUTPUT_TOKENS
    global HOSTED_SONIOX_NO_TRANSLATION_FACTOR
    if not isinstance(cfg, dict):
        return
    HOSTED_LLM_AVAILABLE = bool(cfg.get("available"))
    try:
        cmin = int(cfg.get("context_min", HOSTED_LLM_CONTEXT_MIN))
        cmax = int(cfg.get("context_max", HOSTED_LLM_CONTEXT_MAX))
        HOSTED_LLM_CONTEXT_MIN = max(1, cmin)
        HOSTED_LLM_CONTEXT_MAX = max(HOSTED_LLM_CONTEXT_MIN, cmax)
    except Exception:
        pass
    try:
        HOSTED_LLM_TIMEOUT_SECONDS = max(1.0, float(cfg.get("timeout_seconds", HOSTED_LLM_TIMEOUT_SECONDS)))
    except Exception:
        pass
    try:
        HOSTED_LLM_MAX_INPUT_TOKENS = max(1, int(cfg.get("max_input_tokens", HOSTED_LLM_MAX_INPUT_TOKENS)))
    except Exception:
        pass
    try:
        HOSTED_LLM_MAX_OUTPUT_TOKENS = max(1, int(cfg.get("max_output_tokens", HOSTED_LLM_MAX_OUTPUT_TOKENS)))
    except Exception:
        pass
    try:
        factor = float(cfg.get("soniox_no_translation_factor", HOSTED_SONIOX_NO_TRANSLATION_FACTOR))
        if factor > 0:
            HOSTED_SONIOX_NO_TRANSLATION_FACTOR = min(1.0, factor)
    except Exception:
        pass


def llm_is_hosted() -> bool:
    """Whether LLM calls should go through the relay instead of a local key."""
    return bool(RELAY_MODE)


def llm_context_bounds() -> tuple[int, int]:
    """Effective (min, max) context item counts for LLM refine/translate."""
    if RELAY_MODE:
        return (int(HOSTED_LLM_CONTEXT_MIN), int(HOSTED_LLM_CONTEXT_MAX))
    return (int(LLM_REFINE_CONTEXT_MIN_COUNT), int(LLM_REFINE_CONTEXT_MAX_COUNT))


def llm_timeout_seconds() -> float:
    """Per-request LLM timeout. Hosted mode uses the server value; own-key mode a
    generous timeout (no client-side retry there)."""
    return float(HOSTED_LLM_TIMEOUT_SECONDS) if RELAY_MODE else 60.0


def llm_max_output_tokens() -> int:
    return int(HOSTED_LLM_MAX_OUTPUT_TOKENS) if RELAY_MODE else int(LLM_REFINE_MAX_TOKENS)


def is_llm_refine_available() -> bool:
    """Whether LLM refine/translate can run. In relay mode this reflects the
    server-advertised hosted availability; in own-key mode the local config."""
    if RELAY_MODE:
        return bool(HOSTED_LLM_AVAILABLE)
    keys = get_llm_api_keys()
    base_url = (LLM_BASE_URL or "").strip()
    model = (LLM_MODEL or "").strip()
    if not keys:
        return False
    if not base_url or not model:
        return False
    return True


def get_resource_path(relative_path):
    """Get absolute path to a resource file (works in dev and PyInstaller builds)."""
    if hasattr(sys, '_MEIPASS'):
        # Temporary folder created by PyInstaller
        return os.path.join(sys._MEIPASS, relative_path)
    # Development environment
    return os.path.join(os.path.abspath('.'), relative_path)


def get_system_language(provider: str | None = None) -> str:
    """
    Get the system language code for the active provider.
    Soniox returns an ISO 639-1 code ('zh', 'en', ...); Gemini preserves BCP-47
    variants where meaningful ('zh-hant', ...).
    """
    p = provider or TRANSLATION_PROVIDER
    try:
        # Get system locale
        system_locale = locale.getdefaultlocale()[0]  # e.g. 'zh_CN', 'en_US', 'ja_JP'

        if system_locale:
            if p == "gemini":
                lang_code = canonicalize_language_code(system_locale)
            else:
                lang_code = normalize_language_code(system_locale)
            if is_supported_language_code(lang_code, p):
                print(f"🌐 Detected system language: {system_locale} -> {lang_code}")
                return lang_code
            print(f"⚠️  Detected system language not supported: {system_locale} -> {lang_code}, fallback to: en")
            return "en"
        else:
            print("⚠️  Unable to detect system language, using default: en")
            return "en"
    except Exception as e:
        print(f"⚠️  Failed to get system language: {e}, using default: en")
        return "en"


# Decide translation target language based on configuration
if USE_SYSTEM_LANGUAGE:
    TRANSLATION_TARGET_LANG = get_system_language()
else:
    normalized_target = canonicalize_target_lang(TARGET_LANG)
    if is_supported_language_code(normalized_target):
        TRANSLATION_TARGET_LANG = normalized_target
    else:
        print(f"⚠️  Config TARGET_LANG not supported: {TARGET_LANG} -> {normalized_target}, fallback to: en")
        TRANSLATION_TARGET_LANG = "en"

print(f"✅ Translation provider: {TRANSLATION_PROVIDER}")
print(f"✅ Translation target language set to: {TRANSLATION_TARGET_LANG}")


# ======================== Provider capabilities ========================
# Feature matrix used by the backend (/ui-config) and frontend to gate UI.
PROVIDER_CAPABILITIES = {
    "soniox": {
        "segment_mode": True,
        "speaker_diarization": True,
        "two_way_translation": True,
    },
    "gemini": {
        "segment_mode": False,
        "speaker_diarization": False,
        "two_way_translation": False,
    },
}


def get_capabilities(provider: str | None = None) -> dict:
    """Return a copy of the capability flags for the given (or active) provider."""
    p = provider or TRANSLATION_PROVIDER
    return dict(PROVIDER_CAPABILITIES.get(p, PROVIDER_CAPABILITIES["soniox"]))


# ======================== Runtime provider switching ========================
# The active provider can be switched at runtime (hot-switch) from the Web UI.
# Recompute the module-level values that depend on it so /ui-config and freshly
# created sessions reflect the new provider immediately.

def _compute_enable_speaker_diarization(provider: str) -> bool:
    """Diarization is Soniox-only; Gemini Live Translation forces it off."""
    if provider == "gemini":
        return False
    return _env_bool("ENABLE_SPEAKER_DIARIZATION", True)


def _compute_translation_target_lang(provider: str) -> str:
    """Resolve the default one-way target language for the given provider."""
    if USE_SYSTEM_LANGUAGE:
        return get_system_language(provider)
    normalized = canonicalize_target_lang(TARGET_LANG, provider)
    if is_supported_language_code(normalized, provider):
        return normalized
    return "en"


def set_active_provider(provider: str) -> str:
    """Switch the active translation provider and recompute dependent values.

    Returns the normalized provider name. Safe to call repeatedly (hot-switch).
    """
    global TRANSLATION_PROVIDER, SUPPORTED_LANGUAGE_CODES
    global ENABLE_SPEAKER_DIARIZATION, TRANSLATION_TARGET_LANG

    p = str(provider or "").strip().lower()
    if p not in ("soniox", "gemini"):
        p = "soniox"

    TRANSLATION_PROVIDER = p
    os.environ["TRANSLATION_PROVIDER"] = p
    SUPPORTED_LANGUAGE_CODES = get_supported_language_codes(p)
    ENABLE_SPEAKER_DIARIZATION = _compute_enable_speaker_diarization(p)
    TRANSLATION_TARGET_LANG = _compute_translation_target_lang(p)
    return p


def set_uses_temp_api_key(provider: str, uses_temp: bool) -> None:
    """Record whether the active key for a provider is a dispenser temp key.

    The silence-sleep cost saver is disabled by default for temporary keys. The
    active key can change at runtime via hot-switch (localStorage override vs.
    dispenser temp key) without re-importing config, so the derived
    *_SLEEP_ON_SILENCE value must be recomputed here. An explicit env/CLI
    override always wins and is left untouched. Safe to call repeatedly.
    """
    global SONIOX_USES_TEMP_API_KEY, GEMINI_USES_TEMP_API_KEY
    global SONIOX_SLEEP_ON_SILENCE, GEMINI_SLEEP_ON_SILENCE

    p = str(provider or "").strip().lower()
    uses_temp = bool(uses_temp)
    if p == "gemini":
        GEMINI_USES_TEMP_API_KEY = uses_temp
        GEMINI_SLEEP_ON_SILENCE = _derive_sleep_on_silence(
            uses_temp, get_sleep_on_silence_enabled("gemini")
        )
    else:
        SONIOX_USES_TEMP_API_KEY = uses_temp
        SONIOX_SLEEP_ON_SILENCE = _derive_sleep_on_silence(
            uses_temp, get_sleep_on_silence_enabled("soniox")
        )


def set_soniox_region(region: str) -> str:
    """Select a Soniox regional endpoint (us | eu | jp) and update the websocket URL.

    Returns the normalized region. Safe to call repeatedly (hot-switch).
    """
    global SONIOX_WEBSOCKET_URL, SONIOX_REGION

    r = str(region or "").strip().lower()
    if r not in SONIOX_REGION_URLS:
        r = "us"
    SONIOX_REGION = r
    SONIOX_WEBSOCKET_URL = SONIOX_REGION_URLS[r]
    return r


def set_relay_mode(enabled) -> bool:
    """Enable/disable subtitle-server relay (hosted) mode. Hot-switchable."""
    global RELAY_MODE
    RELAY_MODE = bool(enabled)
    return RELAY_MODE


def set_relay_token(token) -> str:
    """Set the active relay account token (ss_ key). Hot-switchable."""
    global RELAY_TOKEN
    RELAY_TOKEN = str(token or "").strip()
    return RELAY_TOKEN


def _relay_provider(provider: str | None = None) -> str:
    p = (provider or globals().get("TRANSLATION_PROVIDER") or "soniox")
    p = str(p).strip().lower()
    if p not in ("soniox", "gemini"):
        p = "soniox"
    return p


def _relay_connect_url(provider: str | None = None, model: str | None = None, translation: str | None = None) -> str:
    """REST endpoint used to mint a short-lived provider relay connection."""
    p = _relay_provider(provider)
    url = relay_rest_url(f"/relay/{p}/connect")
    params = {}
    if model:
        params["model"] = str(model)
    if translation:
        params["translation"] = str(translation)
    if params:
        url += "?" + urlencode(params)
    return url


def relay_connect_info(provider: str | None = None, model: str | None = None, translation: str | None = None) -> dict:
    """Request the actual relay WebSocket URL from subtitle-server.

    The server registers the full relay session directly with the selected
    backend (Durable Object or VPS) and returns only an opaque one-time ticket in
    the WebSocket URL. The account token is sent only to this REST endpoint, not
    to the relay backend WebSocket.
    """
    if not SUBTITLE_SERVER_URL:
        raise RuntimeError("SUBTITLE_SERVER_URL is required for hosted relay mode")

    token = str(RELAY_TOKEN or "").strip()
    if not token:
        raise RuntimeError("Relay account token is missing; please sign in again")

    url = _relay_connect_url(provider, model=model, translation=translation)
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to request relay connection: {exc}") from exc

    if response.status_code != 200:
        detail = (response.text or "").strip()
        if len(detail) > 300:
            detail = detail[:300] + "..."
        from relay_errors import RelayConnectionRequestError
        raise RelayConnectionRequestError(
            response.status_code,
            detail or response.reason,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Relay connection response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Relay connection response must be a JSON object")

    ws_url = data.get("url")
    if not isinstance(ws_url, str) or not ws_url.strip():
        raise RuntimeError("Relay connection response is missing url")

    relay_headers = data.get("headers") or {}
    if not isinstance(relay_headers, dict):
        raise RuntimeError("Relay connection response headers must be an object")

    clean_headers = {}
    for key, value in relay_headers.items():
        header_name = str(key).strip()
        if not header_name or value is None:
            continue
        clean_headers[header_name] = str(value)

    result = dict(data)
    result["url"] = ws_url.strip()
    result["headers"] = clean_headers
    return result


def relay_rest_url(path: str = "") -> str:
    """REST URL on the configured subtitle-server (path joined onto the base)."""
    path = str(path or "")
    if path and not path.startswith("/"):
        path = "/" + path
    return f"{SUBTITLE_SERVER_URL}{path}"

# ============ IPC Configuration (realtime-subtitle <-> Yakutan) ============

# Whether to enable IPC functionality
IPC_ENABLED = _env_bool("IPC_ENABLED", True)

# IPC server host
IPC_HOST = _env_str("IPC_HOST", "127.0.0.1")

# IPC port range
IPC_PORT_RANGE = range(17353, 17364)

# IPC discovery file path
import tempfile
from shared.vrchat_bridge import get_discovery_path
IPC_DISCOVERY_FILE = _env_str(
    "IPC_DISCOVERY_FILE",
    get_discovery_path()
)


def get_app_data_dir() -> str:
    """Per-user persistent directory for settings shared across local instances.

    Unlike the IPC discovery file (which lives in TEMP), this must survive
    reboots, so it goes under the platform's per-user application-data dir.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    return os.path.join(base, "RealtimeSubtitle")


# Shared browser-settings store. All local instances read/write this single
# file so that settings + login (normally kept in per-origin localStorage)
# survive the dynamic-port origin change when a second instance launches on a
# new port. See local_store.py and static/local-store-sync.js.
LOCAL_SETTINGS_FILE = _env_str(
    "LOCAL_SETTINGS_FILE",
    os.path.join(get_app_data_dir(), "local_settings.json"),
)
