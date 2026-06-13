"""
Interactive Gemini API key setup for first-run startup.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path

ENV_FILE = ".env"


def _env_has_value(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _dotenv_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_gemini_api_key(api_key: str, env_path: Path | None = None) -> None:
    """Append a verified key to the current directory .env file."""
    target = Path(ENV_FILE) if env_path is None else env_path
    line = f"GEMINI_API_KEY={_dotenv_quote(api_key)}"

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        prefix = "" if existing == "" or existing.endswith(("\n", "\r\n")) else "\n"
        target.write_text(existing + prefix + line + "\n", encoding="utf-8")
        return

    target.write_text(line + "\n", encoding="utf-8")


def validate_gemini_api_key(api_key: str, *, validate_func=None) -> tuple[bool, str | None]:
    """Check whether the key is accepted by the Gemini API."""
    if validate_func is None:
        from gemini_client import validate_api_key as validate_func
    return validate_func(api_key)


def ensure_gemini_key_available() -> None:
    """Prompt for a permanent API key when neither permanent nor temp key is configured."""
    if _env_has_value("GEMINI_API_KEY") or _env_has_value("GEMINI_TEMP_KEY_URL"):
        return

    print("❌ GEMINI_API_KEY and GEMINI_TEMP_KEY_URL are both missing.")
    print("Please enter a Gemini API key (https://aistudio.google.com/apikey).")
    print("It will be validated and saved to .env.")

    while True:
        try:
            api_key = getpass.getpass("Gemini API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAPI key input cancelled.")
            raise

        if not api_key:
            print("API key cannot be empty. Please try again.")
            continue

        print("Validating Gemini API key...")
        is_valid, error = validate_gemini_api_key(api_key)
        if not is_valid:
            print(f"❌ Gemini API key validation failed: {error or 'unknown error'}")
            print("Please enter a valid Gemini API key.")
            continue

        os.environ["GEMINI_API_KEY"] = api_key
        save_gemini_api_key(api_key)
        print("✅ Gemini API key validated and saved to .env.")
        return
