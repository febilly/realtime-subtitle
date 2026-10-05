import io
import json
import math
import struct

import av
import pytest

from soniox_audio import OggOpusEncoder, SonioxOpusWebSocket


def speech_like_pcm(samples):
    return b"".join(struct.pack("<h", int(10000 * math.sin(i * 0.1))) for i in range(samples))


def decode(data):
    with av.open(io.BytesIO(data)) as container:
        assert container.streams.audio[0].codec_context.name == "opus"
        return sum(frame.samples for frame in container.decode(audio=0))


def test_constant_32kbps_and_output_before_finish():
    encoder = OggOpusEncoder()
    chunks = [encoder.encode(speech_like_pcm(3840)) for _ in range(10)]
    assert all(chunks)  # No seconds-long muxer buffering.
    data = b"".join(chunks) + encoder.finish()
    assert decode(data) == 38400 * 3  # Opus decodes at 48 kHz.
    with av.open(io.BytesIO(data)) as container:
        packets = [p for p in container.demux(audio=0) if p.size]
        # 40 ms at 32 kbps = 160 bytes, for every packet, including silence.
        assert all(p.size == 160 for p in packets)
    assert len(data) < 38400 * 2 / 6


def test_partial_frames_preserve_duration_and_each_stream_has_headers():
    for _ in range(2):
        encoder = OggOpusEncoder()
        data = b"".join(encoder.encode(speech_like_pcm(n)) for n in (37, 320, 1001, 1600))
        data += encoder.finish()
        assert data.startswith(b"OggS")
        assert b"OpusHead" in data
        assert decode(data) == (37 + 320 + 1001 + 1600) * 3
        assert encoder.finish() == b""


class Socket:
    def __init__(self):
        self.sent = []
        self.closed = None

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        return timeout

    def close(self, *args):
        self.closed = args


@pytest.mark.parametrize("end", ["", b"", json.dumps({"type": "finalize"})])
def test_transport_encodes_silence_flushes_tail_and_preserves_controls(end):
    socket = Socket()
    ws = SonioxOpusWebSocket(socket)
    control = json.dumps({"type": "llm_request", "text": "hello"})
    ws.send(control)
    ws.send(bytes(3200))  # Warmup silence follows the same encoding path.
    ws.send(speech_like_pcm(117))
    ws.send(end)
    assert socket.sent[0] == control
    assert socket.sent[-1] == end
    assert decode(b"".join(p for p in socket.sent[:-1] if isinstance(p, bytes))) == 1717 * 3
    assert ws.recv(timeout=0.25) == 0.25
    ws.close(1000, "rollover")
    assert socket.closed == (1000, "rollover")


def test_invalid_pcm_and_audio_after_finish_are_rejected():
    encoder = OggOpusEncoder()
    with pytest.raises(ValueError, match="16-bit"):
        encoder.encode(b"x")
    encoder.finish()
    with pytest.raises(RuntimeError, match="finished"):
        encoder.encode(bytes(640))


def test_transport_failure_is_propagated_to_audio_router():
    socket = Socket()
    ws = SonioxOpusWebSocket(socket)
    def fail(data):
        raise OSError("connection lost")
    socket.send = fail
    with pytest.raises(OSError, match="connection lost"):
        ws.send(bytes(7680))
    ws.close(1000, "network_error")
