"""
Gemini API key validation helper.

Key configuration now happens at runtime from the Web UI (Settings panel) and is
held in backend memory; the program never prompts on the terminal nor writes the
key to .env. ``validate_gemini_api_key`` is used by the /setup endpoint to verify
a key before activating it.
"""
from __future__ import annotations


def validate_gemini_api_key(api_key: str, *, validate_func=None) -> tuple[bool, str | None]:
    """Check whether the key is accepted by the Gemini API."""
    if validate_func is None:
        from gemini_client import validate_api_key as validate_func
    return validate_func(api_key)
