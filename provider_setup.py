"""
Non-interactive translation-provider resolution for startup.

Resolves which backend (soniox | gemini) to use, without prompting the terminal
and without writing to .env. Resolution order:
1. If TRANSLATION_PROVIDER is set (env/.env/CLI), use it.
2. Otherwise infer from whichever provider already has a usable env key.
3. Otherwise default to "soniox".

The actual API key is configured at runtime from the Web UI (Settings panel) and
held in backend memory; env keys act as a read-only fallback.
"""
from __future__ import annotations

import os

VALID_PROVIDERS = ("soniox", "gemini")


def _normalize(value) -> str:
    return str(value or "").strip().lower()


def _configured_provider_from_env() -> str:
    return _normalize(os.environ.get("TRANSLATION_PROVIDER"))


def provider_has_env_key(provider: str) -> bool:
    """Whether the given provider has a usable key/temp-key-url in the environment."""
    if provider == "soniox":
        return bool(
            os.environ.get("SONIOX_API_KEY", "").strip()
            or os.environ.get("SONIOX_TEMP_KEY_URL", "").strip()
        )
    if provider == "gemini":
        return bool(
            os.environ.get("GEMINI_API_KEY", "").strip()
            or os.environ.get("GEMINI_TEMP_KEY_URL", "").strip()
        )
    return False


def resolve_provider() -> str:
    """Resolve the active translation provider without any interactive prompts.

    Returns one of VALID_PROVIDERS. Must be called before importing ``config`` so
    that os.environ["TRANSLATION_PROVIDER"] is populated when config is evaluated.
    """
    provider = _configured_provider_from_env()

    if provider not in VALID_PROVIDERS:
        with_keys = [p for p in VALID_PROVIDERS if provider_has_env_key(p)]
        if len(with_keys) == 1:
            provider = with_keys[0]
            print(f"ℹ️  TRANSLATION_PROVIDER not set; using '{provider}' (only provider with a key).")
        else:
            provider = "soniox"

    os.environ["TRANSLATION_PROVIDER"] = provider
    return provider
