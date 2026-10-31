"""Client for Yakutan's shared protocol-v1 Qwen ASR / Hy-MT endpoint."""
from __future__ import annotations

import base64
import json
import threading
from urllib.parse import urlsplit

import numpy as np
from websockets.sync.client import connect

PROTOCOL_VERSION = 1


def normalize_server_url(value: str | None) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "ws://" + url
    if url.startswith("http://"):
        url = "ws://" + url[7:]
    elif url.startswith("https://"):
        url = "wss://" + url[8:]
    parsed = urlsplit(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("远程推理地址须为 ws:// 或 wss:// 地址")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("远程推理地址不能包含用户名、密码或片段")
    _ = parsed.port  # Validate malformed/out-of-range ports before connecting.
    return url.rstrip("/")


def _recv_json(ws, timeout: float) -> dict:
    reply = json.loads(ws.recv(timeout=timeout))
    if not isinstance(reply, dict):
        raise RuntimeError("远程推理服务返回了无效消息")
    if reply.get("type") == "error":
        raise RuntimeError(str(reply.get("message") or reply.get("code") or "远程推理失败"))
    return reply


def probe_remote_server(url: str, *, timeout: float = 5.0) -> dict:
    try:
        normalized = normalize_server_url(url)
        if not normalized:
            raise ValueError("远程推理服务器地址为空")
        with connect(normalized, open_timeout=timeout, close_timeout=1) as ws:
            ws.send(json.dumps({"type": "health", "protocol_version": PROTOCOL_VERSION}))
            reply = _recv_json(ws, timeout)
        if reply.get("type") != "health_ok" or reply.get("protocol_version") != PROTOCOL_VERSION:
            raise RuntimeError("远程服务协议不兼容，需要 Yakutan protocol v1 推理服务")
        return reply
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


def remote_readiness_error(status: dict, *, translation_enabled: bool) -> str:
    if status.get("error"):
        return str(status["error"])
    models = status.get("models")
    required = ["qwen3-asr"] + (["hymt2"] if translation_enabled else [])
    if isinstance(models, dict):
        missing = [name for name in required if not isinstance(models.get(name), dict) or not models[name].get("ready")]
        return "远程模型未就绪: " + ", ".join(missing) if missing else ""
    return "" if status.get("ready") else "远程推理服务未就绪"


class RemoteASREngine:
    """Same recognizer interface, using isolated remote connection state."""

    device = "remote"

    def __init__(self, server_url: str, *, timeout: float = 60, corpus_text: str | None = None):
        self.server_url = normalize_server_url(server_url)
        if not self.server_url:
            raise ValueError("远程推理服务器地址为空")
        self.timeout = max(1.0, float(timeout))
        self.language = "auto"
        self._corpus_text = str(corpus_text or "")
        self._context = ""
        self._ws = None
        self._lock = threading.RLock()
        self._request_id = 0
        self._reset_draft_pending = True

    def set_language(self, language: str):
        self.language = language or "auto"

    def set_corpus_text(self, text: str | None):
        self._corpus_text = str(text or "")

    def set_context(self, context: str):
        with self._lock:
            if self._context != str(context or ""):
                self.unload()  # Old servers retain empty-context fallbacks per connection.
            self._context = str(context or "")

    def reset_draft(self):
        with self._lock:
            self._reset_draft_pending = True

    def to_device(self, device: str) -> bool:
        return False

    def unload(self):
        with self._lock:
            ws, self._ws = self._ws, None
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def _connect(self):
        if self._ws is None:
            ws = connect(self.server_url, open_timeout=self.timeout, close_timeout=1)
            try:
                ws.send(json.dumps({"type": "init", "service": "asr", "engine": "qwen3-asr",
                                    "protocol_version": PROTOCOL_VERSION, "sample_rate": 16000}))
                reply = _recv_json(ws, self.timeout)
                if (reply.get("type"), reply.get("service"), reply.get("engine"), reply.get("protocol_version")) != (
                    "init_ok", "asr", "qwen3-asr", PROTOCOL_VERSION,
                ):
                    raise RuntimeError("远程 Qwen ASR 握手不兼容")
            except Exception:
                ws.close()
                raise
            self._ws = ws
        return self._ws

    def transcribe(self, audio: np.ndarray, *, update_context: bool = True) -> dict | None:
        waveform = np.ascontiguousarray(np.asarray(audio, dtype="<f4").reshape(-1))
        if not waveform.size:
            return None
        with self._lock:
            self._request_id += 1
            request = dict(type="transcribe", request_id=self._request_id, audio_format="f32le",
                           sample_rate=16000, audio_base64=base64.b64encode(waveform.tobytes()).decode("ascii"),
                           language=self.language, corpus_text=self._corpus_text, context=self._context,
                           # Never turn completed speech into ASR prompt history.
                           update_context=False, reset_draft=self._reset_draft_pending)
            for attempt in range(2):
                try:
                    ws = self._connect()
                    ws.send(json.dumps(request, ensure_ascii=False))
                    reply = _recv_json(ws, self.timeout)
                    if reply.get("type") != "recognition" or reply.get("request_id") != self._request_id:
                        raise RuntimeError("远程 ASR 响应与请求不匹配")
                    self._reset_draft_pending = bool(update_context)
                    if update_context:
                        if self._context:
                            self.unload()
                        self._context = ""
                    result = reply.get("result")
                    return result if isinstance(result, dict) else None
                except Exception:
                    self.unload()
                    request["reset_draft"] = True
                    if attempt:
                        raise

    def probe_transcribe(self, audio: np.ndarray) -> dict | None:
        # A separate connection makes locator probes independent of the live
        # draft even on the already-deployed Yakutan v1 server.
        probe = RemoteASREngine(self.server_url, timeout=self.timeout, corpus_text=self._corpus_text)
        probe.set_language(self.language)
        probe.set_context(self._context)
        try:
            return probe.transcribe(audio, update_context=False)
        finally:
            probe.unload()
