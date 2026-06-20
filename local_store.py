"""Shared local settings store.

A tiny per-user JSON file shared by every local instance. The browser keeps
settings/login in localStorage, which is partitioned per origin (scheme + host
+ port). When a second instance launches it gets a different dynamic port, so
its page sees an empty localStorage and loses the saved login/settings.

To bridge that, the frontend mirrors localStorage into this file (write-through
on change) and hydrates from it on load. Each instance talks only to *its own*
backend, which reads/writes this shared file — so there is no cross-origin
request, no CORS, and no single "host" instance everything depends on.

Values are opaque strings keyed exactly as in localStorage.
"""
import json
import os
import tempfile
import threading

import config

# Guards against concurrent writes *within* this process. Cross-process races
# are made safe by atomic os.replace(); settings writes are user-driven and
# rare, so a lost key in a simultaneous two-instance write is acceptable.
_lock = threading.Lock()


def _path() -> str:
    return config.LOCAL_SETTINGS_FILE


def load() -> dict:
    """Return the full store, or {} if missing/corrupt."""
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception:
        # Corrupt file: treat as empty rather than crashing startup.
        pass
    return {}


def _atomic_write(data: dict) -> None:
    path = _path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=directory or None, prefix=".local_settings_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def merge(updates=None, removals=None) -> dict:
    """Apply key updates/removals atomically; return the new full store."""
    with _lock:
        data = load()
        if isinstance(updates, dict):
            for k, v in updates.items():
                data[str(k)] = "" if v is None else str(v)
        for k in (removals or []):
            data.pop(str(k), None)
        _atomic_write(data)
        return data


def clear() -> dict:
    """Empty the store (used by the frontend "reset all" flow)."""
    with _lock:
        _atomic_write({})
        return {}
