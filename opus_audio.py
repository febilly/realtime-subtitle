"""Raw mono Opus packets for hosted Gemini (32 kbps CBR, 40 ms)."""

import opus_native

OPUS_BIT_RATE = 32_000
FRAME_MS = 40
FRAME_SAMPLES = 16000 * FRAME_MS // 1000  # 640 samples per packet at 16 kHz


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
