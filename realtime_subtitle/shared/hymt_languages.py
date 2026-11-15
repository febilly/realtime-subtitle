"""Hy-MT2 base target languages, independent of Qwen ASR source coverage.

Source: https://huggingface.co/tencent/Hy-MT2-1.8B#supported-languages
The official table has 38 entries, including Traditional Chinese and Cantonese.
Filipino uses the application's `fil` code; the official `tl` is an alias.
This catalog describes base-model coverage, not StreamRevise quality validation.
"""

HYMT2_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "fr": "French",
    "pt": "Portuguese",
    "es": "Spanish",
    "ja": "Japanese",
    "tr": "Turkish",
    "ru": "Russian",
    "ar": "Arabic",
    "ko": "Korean",
    "th": "Thai",
    "it": "Italian",
    "de": "German",
    "vi": "Vietnamese",
    "ms": "Malay",
    "id": "Indonesian",
    "fil": "Filipino",
    "hi": "Hindi",
    "zh-hant": "Chinese (Traditional)",
    "pl": "Polish",
    "cs": "Czech",
    "nl": "Dutch",
    "km": "Khmer",
    "my": "Burmese",
    "fa": "Persian",
    "gu": "Gujarati",
    "ur": "Urdu",
    "te": "Telugu",
    "mr": "Marathi",
    "he": "Hebrew",
    "bn": "Bengali",
    "ta": "Tamil",
    "uk": "Ukrainian",
    "bo": "Tibetan",
    "kk": "Kazakh",
    "mn": "Mongolian",
    "ug": "Uyghur",
    "yue": "Cantonese",
}

_PRIORITY = ["en", "zh", "ja", "ko"]
HYMT2_LANGUAGE_CODES_ORDERED = _PRIORITY + sorted(
    (code for code in HYMT2_LANGUAGE_NAMES if code not in _PRIORITY),
    key=lambda code: HYMT2_LANGUAGE_NAMES[code].casefold(),
)


def normalize_hymt_language(code: str | None) -> str:
    """Normalize aliases/regions while retaining the Traditional Chinese target."""
    raw = str(code or "").strip().lower().replace("_", "-").split(".", 1)[0]
    if not raw or raw == "auto":
        return ""
    parts = raw.split("-")
    if parts[0] == "zh":
        if "hans" in parts:
            return "zh"
        return "zh-hant" if any(part in {"hant", "tw", "hk", "mo"} for part in parts) else "zh"
    return "fil" if parts[0] == "tl" else parts[0]


def hymt_language_name(code: str | None) -> str:
    normalized = normalize_hymt_language(code)
    return HYMT2_LANGUAGE_NAMES.get(normalized, normalized)
