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
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager

import config

logger = logging.getLogger(__name__)

# Atomic replacement protects readers; the write lock also protects concurrent
# read/modify/write operations from other app instances.
_lock = threading.Lock()


@contextmanager
def _write_lock():
    """Serialize read/modify/write across app processes as well as threads."""
    with _lock:
        lock_path = _path() + ".lock"
        os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                deadline = time.monotonic() + 5
                while True:
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.01)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            acquired = True
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _path() -> str:
    return config.LOCAL_SETTINGS_FILE


def load(max_retries: int = 3, base_delay: float = 0.05) -> dict:
    """Return the full store, or {} if missing/corrupt.

    Distinguishes three cases:
    1. FileNotFoundError: store does not exist yet; returns {}.
    2. json.JSONDecodeError / UnicodeDecodeError: real corruption; backs up the
       corrupt file to <path>.corrupt-<timestamp>, logs a warning, and returns {}.
    3. Transient OSError / PermissionError (e.g. file lock by antivirus, indexer,
       or concurrent atomic write): retries with exponential backoff. If all
       retries fail, raises the OSError so callers can abort safely without
       overwriting/wiping settings.
    """
    path = _path()
    for attempt in range(max_retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning(
                f"Settings file {path} did not contain a JSON object, treating as empty"
            )
            return {}
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Corrupt JSON in settings file {path}: {e}")
            try:
                timestamp = int(time.time())
                corrupt_path = f"{path}.corrupt-{timestamp}"
                if os.path.exists(corrupt_path):
                    corrupt_path = f"{path}.corrupt-{timestamp}-{time.time_ns()}"
                os.replace(path, corrupt_path)
                logger.warning(f"Backed up corrupt settings file to {corrupt_path}")
            except Exception as backup_err:
                logger.warning(
                    f"Failed to backup corrupt settings file {path}: {backup_err}"
                )
            return {}
        except OSError as e:
            if attempt == max_retries - 1:
                logger.warning(
                    f"Failed to read settings file {path} after {max_retries} attempts: {e}"
                )
                raise
            time.sleep(base_delay * (2 ** attempt))
    return {}


def _replace_with_retry(
    src: str, dst: str, max_retries: int = 3, base_delay: float = 0.05
) -> None:
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if attempt == max_retries - 1:
                logger.warning(
                    f"Failed to replace {dst} after {max_retries} attempts: {e}"
                )
                raise
            time.sleep(base_delay * (2 ** attempt))


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
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def merge(updates=None, removals=None) -> dict:
    """Apply key updates/removals atomically; return the new full store.

    If loading existing settings fails due to transient IO errors, aborts without
    writing so existing settings are never wiped.
    """
    with _write_lock():
        try:
            data = load()
        except OSError as e:
            logger.warning(f"Aborting merge: failed to read settings store: {e}")
            raise
        if isinstance(updates, dict):
            for k, v in updates.items():
                data[str(k)] = "" if v is None else str(v)
        for k in (removals or []):
            data.pop(str(k), None)
        _atomic_write(data)
        return data


def clear() -> dict:
    """Empty the store (used by the frontend "reset all" flow)."""
    with _write_lock():
        try:
            _atomic_write({})
            return {}
        except OSError as e:
            logger.warning(f"Failed to clear settings store: {e}")
            raise


def transaction(callback):
    """Read/modify/write backend-owned state without racing settings writes."""
    with _write_lock():
        data = load()
        before = dict(data)
        result = callback(data)
        if data != before:
            _atomic_write(data)
        return result
