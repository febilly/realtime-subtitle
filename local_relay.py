"""Relay link for the local (on-device) provider in hosted mode.

Speech is recognized on this machine, so nothing audio-related goes to the
subtitle-server. The session still opens an LLM-only relay session at
`/relay/local/connect` for as long as recognition runs:

* hosted LLM requests (准确 / 混合) ride it exactly like they ride the Soniox or
  Gemini relay socket, so switching modes mid-run needs no new connection;
* the server records the session duration as usage telemetry. It never
  receives recognized text: only `llm.*` frames the user's mode asks for.

Only the `local.start` handshake and `llm.*` frames are ever sent. The link
reconnects with backoff when it drops, and gives up only on errors that a retry
cannot fix (signed out, account blocked, provider not offered).
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Optional

from websockets.sync.client import connect as sync_connect

import config
from relay_errors import RelayConnectionRequestError, relay_error_info

LOCAL_RELAY_MODEL = "local"
_READY_TIMEOUT_SECONDS = 10.0
_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)


class LocalRelayLink:
    def __init__(
        self,
        on_frame: Callable[[Any], None],
        on_state: Callable[[bool, Optional[str]], None],
        run_id: Optional[str] = None,
    ) -> None:
        self._on_frame = on_frame
        # on_state(connected, terminal_tag): terminal_tag is set when the link
        # stopped for good (e.g. "forbidden").
        self._on_state = on_state
        self._run_id = run_id
        self._stop = threading.Event()
        self._ws = None
        self._ws_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="LocalRelayLink", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "user_stop") -> None:
        self._stop.set()
        with self._ws_lock:
            ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close(1000, reason)
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    def send(self, text: str) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("hosted relay is not connected")
        ws.send(text)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            ws = None
            try:
                info = config.relay_connect_info("local", model=LOCAL_RELAY_MODEL, run_id=self._run_id)
                headers = info.get("headers") or {}
                ws = sync_connect(info["url"], additional_headers=headers or None, open_timeout=10)
                ws.send(json.dumps({"type": "local.start", "model": LOCAL_RELAY_MODEL}))
                ready = json.loads(ws.recv(timeout=_READY_TIMEOUT_SECONDS))
                if not isinstance(ready, dict) or ready.get("type") != "local.ready":
                    raise RuntimeError("relay did not acknowledge the local session")
                with self._ws_lock:
                    if self._stop.is_set():
                        ws.close(1000, "user_stop")
                        return
                    self._ws = ws
                failures = 0
                print("🔗 Hosted relay connected (local recognition)")
                self._on_state(True, None)
                for message in ws:
                    if isinstance(message, (bytes, bytearray)):
                        continue
                    try:
                        data = json.loads(message)
                    except ValueError:
                        continue
                    self._on_frame(data)
            except Exception as error:
                if self._stop.is_set():
                    break
                terminal = self._terminal_tag(error)
                print(f"⚠️  Hosted relay link failed: {error}")
                with self._ws_lock:
                    was_connected = self._ws is not None
                    self._ws = None
                if was_connected or terminal:
                    self._on_state(False, terminal)
                if terminal:
                    return
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
            if self._stop.is_set():
                break
            with self._ws_lock:
                was_connected = self._ws is not None
                self._ws = None
            if was_connected:
                # The server closed a healthy link (e.g. node draining).
                self._on_state(False, None)
            delay = _BACKOFF_SECONDS[min(failures, len(_BACKOFF_SECONDS) - 1)]
            failures += 1
            self._stop.wait(delay)

    @staticmethod
    def _terminal_tag(error: BaseException) -> Optional[str]:
        """Tag for failures a reconnect cannot fix; None means retry."""
        if isinstance(error, RelayConnectionRequestError) and error.status_code in (401, 403, 404):
            return "forbidden" if error.status_code != 404 else "model_not_allowed"
        info = relay_error_info(getattr(error, "rcvd", None) or error)
        if info is not None and info[1] and info[0] != "billing_exhausted":
            return info[0]
        return None
