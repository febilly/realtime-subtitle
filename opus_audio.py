"""Raw mono Opus packets for hosted Gemini (32 kbps CBR, 20 ms)."""

from fractions import Fraction

OPUS_BIT_RATE = 32_000


class RawOpusEncoder:
    def __init__(self, sample_rate=16000):
        import av

        if sample_rate != 16000:
            raise ValueError("Hosted Gemini Opus requires 16000 Hz mono PCM capture")
        self._av = av
        self._samples = 0
        self._finished = False
        self._codec = av.CodecContext.create("libopus", "w")
        self._codec.sample_rate = sample_rate
        self._codec.layout = "mono"
        self._codec.format = "s16"
        self._codec.bit_rate = OPUS_BIT_RATE
        self._codec.time_base = Fraction(1, sample_rate)
        self._codec.options = {"vbr": "off", "application": "voip", "frame_duration": "20"}
        self._codec.open()

    def encode(self, pcm):
        if self._finished:
            raise RuntimeError("Opus stream is already finished")
        if len(pcm) % 2:
            raise ValueError("Mono PCM16 must contain complete 16-bit samples")
        if not pcm:
            return []
        frame = self._av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        frame.sample_rate = 16000
        frame.time_base = Fraction(1, 16000)
        frame.pts = self._samples
        frame.planes[0].update(pcm)
        self._samples += frame.samples
        return [bytes(packet) for packet in self._codec.encode(frame)]

    def finish(self):
        if self._finished:
            return []
        self._finished = True
        try:
            # Drain the buffered partial frame and codec lookahead before audioStreamEnd.
            return [bytes(packet) for packet in self._codec.encode(None)]
        finally:
            self._codec = None

    def close(self):
        self._finished = True
        self._codec = None  # PyAV releases AVCodecContext through reference counting.
