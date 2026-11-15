"""Network request logging used by ``server.py --debug``.

The logger intentionally prints request lines and status/timing only. Query
values that commonly contain credentials are redacted before they reach stdout.
"""
from __future__ import annotations

import functools
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_enabled = False
_patched = False
_original_requests_request = None
_original_aiohttp_request = None
_original_ws_sync_connect = None

_SENSITIVE_QUERY_PARTS = (
    "api_key",
    "apikey",
    "access_token",
    "auth",
    "bearer",
    "code",
    "credential",
    "key",
    "password",
    "secret",
    "state",
    "token",
)


def enable() -> None:
    """Enable debug logging and patch common Python HTTP/WebSocket clients."""
    global _enabled
    _enabled = True
    _patch_clients()
    print("[NET] Outbound network debug logging enabled", flush=True)


def is_enabled() -> bool:
    return _enabled


def sanitize_url(url: Any) -> str:
    raw = str(url)
    try:
        split = urlsplit(raw)
    except Exception:
        return raw

    query_items = []
    for key, value in parse_qsl(split.query, keep_blank_values=True):
        query_items.append((key, "***" if _is_sensitive_key(key) else value))

    netloc = split.netloc
    if split.username or split.password:
        host = split.hostname or ""
        if split.port:
            host = f"{host}:{split.port}"
        netloc = f"***:***@{host}"

    return urlunsplit(
        (
            split.scheme,
            netloc,
            split.path,
            urlencode(query_items, doseq=True, safe="*"),
            split.fragment,
        )
    )


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(part in lowered for part in _SENSITIVE_QUERY_PARTS)


def _format_ms(started_at: float) -> str:
    return f"{(time.perf_counter() - started_at) * 1000:.1f}ms"


def _print(message: str) -> None:
    if _enabled:
        print(message, flush=True)


def _log_outgoing_start(kind: str, method: str, url: Any) -> float:
    _print(f"[NET] {kind} -> {method} {sanitize_url(url)}")
    return time.perf_counter()


def _log_outgoing_finish(kind: str, method: str, url: Any, status: Any, started_at: float) -> None:
    _print(f"[NET] {kind} <- {method} {sanitize_url(url)} {status} {_format_ms(started_at)}")


def _log_outgoing_error(kind: str, method: str, url: Any, error: BaseException, started_at: float) -> None:
    _print(
        f"[NET] {kind} !! {method} {sanitize_url(url)} "
        f"{type(error).__name__}: {error} {_format_ms(started_at)}"
    )


def _patch_clients() -> None:
    global _patched
    if _patched:
        return
    _patch_requests()
    _patch_aiohttp()
    _patch_websockets_sync()
    _patched = True


def _patch_requests() -> None:
    global _original_requests_request
    try:
        import requests
    except Exception:
        return

    if _original_requests_request is not None:
        return

    _original_requests_request = requests.sessions.Session.request

    @functools.wraps(_original_requests_request)
    def request_wrapper(self, method, url, *args, **kwargs):
        started_at = _log_outgoing_start("HTTP", str(method).upper(), url)
        try:
            response = _original_requests_request(self, method, url, *args, **kwargs)
        except Exception as error:
            _log_outgoing_error("HTTP", str(method).upper(), url, error, started_at)
            raise
        _log_outgoing_finish("HTTP", str(method).upper(), url, response.status_code, started_at)
        return response

    requests.sessions.Session.request = request_wrapper


def _patch_aiohttp() -> None:
    global _original_aiohttp_request
    try:
        import aiohttp
    except Exception:
        return

    if _original_aiohttp_request is not None:
        return

    _original_aiohttp_request = aiohttp.ClientSession._request

    @functools.wraps(_original_aiohttp_request)
    async def request_wrapper(self, method, str_or_url, *args, **kwargs):
        started_at = _log_outgoing_start("HTTP", str(method).upper(), str_or_url)
        try:
            response = await _original_aiohttp_request(self, method, str_or_url, *args, **kwargs)
        except Exception as error:
            _log_outgoing_error("HTTP", str(method).upper(), str_or_url, error, started_at)
            raise
        _log_outgoing_finish("HTTP", str(method).upper(), str_or_url, response.status, started_at)
        return response

    aiohttp.ClientSession._request = request_wrapper


def _patch_websockets_sync() -> None:
    global _original_ws_sync_connect
    try:
        import websockets.sync.client as ws_sync_client
    except Exception:
        return

    if _original_ws_sync_connect is not None:
        return

    _original_ws_sync_connect = ws_sync_client.connect

    @functools.wraps(_original_ws_sync_connect)
    def connect_wrapper(uri, *args, **kwargs):
        started_at = _log_outgoing_start("WS  ", "CONNECT", uri)
        try:
            connection = _original_ws_sync_connect(uri, *args, **kwargs)
        except Exception as error:
            _log_outgoing_error("WS  ", "CONNECT", uri, error, started_at)
            raise
        _log_outgoing_finish("WS  ", "CONNECT", uri, "connected", started_at)
        return connection

    ws_sync_client.connect = connect_wrapper
