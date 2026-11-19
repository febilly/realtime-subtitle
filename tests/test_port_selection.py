import json
import pytest

import realtime_subtitle.core.local_store as local_store
from realtime_subtitle.core.port_selection import (
    DEFAULT_SERVER_PORT,
    FALLBACK_SERVER_PORTS,
    LAST_SERVER_PORT_KEY,
    build_port_candidates,
    load_last_server_port,
    parse_valid_port,
    save_last_server_port,
    select_listening_socket,
)


class _FakeSocket:
    def __init__(self, busy_ports, ephemeral_port=54321):
        self.busy_ports = set(busy_ports)
        self.ephemeral_port = ephemeral_port
        self.bound_port = None
        self.closed = False
        self.listening = False
        self.blocking = True

    def bind(self, addr):
        _host, port = addr
        if port in self.busy_ports:
            raise OSError(f"Port {port} already in use")
        self.bound_port = self.ephemeral_port if port == 0 else port

    def listen(self, _backlog):
        self.listening = True

    def setblocking(self, flag):
        self.blocking = flag

    def getsockname(self):
        return ("127.0.0.1", self.bound_port)

    def close(self):
        self.closed = True


def _fake_bind_factory(busy_ports, tried_ports, ephemeral_port=54321):
    def factory():
        sock = _FakeSocket(busy_ports, ephemeral_port=ephemeral_port)
        orig_bind = sock.bind

        def recording_bind(addr):
            tried_ports.append(addr[1])
            return orig_bind(addr)

        sock.bind = recording_bind
        return sock

    return factory


def test_parse_valid_port_rejects_invalid_values():
    assert parse_valid_port(8080) == 8080
    assert parse_valid_port("8081") == 8081
    assert parse_valid_port(" 65535 ") == 65535
    assert parse_valid_port(1) == 1

    for bad in (None, True, False, 0, "0", -1, "-5", 65536, "65536", "", "abc", "8080.5", 8080.5):
        assert parse_valid_port(bad) is None


def test_build_port_candidates_default_port_deduplicates_and_orders():
    # No last port
    assert build_port_candidates(8080, None) == [8080, 8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089, 0]
    # Last port outside fallback range comes right after 8080
    assert build_port_candidates(8080, "18080") == [8080, 18080, 8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089, 0]
    # Last port inside fallback range is promoted before 8081 and not duplicated
    assert build_port_candidates(8080, 8083) == [8080, 8083, 8081, 8082, 8084, 8085, 8086, 8087, 8088, 8089, 0]
    # Last port equal to 8080 is not duplicated
    assert build_port_candidates(8080, 8080) == [8080, 8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089, 0]
    # Invalid last port is ignored
    assert build_port_candidates(8080, "bad") == [8080, 8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089, 0]


def test_select_port_8080_available():
    tried = []
    candidates = build_port_candidates(DEFAULT_SERVER_PORT, last_port=8085)
    sock, port = select_listening_socket(
        "127.0.0.1",
        candidates,
        socket_factory=_fake_bind_factory(busy_ports=set(), tried_ports=tried),
    )
    assert port == 8080
    assert tried == [8080]
    assert sock.listening is True
    assert sock.blocking is False


def test_select_port_8080_busy_but_last_port_available():
    tried = []
    candidates = build_port_candidates(DEFAULT_SERVER_PORT, last_port="8085")
    sock, port = select_listening_socket(
        "127.0.0.1",
        candidates,
        socket_factory=_fake_bind_factory(busy_ports={8080}, tried_ports=tried),
    )
    assert port == 8085
    assert tried == [8080, 8085]
    assert sock.listening is True


def test_select_port_falls_back_to_8081_range_when_8080_and_last_busy():
    tried = []
    candidates = build_port_candidates(DEFAULT_SERVER_PORT, last_port=18080)
    sock, port = select_listening_socket(
        "127.0.0.1",
        candidates,
        socket_factory=_fake_bind_factory(busy_ports={8080, 18080, 8081}, tried_ports=tried),
    )
    assert port == 8082
    assert tried == [8080, 18080, 8081, 8082]
    assert sock.listening is True


def test_select_port_falls_back_to_ephemeral_zero_when_all_fixed_busy():
    tried = []
    busy = {8080, 18080, *FALLBACK_SERVER_PORTS}
    candidates = build_port_candidates(DEFAULT_SERVER_PORT, last_port=18080)
    sock, port = select_listening_socket(
        "127.0.0.1",
        candidates,
        socket_factory=_fake_bind_factory(busy_ports=busy, tried_ports=tried, ephemeral_port=49152),
    )
    assert port == 49152
    assert tried[-1] == 0
    assert tried == [8080, 18080, *FALLBACK_SERVER_PORTS, 0]
    assert sock.listening is True


def test_explicit_non_default_port_skips_sticky_and_fallback_candidates():
    assert build_port_candidates(9000, last_port=8085) == [9000, 0]

    tried = []
    _sock, port = select_listening_socket(
        "127.0.0.1",
        build_port_candidates(9000, last_port=8085),
        socket_factory=_fake_bind_factory(busy_ports={9000}, tried_ports=tried, ephemeral_port=51111),
    )
    assert tried == [9000, 0]
    assert port == 51111


def test_server_port_zero_uses_ephemeral_directly():
    assert build_port_candidates(0, last_port=8085) == [0]

    tried = []
    _sock, port = select_listening_socket(
        "127.0.0.1",
        build_port_candidates(0, last_port=8085),
        socket_factory=_fake_bind_factory(busy_ports=set(), tried_ports=tried, ephemeral_port=52222),
    )
    assert tried == [0]
    assert port == 52222


def test_last_server_port_persistence_and_failure_resilience(tmp_path, monkeypatch):
    store_path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(store_path), raising=False)

    assert load_last_server_port() is None
    assert save_last_server_port(8083) is True
    assert load_last_server_port() == 8083
    assert json.loads(store_path.read_text(encoding="utf-8"))[LAST_SERVER_PORT_KEY] == "8083"

    # Invalid stored value returns None safely
    store_path.write_text(json.dumps({LAST_SERVER_PORT_KEY: "not-a-port"}), encoding="utf-8")
    assert load_last_server_port() is None

    # IO errors on load/save never raise
    monkeypatch.setattr(local_store, "load", lambda: (_ for _ in ()).throw(PermissionError("locked")))
    assert load_last_server_port() is None

    monkeypatch.setattr(local_store, "merge", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")))
    assert save_last_server_port(8084) is False
