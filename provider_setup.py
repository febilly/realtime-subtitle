"""
Interactive translation-provider selection for first-run startup.

Resolves which backend (soniox | gemini) to use:
1. If TRANSLATION_PROVIDER is already set (env/.env), use it.
2. Otherwise prompt the user on the terminal and persist the choice to .env.
3. Then ensure the chosen provider has a usable API key (delegates to the
   provider-specific key setup, which prompts/validates/saves as needed).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_FILE = ".env"
VALID_PROVIDERS = ("soniox", "gemini")


def _normalize(value) -> str:
    return str(value or "").strip().lower()


def _dotenv_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_translation_provider(provider: str, env_path: Path | None = None) -> None:
    """Append the chosen provider to the current directory .env file."""
    target = Path(ENV_FILE) if env_path is None else env_path
    line = f"TRANSLATION_PROVIDER={_dotenv_quote(provider)}"

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        prefix = "" if existing == "" or existing.endswith(("\n", "\r\n")) else "\n"
        target.write_text(existing + prefix + line + "\n", encoding="utf-8")
        return

    target.write_text(line + "\n", encoding="utf-8")


def _configured_provider_from_env() -> str:
    return _normalize(os.environ.get("TRANSLATION_PROVIDER"))


def _provider_has_key(provider: str) -> bool:
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


def prompt_for_provider() -> str:
    """Ask the user to pick a translation provider on the terminal."""
    print("\nSelect a translation provider:")
    print("  1) soniox  - Soniox real-time STT + translation")
    print("  2) gemini  - Gemini Live Translation")
    while True:
        try:
            choice = input("Enter 1/2 or soniox/gemini: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nProvider selection cancelled.")
            raise

        if choice in ("1", "soniox", "s"):
            return "soniox"
        if choice in ("2", "gemini", "g"):
            return "gemini"
        print("Invalid choice. Please enter 1, 2, soniox, or gemini.")


def ensure_provider_key_available(provider: str) -> None:
    """Ensure the selected provider has a usable key (prompts/validates/saves)."""
    if provider == "soniox":
        from soniox_key_setup import ensure_soniox_key_available
        ensure_soniox_key_available()
    else:
        from gemini_key_setup import ensure_gemini_key_available
        ensure_gemini_key_available()


def resolve_provider() -> str:
    """Resolve the active translation provider and ensure its API key exists.

    Returns one of VALID_PROVIDERS. Must be called before importing ``config`` so
    that os.environ["TRANSLATION_PROVIDER"] is populated when config is evaluated.
    """
    provider = _configured_provider_from_env()

    if provider not in VALID_PROVIDERS:
        if sys.stdin is not None and sys.stdin.isatty():
            provider = prompt_for_provider()
        else:
            # Non-interactive: infer from whichever provider already has a key.
            with_keys = [p for p in VALID_PROVIDERS if _provider_has_key(p)]
            if len(with_keys) == 1:
                provider = with_keys[0]
                print(f"ℹ️  TRANSLATION_PROVIDER not set; using '{provider}' (only provider with a key).")
            else:
                provider = "soniox"
                print("⚠️  TRANSLATION_PROVIDER not set and no interactive terminal; defaulting to 'soniox'.")

        os.environ["TRANSLATION_PROVIDER"] = provider
        try:
            save_translation_provider(provider)
            print(f"✅ Saved TRANSLATION_PROVIDER={provider} to .env")
        except Exception as e:
            print(f"⚠️  Failed to save provider selection to .env: {e}")
    else:
        os.environ["TRANSLATION_PROVIDER"] = provider

    ensure_provider_key_available(provider)
    return provider
