"""Per-connection PCM16 -> 32 kbps Ogg Opus transport for Soniox."""

import json
import random
import struct
import threading

import opus_native
from opus_audio import OPUS_BIT_RATE, FRAME_SAMPLES

# RFC 7845: granule positions always tick at 48 kHz regardless of input rate.
_OPUS_RATE = 48_000


def _ogg_crc_table():
    table = []
    for byte in range(256):
        crc = byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 \
                else (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return tuple(table)


_OGG_CRC = _ogg_crc_table()


def _ogg_crc(data):
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _OGG_CRC[((crc >> 24) ^ byte) & 0xFF]
    return crc


def _lacing(length):
    # A lacing value of 255 means the packet continues in the next segment,
    # so an exact multiple of 255 still needs a terminating 0.
    if length == 0:
        return []  # empty packet — contributes no segments
    values = [255] * (length // 255)
    values.append(length % 255)
    return values


class _OggMuxer:
    """Just enough of RFC 7845 for one Opus logical stream."""

    def __init__(self, serial):
        self._serial = serial
        self._page_seq = 0

    def page(self, header_type, granule, packets):
        # Segment table is per-packet: joining the bodies first would lose
        # packet boundaries (a 480-byte page of 3x160 would lace as one packet).
        segments = [value for packet in packets for value in _lacing(len(packet))]
        header = b"".join([
            b"OggS", bytes((0, header_type)), struct.pack("<q", granule),
            struct.pack("<I", self._serial), struct.pack("<I", self._page_seq), b"\0\0\0\0",
            bytes((len(segments),)), bytes(segments),
        ])
        self._page_seq += 1
        crc = _ogg_crc(header + b"".join(packets))
        return header[:22] + struct.pack("<I", crc) + header[26:] + b"".join(packets)


class OggOpusEncoder:
    """Encode mono PCM16 incrementally without PyAV/FFmpeg."""

    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self._finished = False
        self._encoder = opus_native.LibOpusEncoder(sample_rate, OPUS_BIT_RATE)
        self._buffer = bytearray()
        self._samples = 0      # real input samples; drives the EOS granule
        self._toc_samples = 0  # encoded samples incl. padding; drives page granules
        self._preskip = self._encoder.lookahead * (_OPUS_RATE // sample_rate)
        self._muxer = _OggMuxer(random.getrandbits(32))
        self._head = self._muxer.page(
            0x02, 0,  # BOS
            [b"OpusHead" + bytes((1, 1)) + struct.pack("<H", self._preskip)
             + struct.pack("<I", sample_rate) + struct.pack("<h", 0) + bytes((0,))],
        )
        self._tags = self._muxer.page(
            0x00, 0, [b"OpusTags" + struct.pack("<I", 0) + struct.pack("<I", 0)],
        )
        self._headers_sent = False

    def _headers(self):
        if self._headers_sent:
            return b""
        self._headers_sent = True
        return self._head + self._tags

    def encode(self, pcm):
        return b"".join(self.encode_pages(pcm))

    def encode_pages(self, pcm):
        """Return one page per packet, including when routed PCM arrives in bursts."""
        if self._finished:
            raise RuntimeError("Opus stream is already finished")
        if len(pcm) % 2:
            raise ValueError("Mono PCM16 must contain complete 16-bit samples")
        self._buffer += pcm
        headers = self._headers()
        pages = []
        frame_bytes = FRAME_SAMPLES * 2
        while len(self._buffer) >= frame_bytes:
            packet = self._encoder.encode(bytes(self._buffer[:frame_bytes]), FRAME_SAMPLES)
            del self._buffer[:frame_bytes]
            self._samples += FRAME_SAMPLES
            self._toc_samples += FRAME_SAMPLES
            # Flush every 40 ms packet immediately; do not wait for a full page.
            pages.append(headers + self._muxer.page(0x00, self._toc_samples * 3, [packet]))
            headers = b""
        if headers:
            pages.append(headers)
        return pages

    def finish(self):
        if self._finished:
            return b""
        self._finished = True
        try:
            data = bytearray(self._headers())
            packets = []
            if self._buffer:
                real_samples = len(self._buffer) // 2
                self._buffer += bytes(FRAME_SAMPLES * 2 - len(self._buffer))
                packet = self._encoder.encode(bytes(self._buffer), FRAME_SAMPLES)
                self._buffer.clear()
                self._samples += real_samples
                self._toc_samples += FRAME_SAMPLES
                packets.append(packet)
            # Decoders trim the pre-skip off both ends: the start trim comes out
            # of the first packet, the end trim off the last, landing the decoded
            # duration exactly on the EOS granule. Zero-padding at finalize
            # usually provides that slack, but when it doesn't (input was an
            # exact frame multiple), emit one silence packet to absorb it.
            rate_ratio = _OPUS_RATE // self.sample_rate
            if (self._toc_samples - self._samples) * rate_ratio <= self._preskip:
                packets.append(self._encoder.encode(bytes(FRAME_SAMPLES * 2), FRAME_SAMPLES))
            eos_granule = self._preskip + self._samples * rate_ratio
            data += self._muxer.page(0x04, eos_granule, packets)  # EOS
            return bytes(data)
        finally:
            self._encoder.close()

    def close(self):
        """Release native resources when the connection is abandoned."""
        if not self._finished:
            self._finished = True
            self._encoder.close()
        self._buffer.clear()


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
                for encoded in self._encoder.encode_pages(bytes(payload)):
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
