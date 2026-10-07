"""32 kbps mono Opus: 40 ms packets and 100 ms Gemini binary batches."""

import opus_native
import struct

OPUS_BIT_RATE = 32_000
FRAME_MS = 40
FRAME_SAMPLES = 16000 * FRAME_MS // 1000  # 640 samples per packet at 16 kHz
GEMINI_CHUNK_SAMPLES = 1600  # 100 ms at 16 kHz
GEMINI_PACKET_SAMPLES = 320  # Five 20 ms packets per 100 ms batch
GEMINI_BATCH_MAGIC = b"OPB1"


class RawOpusEncoder:
    def __init__(self, sample_rate=16000):
        if sample_rate != 16000:
            raise ValueError("Hosted Gemini Opus requires 16000 Hz mono PCM capture")
        self._finished = False
        self._encoder = opus_native.LibOpusEncoder(sample_rate, OPUS_BIT_RATE)
        self._buffer = bytearray()

    def encode(self, pcm):
        if self._finished:
            raise RuntimeError("Opus stream is already finished")
        if len(pcm) % 2:
            raise ValueError("Mono PCM16 must contain complete 16-bit samples")
        self._buffer += pcm
        packets = []
        frame_bytes = FRAME_SAMPLES * 2
        while len(self._buffer) >= frame_bytes:
            packets.append(self._encoder.encode(bytes(self._buffer[:frame_bytes]), FRAME_SAMPLES))
            del self._buffer[:frame_bytes]
        return packets

    def finish(self):
        """Encode the buffered partial frame (zero-padded) and release libopus."""
        if self._finished:
            return []
        self._finished = True
        try:
            if not self._buffer:
                return []
            self._buffer += bytes(FRAME_SAMPLES * 2 - len(self._buffer))
            return [self._encoder.encode(bytes(self._buffer), FRAME_SAMPLES)]
        finally:
            self._buffer.clear()
            self._encoder.close()

    def close(self):
        self._finished = True
        self._buffer.clear()
        self._encoder.close()


class GeminiOpusBatchEncoder:
    """One OPB1 binary message per 100 ms, with five length-prefixed packets.

    The decoder relay concatenates the decoded PCM into one 100 ms Google
    message. Final partial chunks are zero-padded to the same duration.
    """

    def __init__(self, sample_rate=16000):
        if sample_rate != 16000:
            raise ValueError("Hosted Gemini Opus requires 16000 Hz mono PCM capture")
        self._encoder = opus_native.LibOpusEncoder(sample_rate, OPUS_BIT_RATE)
        self._buffer = bytearray()
        self._finished = False

    def _batch(self, pcm):
        output = bytearray(GEMINI_BATCH_MAGIC)
        frame_bytes = GEMINI_PACKET_SAMPLES * 2
        for offset in range(0, len(pcm), frame_bytes):
            packet = self._encoder.encode(pcm[offset:offset + frame_bytes], GEMINI_PACKET_SAMPLES)
            output += struct.pack(">H", len(packet)) + packet
        return bytes(output)

    def encode(self, pcm):
        if self._finished:
            raise RuntimeError("Opus stream is already finished")
        if len(pcm) % 2:
            raise ValueError("Mono PCM16 must contain complete 16-bit samples")
        self._buffer += pcm
        messages = []
        chunk_bytes = GEMINI_CHUNK_SAMPLES * 2
        while len(self._buffer) >= chunk_bytes:
            messages.append(self._batch(bytes(self._buffer[:chunk_bytes])))
            del self._buffer[:chunk_bytes]
        return messages

    def finish(self):
        if self._finished:
            return []
        self._finished = True
        try:
            if not self._buffer:
                return []
            self._buffer += bytes(GEMINI_CHUNK_SAMPLES * 2 - len(self._buffer))
            return [self._batch(bytes(self._buffer))]
        finally:
            self._buffer.clear()
            self._encoder.close()

    def close(self):
        self._finished = True
        self._buffer.clear()
        self._encoder.close()
