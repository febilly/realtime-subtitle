"""
Soniox API key validation helper.

Key configuration now happens at runtime from the Web UI (Settings panel) and is
held in backend memory; the program never prompts on the terminal nor writes the
key to .env. ``validate_soniox_api_key`` is used by the /setup endpoint to verify
a key before activating it.
"""
from __future__ import annotations

import json
import os
from typing import Callable

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as sync_connect


DEFAULT_SONIOX_WEBSOCKET_URL = "wss://stt-rt.soniox.com/transcribe-websocket"


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
