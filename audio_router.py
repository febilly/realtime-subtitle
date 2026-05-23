"""Thread-safe audio routing helpers for Soniox stream rollover."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any

import numpy as np

try:
    from ten_vad import TenVad as TenVadBackend
except Exception as exc:  # noqa: BLE001 - optional at import time for tests/dev envs
    TenVadBackend = None
    TenVadImportError = exc
else:
    TenVadImportError = None


class TenVadSilenceDetector:
    """TEN VAD backed PCM_s16le detector used to find safe rollover gaps."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        sample_width_bytes: int = 2,
        silence_hold_seconds: float = 0.7,
        hop_size: int = 256,
        speech_threshold: float = 0.5,
    ):
        if TenVadBackend is None:
            raise RuntimeError(f"ten-vad is unavailable: {TenVadImportError}")

        self.sample_rate = max(1, int(sample_rate))
        self.sample_width_bytes = max(1, int(sample_width_bytes))
        self.silence_hold_seconds = max(0.0, float(silence_hold_seconds))
        self.hop_size = max(1, int(hop_size))
        self.speech_threshold = max(0.0, float(speech_threshold))
        self._vad = TenVadBackend(self.hop_size, self.speech_threshold)
        self._pending = bytearray()
        self.consecutive_silence_seconds = 0.0
        self.last_audio_at: float | None = None
        self.last_probability = 0.0
        self.last_flag = 0

    def update(self, pcm_bytes: bytes) -> bool:
        """Record real-audio chunks and return whether all processed frames were non-speech."""
        if not pcm_bytes:
            return False

        frame_bytes = self.hop_size * self.sample_width_bytes
        if frame_bytes <= 0:
            return False

        self._pending.extend(pcm_bytes)
        processed_any = False
        saw_speech = False

        while len(self._pending) >= frame_bytes:
            frame = bytes(self._pending[:frame_bytes])
            del self._pending[:frame_bytes]
            audio_frame = np.frombuffer(frame, dtype=np.int16)
            probability, flag = self._vad.process(audio_frame)
            self.last_probability = float(probability)
            self.last_flag = int(flag)
            processed_any = True
            self.last_audio_at = time.monotonic()

            if int(flag) == 1:
                saw_speech = True
                self.consecutive_silence_seconds = 0.0
            else:
                self.consecutive_silence_seconds += self.hop_size / self.sample_rate

        if not processed_any:
            return False
        return not saw_speech

    def is_ready(self, *, min_observed_at: float | None = None) -> bool:
        if self.last_audio_at is None:
            return False
        if min_observed_at is not None and self.last_audio_at < min_observed_at:
            return False
        return self.consecutive_silence_seconds >= self.silence_hold_seconds


class EnergySilenceDetector:
    """Small PCM_s16le energy detector used to find safe rollover gaps."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        sample_width_bytes: int = 2,
        silence_hold_seconds: float = 0.7,
        initial_noise_floor: float = 120.0,
        min_speech_rms: float = 550.0,
    ):
        self.sample_rate = max(1, int(sample_rate))
        self.sample_width_bytes = max(1, int(sample_width_bytes))
        self.silence_hold_seconds = max(0.0, float(silence_hold_seconds))
        self.noise_floor = max(0.0, float(initial_noise_floor))
        self.min_speech_rms = max(1.0, float(min_speech_rms))
        self.consecutive_silence_seconds = 0.0
        self.last_audio_at: float | None = None
        self.last_rms = 0.0

    def update(self, pcm_bytes: bytes) -> bool:
        """Record one real-audio chunk and return whether it looked silent."""
        if not pcm_bytes:
            return False

        usable_length = len(pcm_bytes) - (len(pcm_bytes) % self.sample_width_bytes)
        if usable_length <= 0:
            return False

        sample_count = usable_length // self.sample_width_bytes
        if sample_count <= 0:
            return False

        samples = memoryview(pcm_bytes[:usable_length]).cast("h")
        square_sum = sum(int(sample) * int(sample) for sample in samples)
        rms = math.sqrt(square_sum / sample_count)
        threshold = max(self.min_speech_rms, self.noise_floor * 3.0)
        is_silent = rms < threshold
        duration = sample_count / self.sample_rate
        now = time.monotonic()

        self.last_audio_at = now
        self.last_rms = rms

        if is_silent:
            self.noise_floor = (self.noise_floor * 0.98) + (rms * 0.02)
            self.consecutive_silence_seconds += duration
        else:
            self.consecutive_silence_seconds = 0.0

        return is_silent

    def is_ready(self, *, min_observed_at: float | None = None) -> bool:
        if self.last_audio_at is None:
            return False
        if min_observed_at is not None and self.last_audio_at < min_observed_at:
            return False
        return self.consecutive_silence_seconds >= self.silence_hold_seconds


class AudioSendRouter:
    """Route captured audio to the active websocket and buffer during handoff."""

    def __init__(
        self,
        max_buffered_chunks: int = 200,
        *,
        sample_rate: int = 16000,
        silence_hold_seconds: float = 0.7,
    ):
        self._max_buffered_chunks = max(1, int(max_buffered_chunks))
        self._buffered_chunks: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._target: Any | None = None
        self._closed = False
        try:
            self._silence_detector = TenVadSilenceDetector(
                sample_rate=sample_rate,
                silence_hold_seconds=silence_hold_seconds,
            )
        except Exception as error:
            print(f"⚠️  TEN VAD unavailable for stream rollover, using energy silence detector: {error}")
            self._silence_detector = EnergySilenceDetector(
                sample_rate=sample_rate,
                silence_hold_seconds=silence_hold_seconds,
            )

    def _buffer_locked(self, payload: bytes) -> None:
        if len(self._buffered_chunks) >= self._max_buffered_chunks:
            self._buffered_chunks.popleft()
        self._buffered_chunks.append(payload)

    def _attach_target(self, target: Any, expected_current: Any | None = None) -> bool:
        """Detach, flush buffered audio to target, then make it active."""
        while True:
            with self._lock:
                if self._closed:
                    return False
                if (
                    expected_current is not None
                    and self._target is not expected_current
                    and self._target is not None
                ):
                    return False
                self._target = None
                buffered = list(self._buffered_chunks)
                self._buffered_chunks.clear()

            for index, payload in enumerate(buffered):
                try:
                    target.send(payload)
                except Exception as error:
                    with self._lock:
                        for remaining in buffered[index:]:
                            self._buffer_locked(remaining)
                    print(f"⚠️  Failed to flush buffered audio after stream rollover: {error}")
                    return False

            with self._lock:
                if self._closed:
                    return False
                if not self._buffered_chunks:
                    self._target = target
                    return True

            # More audio arrived while buffered data was being flushed. Keep the
            # target detached and loop once more so chunk order stays intact.

    def set_target(self, target: Any) -> bool:
        """Set a websocket-like target and flush audio buffered during startup."""
        return self._attach_target(target)

    def switch_target(self, target: Any, expected_current: Any | None = None) -> bool:
        """Atomically route future real audio to a fresh websocket target."""
        return self._attach_target(target, expected_current=expected_current)

    def clear_target(self, target: Any | None = None) -> None:
        """Temporarily detach the sender so new audio is buffered."""
        with self._lock:
            if target is None or self._target is target:
                self._target = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._target = None
            self._buffered_chunks.clear()

    def buffered_count(self) -> int:
        with self._lock:
            return len(self._buffered_chunks)

    def silence_ready(self, *, min_observed_at: float | None = None) -> bool:
        return self._silence_detector.is_ready(min_observed_at=min_observed_at)

    def consecutive_silence_seconds(self) -> float:
        return self._silence_detector.consecutive_silence_seconds

    def last_audio_at(self) -> float | None:
        return self._silence_detector.last_audio_at

    def send(self, payload: bytes) -> None:
        """Send audio to the active target, or buffer it if there isn't one."""
        self._silence_detector.update(payload)

        with self._lock:
            if self._closed:
                return
            target = self._target
            if target is None:
                self._buffer_locked(payload)
                return

        try:
            target.send(payload)
        except Exception as error:
            with self._lock:
                if self._target is target:
                    self._target = None
                if not self._closed:
                    self._buffer_locked(payload)
            print(f"⚠️  Audio send failed; buffering until the next Soniox stream: {error}")
