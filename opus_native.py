"""Minimal ctypes binding to the bundled libopus (opus.dll).

Only the encoder surface is wrapped; this exists so the audio transports do
not need PyAV/FFmpeg (roughly 30 MB of DLLs) just to encode Opus.
"""

import ctypes
import os
import sys
import threading

OPUS_OK = 0
OPUS_APPLICATION_VOIP = 2048

OPUS_SET_BITRATE = 4002
OPUS_SET_VBR = 4006
OPUS_GET_LOOKAHEAD = 4027

# libopus refuses packets larger than this (1275 * 48k/10k + slack).
_MAX_PACKET_BYTES = 4000

_lib = None
_lib_lock = threading.Lock()


class OpusError(RuntimeError):
    pass


def _configure(lib):
    lib.opus_encoder_create.restype = ctypes.c_void_p
    lib.opus_encoder_create.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
    ]
    lib.opus_encoder_ctl.restype = ctypes.c_int
    # Variadic: later calls pass their own argument types positionally.
    lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.opus_encode.restype = ctypes.c_int
    lib.opus_encode.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
    ]
    lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    return lib


def _load_lib():
    global _lib
    with _lib_lock:
        if _lib is not None:
            return _lib
        candidates = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                candidates.append(os.path.join(meipass, "opus.dll"))
            candidates.append(os.path.join(os.path.dirname(sys.executable), "opus.dll"))
        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "libopus", "win64", "opus.dll")
        )
        for path in candidates:
            if os.path.exists(path):
                _lib = _configure(ctypes.CDLL(path))
                return _lib
        raise OpusError("Bundled opus.dll not found; tried: " + "; ".join(candidates))


class LibOpusEncoder:
    """Mono PCM16 in, one Opus packet per exactly frame_size-samples call."""

    def __init__(self, sample_rate=16000, bit_rate=32000,
                 application=OPUS_APPLICATION_VOIP):
        lib = _load_lib()
        self._lib = lib
        error = ctypes.c_int()
        handle = lib.opus_encoder_create(sample_rate, 1, application, ctypes.byref(error))
        if not handle or error.value != OPUS_OK:
            raise OpusError("opus_encoder_create failed with code %d" % error.value)
        self._handle = handle
        self.sample_rate = sample_rate
        try:
            self._ctl(OPUS_SET_BITRATE, ctypes.c_int(bit_rate))
            self._ctl(OPUS_SET_VBR, ctypes.c_int(0))  # constant bit rate
            lookahead = ctypes.c_int()
            self._ctl(OPUS_GET_LOOKAHEAD, ctypes.byref(lookahead))
            # Codec delay in input samples; becomes the Ogg Opus pre-skip.
            self.lookahead = lookahead.value
        except Exception:
            self.close()
            raise

    def _ctl(self, request, arg=None):
        rc = self._lib.opus_encoder_ctl(self._handle, request, arg) if arg is not None \
            else self._lib.opus_encoder_ctl(self._handle, request)
        if rc != OPUS_OK:
            raise OpusError("opus_encoder_ctl(%d) failed with code %d" % (request, rc))

    def encode(self, pcm, frame_size):
        """Encode exactly frame_size samples (16-bit LE, complete)."""
        if len(pcm) != frame_size * 2:
            raise ValueError("Expected %d samples, got %d bytes" % (frame_size, len(pcm)))
        out = ctypes.create_string_buffer(_MAX_PACKET_BYTES)
        size = self._lib.opus_encode(
            self._handle, pcm, frame_size, out, len(out),
        )
        if size < 0:
            raise OpusError("opus_encode failed with code %d" % size)
        return out.raw[:size]

    def close(self):
        handle, self._handle = getattr(self, "_handle", None), None
        if handle and self._lib is not None:
            self._lib.opus_encoder_destroy(handle)

    def __del__(self):
        self.close()
