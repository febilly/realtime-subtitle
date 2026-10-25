import importlib
import sys

import pytest


@pytest.fixture
def gemini_client():
    """Provide a gemini_client bound to the real config.

    Other test modules (test_ipc_server) install a mock config into
    sys.modules at import time, so we deliberately avoid importing config or
    gemini_client at module top (that would fail during collection if this file
    is imported after the mock is installed). Instead, ensure the real config is
    active, import a fresh gemini_client bound to it, and restore the previous
    config module afterward.
    """
    original_config = sys.modules.get("config")
    sys.modules.pop("config", None)
    import config  # fresh real config
    importlib.reload(config)
    import gemini_client as module
    importlib.reload(module)
    try:
        yield module
    finally:
        if original_config is not None:
            sys.modules["config"] = original_config
        else:
            sys.modules.pop("config", None)


def _translation_config(message):
    return message["setup"]["generationConfig"]["translationConfig"]


def _gemini_lang(code):
    from config import to_gemini_language_code
    return to_gemini_language_code(code)


def test_one_way_uses_translation_target_lang_param(gemini_client):
    config = _translation_config(gemini_client.get_setup_message("one_way", "zh-hans"))
    assert config["targetLanguageCode"] == _gemini_lang("zh-hans")


def test_two_way_falls_back_to_target_lang_1(gemini_client, monkeypatch):
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "ja")
    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    monkeypatch.setattr(gemini_client, "_warned_two_way", False)

    # The translation_target_lang param is ignored for two_way; TARGET_LANG_1 wins.
    config = _translation_config(gemini_client.get_setup_message("two_way", "zh-hans"))
    assert config["targetLanguageCode"] == _gemini_lang("ja")


def test_two_way_echo_follows_derived_flag(gemini_client, monkeypatch):
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "en")
    monkeypatch.setattr(gemini_client, "_warned_two_way", True)  # silence warning

    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    assert _translation_config(gemini_client.get_setup_message("two_way"))["echoTargetLanguage"] is True

    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", False)
    assert _translation_config(gemini_client.get_setup_message("two_way"))["echoTargetLanguage"] is False


def test_two_way_matches_one_way_after_warning(gemini_client, monkeypatch):
    # Apart from the fixed TARGET_LANG_1 target and the one-time warning, two_way
    # must produce the same translationConfig as one_way into the same language.
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "en")
    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    monkeypatch.setattr(gemini_client, "_warned_two_way", True)

    two_way = _translation_config(gemini_client.get_setup_message("two_way"))
    one_way = _translation_config(gemini_client.get_setup_message("one_way", "en"))
    assert two_way == one_way


def test_two_way_warns_once(gemini_client, monkeypatch, capsys):
    monkeypatch.setattr(gemini_client, "TARGET_LANG_1", "en")
    monkeypatch.setattr(gemini_client, "GEMINI_ECHO_TARGET_LANGUAGE", True)
    monkeypatch.setattr(gemini_client, "_warned_two_way", False)

    gemini_client.get_setup_message("two_way")
    gemini_client.get_setup_message("two_way")

    out = capsys.readouterr().out
    assert out.count("does not support two-way translation") == 1


def test_relay_connect_live_uses_server_minted_ws_url(gemini_client, monkeypatch):
    import config

    connect_calls = []
    sent_payloads = []
    relay_calls = []
    close_calls = []

    class FakeWs:
        def send(self, payload):
            sent_payloads.append(payload)

        def recv(self, timeout=None):
            return '{"setupComplete": {}, "relayAudioFormats": ["opus-batch-v1"]}'

        def close(self, code=None, reason=None):
            close_calls.append((code, reason))

    def relay_connect_info(provider=None, model=None, translation=None, run_id=None, audio_codec=None):
        relay_calls.append({
            "provider": provider,
            "model": model,
            "translation": translation,
            "run_id": run_id, "audio_codec": audio_codec,
        })
        return {
            "url": "wss://relay-2.example.invalid/?ticket=test",
            "headers": {"X-Test-Relay": "ok"},
        }

    def sync_connect(url, **kwargs):
        connect_calls.append((url, kwargs))
        return FakeWs()

    monkeypatch.setattr(config, "RELAY_MODE", True)
    monkeypatch.setattr(config, "relay_connect_info", relay_connect_info)
    monkeypatch.setattr(gemini_client, "sync_connect", sync_connect)

    stream = gemini_client.connect_live("local-key", "one_way", "en", run_id="abc123def456")
    stream.send(bytes(640))
    stream.finalize()
    stream.close("user_stop")

    import json
    assert isinstance(sent_payloads[1], bytes)
    assert sent_payloads[1].startswith(b"OPB1")
    assert len(sent_payloads[1]) == 414  # Five 80-byte packets + 14-byte framing
    assert json.loads(sent_payloads[-1]) == {"realtimeInput": {"audioStreamEnd": True}}

    assert relay_calls == [{
        "provider": "gemini",
        "model": f"models/{gemini_client.GEMINI_MODEL}",
        "translation": None,
        "run_id": "abc123def456", "audio_codec": "opus",
    }]
    # The reason has to reach the close frame; it is how the server tells a
    # deliberate stop from a dropped connection.
    assert close_calls == [(1000, "user_stop")]
    assert connect_calls == [(
        "wss://relay-2.example.invalid/?ticket=test",
        {"max_size": None, "additional_headers": {"X-Test-Relay": "ok"}},
    )]
    assert "Authorization" not in connect_calls[0][1].get("additional_headers", {})
    assert sent_payloads


def test_hosted_stream_sends_raw_opus_and_drains_before_end(gemini_client):
    import json
    import av

    class Socket:
        sent = []
        def send(self, value): self.sent.append(value)
        def close(self, *args): self.closed = args

    socket = Socket()
    stream = gemini_client.GeminiLiveStream(socket, opus=True)
    stream.send('{"type":"llm_request","text":"hello"}')
    for samples in (37, 320, 1001, 1600):
        stream.send(bytes(samples * 2))
    before_finish = len(socket.sent)
    stream.finalize()
    assert len(socket.sent) > before_finish + 1
    assert json.loads(socket.sent[-1]) == {"realtimeInput": {"audioStreamEnd": True}}
    assert socket.sent[0] == '{"type":"llm_request","text":"hello"}'
    packets = []
    for payload in socket.sent[1:-1]:
        assert isinstance(payload, bytes)
        assert payload.startswith(b"OPB1")
        offset = 4
        batch_packets = []
        while offset < len(payload):
            length = int.from_bytes(payload[offset:offset + 2], "big")
            offset += 2
            packet = payload[offset:offset + length]
            offset += length
            assert len(packet) == 80  # 32 kbps CBR * 20 ms
            batch_packets.append(packet)
        assert offset == len(payload)
        assert len(batch_packets) == 5  # One 100 ms upstream PCM message
        packets.extend(batch_packets)
    decoder = av.CodecContext.create("opus", "r")
    frames = [frame for packet in packets for frame in decoder.decode(av.Packet(packet))]
    assert sum(frame.samples for frame in frames) >= (37 + 320 + 1001 + 1600) * 3
    stream.close("rollover")
    assert socket.closed == (1000, "rollover")


def test_hosted_stream_sends_one_binary_batch_per_100ms(gemini_client):
    class Socket:
        def __init__(self): self.sent = []
        def send(self, value): self.sent.append(value)
        def close(self, *args): pass

    socket = Socket()
    stream = gemini_client.GeminiLiveStream(socket, opus=True)
    try:
        for index in range(10):
            stream.send(bytes(3200))
            assert len(socket.sent) == index + 1
            assert len(socket.sent[-1]) == 414
        stream.finalize()
        assert len(socket.sent) == 11  # No extra audio for complete 100 ms chunks.
    finally:
        stream.close()


def test_hosted_connection_falls_back_to_json_for_old_decoder(gemini_client, monkeypatch):
    import base64
    import json
    import config

    class Socket:
        def __init__(self): self.sent = []
        def send(self, value): self.sent.append(value)
        def recv(self, timeout=None): return '{"setupComplete": {}}'
        def close(self, *args): pass

    socket = Socket()
    monkeypatch.setattr(config, "RELAY_MODE", True)
    monkeypatch.setattr(config, "relay_connect_info", lambda *args, **kwargs: {"url": "wss://relay.example.invalid"})
    monkeypatch.setattr(gemini_client, "sync_connect", lambda *args, **kwargs: socket)
    stream = gemini_client.connect_live("placeholder", "none")
    try:
        stream.send(bytes(3200))
        assert len(socket.sent) == 3  # Setup and two legacy 40 ms audio packets.
        for payload in socket.sent[1:]:
            audio = json.loads(payload)["realtimeInput"]["audio"]
            assert audio["mimeType"] == "audio/opus;rate=16000;channels=1"
            assert len(base64.b64decode(audio["data"])) == 160
    finally:
        stream.close()


def test_own_key_stream_keeps_original_pcm(gemini_client):
    import base64
    import json

    class Socket:
        def __init__(self): self.sent = []
        def send(self, value): self.sent.append(value)
        def close(self, *args): pass

    socket = Socket()
    stream = gemini_client.GeminiLiveStream(socket)
    pcm = bytes(range(256)) * 5
    stream.send(pcm)
    stream.finalize()
    audio = json.loads(socket.sent[0])["realtimeInput"]["audio"]
    assert audio["mimeType"] == "audio/pcm;rate=16000"
    assert base64.b64decode(audio["data"]) == pcm
    stream.close()


def test_raw_opus_rejects_invalid_input_and_frees_on_finish():
    import pytest
    from opus_audio import RawOpusEncoder

    with pytest.raises(ValueError, match="16000"):
        RawOpusEncoder(48000)
    encoder = RawOpusEncoder()
    with pytest.raises(ValueError, match="16-bit"):
        encoder.encode(b"x")
    packets = encoder.encode(bytes(1600 * 2)) + encoder.finish()
    assert packets and all(len(packet) == 160 for packet in packets)
    assert encoder._encoder._handle is None  # libopus encoder freed on finish()
    assert encoder.finish() == []
    with pytest.raises(RuntimeError, match="finished"):
        encoder.encode(bytes(640))
    encoder.close()


def test_own_key_connection_uses_google_pcm_even_with_direct_temporary_key_mode(gemini_client, monkeypatch):
    import base64
    import json
    import config

    class Socket:
        def __init__(self): self.sent = []
        def send(self, value): self.sent.append(value)
        def recv(self, timeout=None): return '{"setupComplete":{}}'
        def close(self, *args): pass

    socket = Socket()
    calls = []
    def connect(url, **kwargs):
        calls.append(url)
        return socket
    monkeypatch.setattr(config, "RELAY_MODE", False)
    monkeypatch.setattr(gemini_client, "sync_connect", connect)
    stream = gemini_client.connect_live("own-key", "one_way", "en")
    pcm = bytes(range(256)) * 5
    stream.send(pcm)
    audio = json.loads(socket.sent[1])["realtimeInput"]["audio"]
    assert audio["mimeType"] == "audio/pcm;rate=16000"
    assert base64.b64decode(audio["data"]) == pcm
    assert calls == [gemini_client.GEMINI_WEBSOCKET_URL + "?key=own-key"]
    stream.close()
