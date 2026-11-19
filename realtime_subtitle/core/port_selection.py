"""Server port candidate ordering and sticky-port persistence helpers."""
from __future__ import annotations

import socket
from typing import Callable, Iterable

import realtime_subtitle.core.local_store as local_store

DEFAULT_SERVER_PORT = 8080
FALLBACK_SERVER_PORTS = tuple(range(8081, 8090))
LAST_SERVER_PORT_KEY = "last_server_port"


def parse_valid_port(value) -> int | None:
    """Return a valid non-ephemeral TCP port (1..65535), or None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        port = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.isdigit():
            return None
        port = int(stripped)
    else:
        return None

    if 1 <= port <= 65535:
        return port
    return None


def build_port_candidates(
    preferred_port: int | None,
    last_port=None,
    *,
    default_port: int = DEFAULT_SERVER_PORT,
    fallback_ports: Iterable[int] = FALLBACK_SERVER_PORTS,
) -> list[int]:
    """Return ordered, deduplicated candidate ports for server binding.

    - ``preferred_port == 0`` (or invalid/non-positive) preserves ephemeral port selection: ``[0]``.
    - Explicit non-default ``preferred_port`` skips sticky/fallback ports: ``[preferred_port, 0]``.
    - Default ``preferred_port`` (8080) tries:
      1. ``default_port`` (8080)
      2. ``last_port`` (if valid and distinct)
      3. ``fallback_ports`` (8081-8089, deduplicated)
      4. ``0`` (OS-assigned ephemeral port)
    """
    if isinstance(preferred_port, bool) or preferred_port is None:
        return [0]
    try:
        preferred = int(preferred_port)
    except (TypeError, ValueError):
        return [0]

    if preferred <= 0 or preferred > 65535:
        return [0]

    if preferred != default_port:
        return [preferred, 0]

    candidates = [default_port]
    seen = {default_port}

    parsed_last = parse_valid_port(last_port)
    if parsed_last is not None and parsed_last not in seen:
        candidates.append(parsed_last)
        seen.add(parsed_last)

    for raw_port in fallback_ports:
        parsed_fallback = parse_valid_port(raw_port)
        if parsed_fallback is not None and parsed_fallback not in seen:
            candidates.append(parsed_fallback)
            seen.add(parsed_fallback)

    candidates.append(0)
    return candidates


def select_listening_socket(
    host: str,
    candidates: Iterable[int],
    socket_factory: Callable[[], socket.socket] | None = None,
) -> tuple[socket.socket, int]:
    """Bind the first available candidate port and return ``(socket, actual_port)``."""
    make_socket = socket_factory or (
        lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    )
    last_error: OSError | None = None
    for port in candidates:
        sock = make_socket()
        try:
            sock.bind((host, port))
            sock.listen(128)
            sock.setblocking(False)
            actual_port = sock.getsockname()[1]
            return sock, actual_port
        except OSError as error:
            last_error = error
            try:
                sock.close()
            except Exception:
                pass
            continue

    raise last_error if last_error else RuntimeError("Failed to allocate listening socket")


def load_last_server_port() -> int | None:
    """Load and validate the last successfully bound port from local_store."""
    try:
        store = local_store.load()
    except Exception:
        return None
    if not isinstance(store, dict):
        return None
    return parse_valid_port(store.get(LAST_SERVER_PORT_KEY))


def save_last_server_port(port: int) -> bool:
    """Persist the bound port to local_store; failures never abort startup."""
    valid = parse_valid_port(port)
    if valid is None:
        return False
    try:
        local_store.merge({LAST_SERVER_PORT_KEY: valid})
        return True
    except Exception:
        return False
