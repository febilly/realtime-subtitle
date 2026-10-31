import json
import threading
from types import SimpleNamespace

from websockets.sync.server import serve

import local_relay
from local_relay import LocalRelayLink
from relay_errors import RelayConnectionRequestError


def _wait(event, timeout=5.0):
    assert event.wait(timeout), "timed out"


def _fake_config(url=None, error=None):
    def relay_connect_info(provider, model=None, run_id=None):
        assert (provider, model) == ("local", "local")
        if error is not None:
            raise error
        return {"url": url, "headers": {}}
    return SimpleNamespace(relay_connect_info=relay_connect_info)


def test_link_handshakes_relays_llm_frames_and_closes_with_user_stop(monkeypatch):
    received = []
    closed = threading.Event()
    close_info = {}

    def handler(ws):
        start = json.loads(ws.recv())
        received.append(start)
        ws.send(json.dumps({"type": "local.ready"}))
        try:
            for message in ws:
                frame = json.loads(message)
                received.append(frame)
                ws.send(json.dumps({"type": "llm.response", "id": frame["id"], "content": "好"}))
        finally:
            close_info["code"] = ws.close_code
            close_info["reason"] = ws.close_reason
            closed.set()

    with serve(handler, "127.0.0.1", 0) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.socket.getsockname()[1]
        monkeypatch.setattr(local_relay, "config", _fake_config(f"ws://127.0.0.1:{port}/relay/local"))
        connected = threading.Event()
        got_frame = threading.Event()
        frames, states = [], []
        link = LocalRelayLink(
            lambda data: (frames.append(data), got_frame.set()),
            lambda ok, tag: (states.append((ok, tag)), ok and connected.set()),
            run_id="abc123",
        )
        link.start()
        _wait(connected)
        link.send(json.dumps({"type": "llm.request", "id": "r1", "messages": []}))
        _wait(got_frame)
        link.stop()
        _wait(closed)
        server.shutdown()

    assert received[0] == {"type": "local.start", "model": "local"}
    assert received[1]["type"] == "llm.request"
    assert frames == [{"type": "llm.response", "id": "r1", "content": "好"}]
    assert states == [(True, None)]
    assert close_info == {"code": 1000, "reason": "user_stop"}
    assert not link.connected


def test_send_before_connect_raises_so_llm_falls_back():
    link = LocalRelayLink(lambda data: None, lambda ok, tag: None)
    try:
        link.send("{}")
    except RuntimeError:
        pass
    else:
        raise AssertionError("send should fail while disconnected")


def test_signed_out_account_stops_retrying(monkeypatch):
    monkeypatch.setattr(local_relay, "config", _fake_config(error=RelayConnectionRequestError(401, "Invalid API key")))
    done = threading.Event()
    states = []
    link = LocalRelayLink(lambda data: None, lambda ok, tag: (states.append((ok, tag)), done.set()))
    link.start()
    _wait(done)
    link._thread.join(timeout=2)
    assert states == [(False, "forbidden")]
    assert not link._thread.is_alive()


def test_transient_failure_retries(monkeypatch):
    calls = []
    second = threading.Event()

    def relay_connect_info(provider, model=None, run_id=None):
        calls.append(provider)
        if len(calls) >= 2:
            second.set()
        raise RuntimeError("server unreachable")

    monkeypatch.setattr(local_relay, "config", SimpleNamespace(relay_connect_info=relay_connect_info))
    monkeypatch.setattr(local_relay, "_BACKOFF_SECONDS", (0.01,))
    link = LocalRelayLink(lambda data: None, lambda ok, tag: None)
    link.start()
    _wait(second)
    link.stop()
    assert len(calls) >= 2
