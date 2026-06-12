"""
Interactive Soniox API key setup for first-run startup.
"""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from typing import Callable

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as sync_connect


DEFAULT_SONIOX_WEBSOCKET_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
ENV_FILE = ".env"


def _env_has_value(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _dotenv_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_soniox_api_key(api_key: str, env_path: Path | None = None) -> None:
    """Append a verified key to the current directory .env file."""
    target = Path(ENV_FILE) if env_path is None else env_path
    line = f"SONIOX_API_KEY={_dotenv_quote(api_key)}"

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        prefix = "" if existing == "" or existing.endswith(("\n", "\r\n")) else "\n"
        target.write_text(existing + prefix + line + "\n", encoding="utf-8")
        return

    target.write_text(line + "\n", encoding="utf-8")


def validate_soniox_api_key(
    api_key: str,
    websocket_url: str | None = None,
    *,
    connect_func: Callable | None = None,
    recv_timeout: float = 5.0,
) -> tuple[bool, str | None]:
    """Open a Soniox stream briefly to check whether the key is accepted."""
    url = websocket_url or os.environ.get("SONIOX_WEBSOCKET_URL") or DEFAULT_SONIOX_WEBSOCKET_URL
    connect_impl = sync_connect if connect_func is None else connect_func
    config = {
        "api_key": api_key,
        "model": "stt-rt-v4",
        "audio_format": "pcm_s16le",
        "sample_rate": 16000,
        "num_channels": 1,
        "enable_endpoint_detection": True,
    }

    try:
        with connect_impl(url) as ws:
            ws.send(json.dumps(config))
            try:
                message = ws.recv(timeout=recv_timeout)
            except TimeoutError:
                return True, None

            try:
                response = json.loads(message)
            except Exception:
                return True, None

            if response.get("error_code") is not None:
                error_message = response.get("error_message") or response.get("error_code")
                return False, str(error_message)

            return True, None
    except ConnectionClosed as error:
        reason = getattr(error, "reason", "") or str(error)
        return False, reason
    except Exception as error:
        return False, str(error)


def ensure_soniox_key_available() -> None:
    """Prompt for a permanent API key when neither permanent nor temp key is configured."""
    if _env_has_value("SONIOX_API_KEY") or _env_has_value("SONIOX_TEMP_KEY_URL"):
        return

    print("❌ SONIOX_API_KEY and SONIOX_TEMP_KEY_URL are both missing.")
    print("Please enter a Soniox API key. It will be validated and saved to .env.")

    while True:
        try:
            api_key = getpass.getpass("Soniox API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAPI key input cancelled.")
            raise

        if not api_key:
            print("API key cannot be empty. Please try again.")
            continue

        print("Validating Soniox API key...")
        is_valid, error = validate_soniox_api_key(api_key)
        if not is_valid:
            print(f"❌ Soniox API key validation failed: {error or 'unknown error'}")
            print("Please enter a valid Soniox API key.")
            continue

        os.environ["SONIOX_API_KEY"] = api_key
        save_soniox_api_key(api_key)
        print("✅ Soniox API key validated and saved to .env.")
        return
