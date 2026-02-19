"""
Configuration file - stores all configuration options and constants.
"""
import os
import sys
import locale
import time
import threading
from dotenv import load_dotenv


# Soniox-supported languages (ISO 639-1), used to validate system/target language.
# Source: docs/supported-languages.mdx
SUPPORTED_LANGUAGE_CODES = {
    "af", "sq", "ar", "az", "eu", "be", "bn", "bs", "bg", "ca",
    "zh", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "gl",
    "de", "el", "gu", "he", "hi", "hu", "id", "it", "ja", "kn",
    "kk", "ko", "lv", "lt", "mk", "ms", "ml", "mr", "no", "fa",
    "pl", "pt", "pa", "ro", "ru", "sr", "sk", "sl", "es", "sw",
    "sv", "tl", "ta", "te", "th", "tr", "uk", "ur", "vi", "cy",
}


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


def is_supported_language_code(lang: str) -> bool:
    code = normalize_language_code(lang)
    return bool(code) and code in SUPPORTED_LANGUAGE_CODES

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


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None else str(value)

# Soniox API configuration
SONIOX_WEBSOCKET_URL = _env_str("SONIOX_WEBSOCKET_URL", "wss://stt-rt.soniox.com/transcribe-websocket")
SONIOX_TEMP_KEY_URL = os.environ.get("SONIOX_TEMP_KEY_URL")

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

# Speaker diarization switch (enabled by default)
# True: enable diarization (frontend shows speaker labels)
# False: disable diarization (frontend hides speaker labels)
ENABLE_SPEAKER_DIARIZATION = _env_bool("ENABLE_SPEAKER_DIARIZATION", True)

# Hide speaker labels (disabled by default)
# True: frontend hides speaker index labels (even if diarization is enabled)
# False: show speaker labels normally
HIDE_SPEAKER_LABELS = _env_bool("HIDE_SPEAKER_LABELS", False)

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


def get_system_language() -> str:
    """
    Get the system language code.
    Returns an ISO 639-1 two-letter code (e.g. 'zh', 'en', 'ja', 'ko').
    """
    try:
        # Get system locale
        system_locale = locale.getdefaultlocale()[0]  # e.g. 'zh_CN', 'en_US', 'ja_JP'
        
        if system_locale:
            # Extract language code (first two letters)
            lang_code = normalize_language_code(system_locale)
            if is_supported_language_code(lang_code):
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
    normalized_target = normalize_language_code(TARGET_LANG)
    if is_supported_language_code(normalized_target):
        TRANSLATION_TARGET_LANG = normalized_target
    else:
        print(f"⚠️  Config TARGET_LANG not supported: {TARGET_LANG} -> {normalized_target}, fallback to: en")
        TRANSLATION_TARGET_LANG = "en"

print(f"✅ Translation target language set to: {TRANSLATION_TARGET_LANG}")

# Hard validation: exit if neither permanent API key nor temp key URL is provided.
if not os.environ.get("SONIOX_API_KEY") and not SONIOX_TEMP_KEY_URL:
    print("❌ Configuration error: neither SONIOX_API_KEY nor SONIOX_TEMP_KEY_URL is set.\nPlease set one of them in your environment or in the .env file.")
    input("Press Enter to exit...")
    sys.exit(1)
