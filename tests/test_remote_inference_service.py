"""Loopback protocol tests for the shared remote inference service.

All model-facing objects are fakes: these tests exercise only WebSocket frames
and prompt construction, never model loading or the gpu4038 deployment.
"""

from __future__ import annotations

import asyncio
import base64
import json

import numpy as np
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from remote_inference_service import server


class FakeASRManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def transcribe(self, engine, state, audio, payload):
        self.calls.append({
            "engine": engine,
            "context": state.context,
            "audio": audio.copy(),
            "payload": payload,
        })
        state.context = "new context"
        return {"text": "recognized text"}, state.context, {"queue_ms": 1.0, "run_ms": 2.0}


class FakeHyMT2Backend:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "翻译结果"


async def _with_server(callback, *, ready: bool = True):
    manager = FakeASRManager()
    backend = FakeHyMT2Backend()
    handler = server.make_connection_handler(
        {"qwen3-asr": {"ready": ready, "missing": []}, "hymt2": {"ready": ready, "missing": []}},
        manager,
        backend,
        history_limit=8,
    )
    async with serve(handler, "127.0.0.1", 0, max_size=1024 * 1024) as websocket_server:
        port = websocket_server.sockets[0].getsockname()[1]
        await callback(f"ws://127.0.0.1:{port}", manager, backend)


def test_health_uses_websocket_protocol_and_reports_model_readiness():
    async def scenario(url, _manager, _backend):
        async with connect(url) as ws:
            await ws.send(json.dumps({"type": "health"}))
            reply = json.loads(await ws.recv())
        assert reply["type"] == "health_ok"
        assert reply["protocol_version"] == 1
        assert reply["ready"] is True
        assert reply["models"]["qwen3-asr"]["ready"] is True

    asyncio.run(_with_server(scenario))


def test_asr_init_and_transcribe_round_trip_over_loopback_websocket():
    async def scenario(url, manager, _backend):
        audio = np.array([0.25, -0.5], dtype="<f4")
        async with connect(url) as ws:
            await ws.send(json.dumps({"type": "init", "service": "asr", "engine": "qwen3-asr"}))
            assert json.loads(await ws.recv()) == {
                "type": "init_ok", "service": "asr", "protocol_version": 1,
                "engine": "qwen3-asr", "sample_rate": 16000,
            }
            await ws.send(json.dumps({
                "type": "transcribe", "request_id": "request-7", "audio_format": "f32le",
                "sample_rate": 16000, "audio_base64": base64.b64encode(audio.tobytes()).decode(),
                "language": "en", "context": "old context",
            }))
            reply = json.loads(await ws.recv())
        assert reply == {
            "type": "recognition", "request_id": "request-7", "engine": "qwen3-asr",
            "result": {"text": "recognized text"}, "context": "new context",
            "timing": {"queue_ms": 1.0, "run_ms": 2.0},
        }
        assert len(manager.calls) == 1
        assert manager.calls[0]["engine"] == "qwen3-asr"
        assert manager.calls[0]["payload"]["language"] == "en"
        assert np.array_equal(manager.calls[0]["audio"], audio)

    asyncio.run(_with_server(scenario))


def test_hymt_default_init_accepts_yakutan_payload_and_protects_following_context():
    async def scenario(url, _manager, backend):
        async with connect(url) as ws:
            # Deliberately omit service: old Yakutan/Realtime Subtitle Hy-MT2
            # clients rely on the default service being hymt2.
            await ws.send(json.dumps({"type": "init", "source_lang": "en", "target_lang": "zh"}))
            init = json.loads(await ws.recv())
            assert init["type"] == "init_ok"
            assert init["service"] == "hymt2"
            assert init["hypothesis_mode"] == "sentence_revision"
            assert init["source_token_join_mode"] == "verbatim"

            await ws.send(json.dumps({
                "type": "update", "seq": 9,
                "words": [["Hello "], ["world"]], "tail": {"words": [["!"]]},
                "source_lang": "en", "target_lang": "zh", "is_final": True,
                "previous_source": "Older revision", "previous_translation": "旧译文",
                "history": [["Completed source", "完成译文"], "Legacy source"],
                "following_source": "The following row is context only.",
            }))
            reply = json.loads(await ws.recv())
        assert reply["type"] == "translation"
        assert reply["seq"] == 9
        assert reply["committed_text"] == "翻译结果"
        assert reply["covered_source_units"] == len("Hello world!")
        assert reply["stop_reason"] == "final"
        assert backend.prompts
        prompt = backend.prompts[0]
        assert "[Source Text]\nHello world!" in prompt
        assert "Completed source\n=> 完成译文" in prompt
        assert "Legacy source" in prompt
        assert "Upcoming source context (reference only; do not translate this text):" in prompt
        assert "The following row is context only." in prompt

    asyncio.run(_with_server(scenario))
