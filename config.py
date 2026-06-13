"""
Configuration file - stores all configuration options and constants.
"""
import json
import os
import sys
import locale
import time
import threading
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

# Active validation set (selected by the resolved provider).
SUPPORTED_LANGUAGE_CODES = get_supported_language_codes(TRANSLATION_PROVIDER)

# Soniox API configuration
SONIOX_WEBSOCKET_URL = _env_str("SONIOX_WEBSOCKET_URL", "wss://stt-rt.soniox.com/transcribe-websocket")
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
# reopen it when local VAD sees speech again. By default, keep temporary API key
# streams open and sleep only when a persistent SONIOX_API_KEY is configured.
SONIOX_SLEEP_ON_SILENCE = _env_bool("SONIOX_SLEEP_ON_SILENCE", not SONIOX_USES_TEMP_API_KEY)

SONIOX_SLEEP_IDLE_SECONDS = max(1.0, _env_float("SONIOX_SLEEP_IDLE_SECONDS", 30.0))
SONIOX_SLEEP_PRE_ROLL_SECONDS = max(0.0, _env_float("SONIOX_SLEEP_PRE_ROLL_SECONDS", 0.5))
SONIOX_SLEEP_SPEECH_GRACE_SECONDS = max(0.0, _env_float("SONIOX_SLEEP_SPEECH_GRACE_SECONDS", 0.25))

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
GEMINI_SLEEP_ON_SILENCE = _env_bool("GEMINI_SLEEP_ON_SILENCE", not GEMINI_USES_TEMP_API_KEY)
GEMINI_SLEEP_IDLE_SECONDS = max(1.0, _env_float("GEMINI_SLEEP_IDLE_SECONDS", 30.0))
GEMINI_SLEEP_PRE_ROLL_SECONDS = max(0.0, _env_float("GEMINI_SLEEP_PRE_ROLL_SECONDS", 0.5))
GEMINI_SLEEP_SPEECH_GRACE_SECONDS = max(0.0, _env_float("GEMINI_SLEEP_SPEECH_GRACE_SECONDS", 0.25))

# When enabled, a half-width , . ? or ! in Gemini's returned text (source or
# translation) is converted to its full-width form (，。？！) whenever it sits
# directly between two CJK (Han) characters, e.g. "你好,世界" -> "你好，世界".
# Requiring CJK on both sides avoids touching decimals, URLs, abbreviations, etc.
GEMINI_FULLWIDTH_PUNCT_FIX = _env_bool("GEMINI_FULLWIDTH_PUNCT_FIX", True)

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

# Whether to show edits (diff highlight) between original and refined translation on frontend.
# - True: deleted text in red background + strikethrough; added text in green background
# - False: show final translation only (default)
LLM_REFINE_SHOW_DIFF = _env_bool("LLM_REFINE_SHOW_DIFF", True)

# When diff highlight is enabled, whether to show deleted text.
# - True: current behavior (show deletions with red background + strikethrough)
# - False: show additions only, hide deletions (default)
LLM_REFINE_SHOW_DELETIONS = _env_bool("LLM_REFINE_SHOW_DELETIONS", False)

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


def is_llm_refine_available() -> bool:
    """Whether the backend has enough configuration to use LLM refine feature."""
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
