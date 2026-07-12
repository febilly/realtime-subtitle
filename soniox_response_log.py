"""Optional JSONL capture of raw Soniox response batches.

Disabled by default. Set ``SONIOX_RESPONSE_LOG=1`` to write one record per
processed Soniox WebSocket response under ``logs/soniox-responses/``. Logging
is diagnostic-only and must never interrupt recognition.
"""

import atexit
import json
import os
import secrets
import threading
import time
from datetime import datetime


_lock = threading.RLock()
_file = None
_enabled: bool | None = None


def _env_enabled() -> bool:
    # A developer may keep capture enabled in their local .env. Never let a
    # normal test run fill logs with synthetic response batches.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    value = os.environ.get("SONIOX_RESPONSE_LOG")
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _open_log_file():
    logs_dir = os.path.join(os.getcwd(), "logs", "soniox-responses")
    os.makedirs(logs_dir, exist_ok=True)
    filename = (
        f"responses_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{os.getpid()}_{time.time_ns()}_{secrets.token_hex(4)}.jsonl"
    )
    path = os.path.join(logs_dir, filename)
    handle = open(path, "x", encoding="utf-8")
    print(f"📝 Soniox response log: {path}")
    return handle


def log_response(response: dict, **context) -> None:
    """Append one raw parsed response plus the caller's runtime context."""
    global _file, _enabled
    try:
        if _enabled is None:
            _enabled = _env_enabled()
        if not _enabled:
            return
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": "soniox_response",
            **context,
            "response": response,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            if _file is None:
                _file = _open_log_file()
            _file.write(line + "\n")
            _file.flush()
    except Exception:
        close()
        _enabled = False


def close() -> None:
    global _file
    with _lock:
        handle = _file
        _file = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


atexit.register(close)
