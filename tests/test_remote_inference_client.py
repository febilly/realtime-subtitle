import base64
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from websockets.sync.server import serve

from local_inference.remote_client import RemoteASREngine, normalize_server_url, probe_remote_server, remote_readiness_error
from streaming_translation.api.hymt2 import HyMT2API


@pytest.fixture
def endpoint():
    state = SimpleNamespace(calls=[], connections=0, drop_once=False)

    def handler(ws):
        state.connections += 1
        connection = state.connections
        initial = json.loads(ws.recv())
        if initial["type"] == "health":
            ws.send(json.dumps(dict(type="health_ok", protocol_version=1, ready=True,
                                    models={"qwen3-asr": {"ready": True}, "hymt2": {"ready": True}})))
            return
        asr = initial.get("service") == "asr"
        ws.send(json.dumps(dict(type="init_ok", protocol_version=1, service="asr" if asr else "hymt2",
                                engine="qwen3-asr", hypothesis_mode="sentence_revision", source_token_join_mode="verbatim")))
        for raw in ws:
            message = json.loads(raw)
            state.calls.append((connection, message))
            if state.drop_once:
                state.drop_once = False
                ws.close()
                return
            if asr:
                ws.send(json.dumps(dict(type="recognition", request_id=message["request_id"],
                                        result={"text": "Hello.", "language": "en"}, context="must not be reused")))
            else:
                ws.send(json.dumps(dict(type="translation", seq=message["seq"], committed_text="你好。", buffer_text="")))

    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        state.url = f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        try:
            yield state
        finally:
            server.shutdown()
            thread.join(3)


@pytest.mark.parametrize("value,expected", [("127.0.0.1:18775", "ws://127.0.0.1:18775"),
                                           ("https://example.com/", "wss://example.com"), ("", "")])
def test_url_normalization(value, expected):
    assert normalize_server_url(value) == expected


@pytest.mark.parametrize("value", ["file:///tmp/model", "ws://", "ws://user:password@host", "ws://host:90000"])
def test_url_rejects_invalid_endpoints(value):
    with pytest.raises(ValueError):
        normalize_server_url(value)


def test_health_and_asr_audio_roundtrip_keep_prompt_context_isolated(endpoint):
    assert probe_remote_server(endpoint.url)["ready"]
    engine = RemoteASREngine(endpoint.url)
    audio = np.array([0.25, -0.5], dtype=np.float32)
    try:
        assert engine.transcribe(audio, update_context=False)["text"] == "Hello."
        engine.transcribe(audio, update_context=True)
        engine.transcribe(audio, update_context=False)
        payloads = [p for _, p in endpoint.calls]
        assert np.array_equal(np.frombuffer(base64.b64decode(payloads[0]["audio_base64"]), dtype="<f4"), audio)
        assert [p["reset_draft"] for p in payloads] == [True, False, True]
        assert all(not p["update_context"] and p["context"] == "" for p in payloads)
    finally:
        engine.unload()


def test_locator_probe_uses_independent_connection_and_retains_live_draft(endpoint):
    engine = RemoteASREngine(endpoint.url)
    try:
        engine.transcribe(np.ones(2), update_context=False)
        engine.probe_transcribe(np.ones(1))
        engine.transcribe(np.ones(3), update_context=False)
        assert endpoint.calls[0][0] == endpoint.calls[2][0] != endpoint.calls[1][0]
        assert endpoint.calls[1][1]["reset_draft"] is True
        assert endpoint.calls[2][1]["reset_draft"] is False
    finally:
        engine.unload()


def test_asr_reconnect_replays_full_window_without_old_draft(endpoint):
    engine = RemoteASREngine(endpoint.url)
    endpoint.drop_once = True
    try:
        assert engine.transcribe(np.ones(3), update_context=False)["text"] == "Hello."
        assert len(endpoint.calls) == 2
        assert endpoint.calls[0][0] != endpoint.calls[1][0]
        assert endpoint.calls[0][1]["request_id"] == endpoint.calls[1][1]["request_id"]
        assert endpoint.calls[1][1]["reset_draft"] is True
    finally:
        engine.unload()


def test_remote_hymt_keeps_revision_history_and_context_sensitive_final(endpoint, monkeypatch):
    monkeypatch.setattr("streaming_translation.api.hymt2.acquire_local_engine", Mock(side_effect=AssertionError("local weights loaded")))
    translator = HyMT2API(backend="remote", websocket_url=endpoint.url, max_retries=0)
    try:
        assert translator.load_local_engine() is False
        assert translator.translate("Hello.", "en", "zh", is_partial=True, following_source="Next.") == "你好。"
        translator.translate("Hello.", "en", "zh", is_partial=False, following_source="Next revised.")
        assert len(endpoint.calls) == 2
        assert endpoint.calls[1][1]["previous_source"] == "Hello."
        assert endpoint.calls[1][1]["previous_translation"] == "你好。"
        assert endpoint.calls[1][1]["following_source"] == "Next revised."
    finally:
        translator.close()


def test_missing_unused_sensevoice_does_not_block_remote_qwen():
    status = {"ready": False, "models": {"qwen3-asr": {"ready": True}, "hymt2": {"ready": True}}}
    assert remote_readiness_error(status, translation_enabled=True) == ""
    status["models"]["hymt2"]["ready"] = False
    assert remote_readiness_error(status, translation_enabled=False) == ""
    assert "hymt2" in remote_readiness_error(status, translation_enabled=True)


def test_remote_recognizer_bypasses_local_weights(monkeypatch):
    import local_inference.recognizer as module
    monkeypatch.setattr(module, "is_asr_cached", Mock(side_effect=AssertionError("local ASR checked")))
    monkeypatch.setattr(module, "Qwen3ASREngine", Mock(side_effect=AssertionError("local ASR loaded")))
    recognizer = module.LocalQwenRecognizer(Mock(), Mock(), inference_backend="remote", server_url="ws://127.0.0.1:18775")
    assert isinstance(recognizer._ensure_engine(), RemoteASREngine)
    assert recognizer._ensure_boundary_scout() is None


def test_remote_session_checks_service_without_requiring_local_models(monkeypatch):
    import local_session as module
    monkeypatch.setattr(module, "get_all_local_models_status", Mock(side_effect=AssertionError("local models checked")))
    session = module.LocalInferenceSession(Mock(), Mock())
    session._inference_backend = "remote"
    session._server_url = "ws://127.0.0.1:18775"
    session._check_models(True)
    monkeypatch.setattr("local_inference.remote_client.probe_remote_server", lambda url: {"ready": True})
    monkeypatch.setattr("local_inference.model_manager.download_silero", Mock())
    session._check_models(True, prepare_remote=True)
    translator = session._make_row_translator()
    assert translator.backend == "remote" and translator._local_engine is None
    translator.close()
