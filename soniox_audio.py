"""Per-connection PCM16 -> 32 kbps Ogg Opus transport for Soniox."""

from fractions import Fraction
import json
import threading


OPUS_BIT_RATE = 32_000


class _OggOutput:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)
        return len(data)

    def take(self):
        data = bytes(self.data)
        self.data.clear()
        return data


class OggOpusEncoder:
    """Encode mono PCM16 incrementally without an external ffmpeg process."""

    def __init__(self, sample_rate=16000):
        import av

        self._av = av
        self.sample_rate = sample_rate
        self._samples = 0
        self._finished = False
        self._output = _OggOutput()
        # Flush pages every 100 ms instead of Ogg's default multi-second delay.
        self._container = av.open(
            self._output, "w", format="ogg",
            options={"page_duration": "100000", "flush_packets": "1"},
        )
        try:
            self._stream = self._container.add_stream("libopus", rate=sample_rate)
            self._stream.layout = "mono"
            self._stream.bit_rate = OPUS_BIT_RATE
            self._stream.codec_context.options = {
                "vbr": "off", "application": "voip", "frame_duration": "20",
            }
            self._container.start_encoding()
        except Exception:
            self._container.close()
            raise

    def encode(self, pcm):
        if self._finished:
            raise RuntimeError("Opus stream is already finished")
        if len(pcm) % 2:
            raise ValueError("Mono PCM16 must contain complete 16-bit samples")
        if pcm:
            samples = len(pcm) // 2
            frame = self._av.AudioFrame(format="s16", layout="mono", samples=samples)
            frame.sample_rate = self.sample_rate
            frame.time_base = Fraction(1, self.sample_rate)
            frame.pts = self._samples
            frame.planes[0].update(pcm)
            self._samples += samples
            self._container.mux(self._stream.encode(frame))
        return self._output.take()

    def finish(self):
        if not self._finished:
            self._finished = True
            try:
                self._container.mux(self._stream.encode(None))
            finally:
                self._container.close()
        return self._output.take()

    def close(self):
        """Release native resources when the connection is abandoned."""
        if not self._finished:
            self._finished = True
            self._container.close()
        self._output.take()


class SonioxOpusWebSocket:
    """WebSocket adapter: audio is encoded, JSON/control frames pass through.

    VAD and the audio router keep receiving PCM. Each connection owns its own
    encoder and Ogg headers, including rollover warmup and sleep/wake streams.
    """

    def __init__(self, ws, sample_rate=16000):
        self._ws = ws
        self._lock = threading.Lock()
        self._encoder = OggOpusEncoder(sample_rate)

    def send(self, payload):
        with self._lock:
            if isinstance(payload, (bytes, bytearray, memoryview)) and payload:
                encoded = self._encoder.encode(bytes(payload))
                if encoded:
                    self._ws.send(encoded)
                return
            finalize = False
            if isinstance(payload, str) and payload:
                try:
                    command = json.loads(payload)
                    finalize = isinstance(command, dict) and command.get("type") == "finalize"
                except ValueError:
                    pass
            # The runtime finalizes only after detaching an old stream. Drain
            # codec/muxer latency before asking Soniox to finalize its last words.
            if payload in ("", b"") or finalize:
                encoded = self._encoder.finish()
                if encoded:
                    self._ws.send(encoded)
            self._ws.send(payload)

    def close(self, *args, **kwargs):
        with self._lock:
            try:
                self._encoder.close()
            finally:
                self._ws.close(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._ws, name)
