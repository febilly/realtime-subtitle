"""Thread-safe audio routing helpers for Soniox stream rollover."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any

import numpy as np

# Default local VAD backend. "ten_vad" is the shipped default and the only
# backend bundled into the packaged exe. "silero" is kept as a quick code-level
# alternative: install silero-vad-lite and set DEFAULT_VAD_BACKEND = "silero"
# (or pass vad_backend="silero" to AudioSendRouter) to switch.
DEFAULT_VAD_BACKEND = "ten_vad"

try:
    from ten_vad import TenVad as TenVadBackend
except Exception as exc:  # noqa: BLE001 - optional at import time for tests/dev envs
    TenVadBackend = None
    TenVadImportError = exc
else:
    TenVadImportError = None

try:
    # Optional alternative backend; not installed/bundled by default.
    from silero_vad_lite import SileroVAD as SileroVadLiteBackend
except Exception as exc:  # noqa: BLE001 - optional at import time for tests/dev envs
    SileroVadLiteBackend = None
    SileroVadLiteImportError = exc
else:
    SileroVadLiteImportError = None


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
        self.last_observed_seconds = 0.0
        self.last_speech_seconds = 0.0

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
        processed_frames = 0
        speech_frames = 0

        while len(self._pending) >= frame_bytes:
            frame = bytes(self._pending[:frame_bytes])
            del self._pending[:frame_bytes]
            audio_frame = np.frombuffer(frame, dtype=np.int16)
            probability, flag = self._vad.process(audio_frame)
            self.last_probability = float(probability)
            self.last_flag = int(flag)
            processed_any = True
            processed_frames += 1
            self.last_audio_at = time.monotonic()

            if int(flag) == 1:
                saw_speech = True
                speech_frames += 1
                self.consecutive_silence_seconds = 0.0
            else:
                self.consecutive_silence_seconds += self.hop_size / self.sample_rate

        if not processed_any:
            self.last_observed_seconds = 0.0
            self.last_speech_seconds = 0.0
            return False
        frame_duration = self.hop_size / self.sample_rate
        self.last_observed_seconds = processed_frames * frame_duration
        self.last_speech_seconds = speech_frames * frame_duration
        return not saw_speech

    def is_ready(self, *, min_observed_at: float | None = None) -> bool:
        if self.last_audio_at is None:
            return False
        if min_observed_at is not None and self.last_audio_at < min_observed_at:
            return False
        return self.consecutive_silence_seconds >= self.silence_hold_seconds


class SileroVadLiteSilenceDetector:
    """Silero VAD Lite backed PCM_s16le detector used to find speech/silence."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        sample_width_bytes: int = 2,
        silence_hold_seconds: float = 0.7,
        hop_size: int | None = None,
        speech_threshold: float = 0.5,
    ):
        if SileroVadLiteBackend is None:
            raise RuntimeError(f"silero-vad-lite is unavailable: {SileroVadLiteImportError}")

        self.sample_rate = max(1, int(sample_rate))
        self.sample_width_bytes = max(1, int(sample_width_bytes))
        self.silence_hold_seconds = max(0.0, float(silence_hold_seconds))
        self.speech_threshold = min(1.0, max(0.0, float(speech_threshold)))
        self._vad = SileroVadLiteBackend(self.sample_rate)
        self.hop_size = max(1, int(hop_size or self._vad.window_size_samples))
        self._pending = bytearray()
        self.consecutive_silence_seconds = 0.0
        self.last_audio_at: float | None = None
        self.last_probability = 0.0
        self.last_flag = 0
        self.last_observed_seconds = 0.0
        self.last_speech_seconds = 0.0

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
        processed_frames = 0
        speech_frames = 0

        while len(self._pending) >= frame_bytes:
            frame = bytes(self._pending[:frame_bytes])
            del self._pending[:frame_bytes]
            audio_frame = np.frombuffer(frame, dtype=np.int16)
            float_frame = (audio_frame.astype(np.float32) / 32768.0).copy()
            probability = self._vad.process(memoryview(float_frame))
            self.last_probability = float(probability)
            self.last_flag = int(self.last_probability >= self.speech_threshold)
            processed_any = True
            processed_frames += 1
            self.last_audio_at = time.monotonic()

            if self.last_flag == 1:
                saw_speech = True
                speech_frames += 1
                self.consecutive_silence_seconds = 0.0
            else:
                self.consecutive_silence_seconds += self.hop_size / self.sample_rate

        if not processed_any:
            self.last_observed_seconds = 0.0
            self.last_speech_seconds = 0.0
            return False
        frame_duration = self.hop_size / self.sample_rate
        self.last_observed_seconds = processed_frames * frame_duration
        self.last_speech_seconds = speech_frames * frame_duration
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
        self.last_observed_seconds = 0.0
        self.last_speech_seconds = 0.0

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
        self.last_observed_seconds = duration
        self.last_speech_seconds = 0.0 if is_silent else duration

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


class TolerantSpeechActivityGate:
    """Track long silence while requiring enough speech in a recent window."""

    def __init__(
        self,
        *,
        idle_timeout_seconds: float,
        speech_grace_seconds: float = 0.5,
        speech_window_seconds: float = 0.75,
        wake_speech_seconds: float | None = None,
    ):
        self.idle_timeout_seconds = max(0.0, float(idle_timeout_seconds))
        self.speech_grace_seconds = max(0.0, float(speech_grace_seconds))
        self.speech_window_seconds = max(
            self.speech_grace_seconds,
            float(speech_window_seconds),
        )
        if wake_speech_seconds is None:
            wake_speech_seconds = self.speech_grace_seconds
        self.wake_speech_seconds = max(0.0, float(wake_speech_seconds))
        self.confirmed_silence_seconds = 0.0
        self._window: deque[tuple[float, float]] = deque()
        self._window_observed_seconds = 0.0
        self._window_speech_seconds = 0.0
        self.last_audio_at: float | None = None
        self._wake_ready = False

    def reset_after_wake(self) -> None:
        self.confirmed_silence_seconds = 0.0
        self._reset_window()
        self._wake_ready = False

    def reset_for_dormant(self) -> None:
        self._reset_window()
        self._wake_ready = False

    def _reset_window(self) -> None:
        self._window.clear()
        self._window_observed_seconds = 0.0
        self._window_speech_seconds = 0.0

    def _append_observation(self, observed: float, speech: float) -> None:
        self._window.append((observed, speech))
        self._window_observed_seconds += observed
        self._window_speech_seconds += speech

        while self._window and self._window_observed_seconds > self.speech_window_seconds:
            excess = self._window_observed_seconds - self.speech_window_seconds
            oldest_observed, oldest_speech = self._window[0]
            if oldest_observed <= excess + 1e-9:
                self._window.popleft()
                self._window_observed_seconds -= oldest_observed
                self._window_speech_seconds -= oldest_speech
                continue

            remaining_observed = oldest_observed - excess
            speech_fraction = oldest_speech / oldest_observed if oldest_observed > 0.0 else 0.0
            removed_speech = oldest_speech - (remaining_observed * speech_fraction)
            self._window[0] = (remaining_observed, oldest_speech - removed_speech)
            self._window_observed_seconds -= excess
            self._window_speech_seconds -= removed_speech
            break

    def update(self, observed_seconds: float, speech_seconds: float) -> bool:
        observed = max(0.0, float(observed_seconds or 0.0))
        speech = min(observed, max(0.0, float(speech_seconds or 0.0)))
        if observed <= 0.0:
            return self._wake_ready

        self.last_audio_at = time.monotonic()
        self._append_observation(observed, speech)

        speech_confirmed = (
            self._window_speech_seconds >= self.speech_grace_seconds
            and self._window_observed_seconds <= self.speech_window_seconds + 1e-9
        )
        if speech_confirmed:
            if self._window_speech_seconds >= self.wake_speech_seconds:
                self._wake_ready = True
            self.confirmed_silence_seconds = 0.0
        else:
            self.confirmed_silence_seconds += observed

        return self._wake_ready

    def sleep_ready(self) -> bool:
        if self.last_audio_at is None:
            return False
        return self.confirmed_silence_seconds >= self.idle_timeout_seconds

    def wake_ready(self) -> bool:
        return self._wake_ready


class AudioSendRouter:
    """Route captured audio to the active websocket and buffer during handoff."""

    def __init__(
        self,
        max_buffered_chunks: int = 200,
        *,
        sample_rate: int = 16000,
        chunk_size: int = 3840,
        silence_hold_seconds: float = 0.7,
        vad_speech_threshold: float = 0.5,
        vad_backend: str | None = None,
        sleep_idle_seconds: float | None = None,
        sleep_pre_roll_seconds: float = 1.0,
        sleep_speech_grace_seconds: float = 0.5,
        sleep_speech_window_seconds: float = 0.75,
    ):
        self._max_buffered_chunks = max(1, int(max_buffered_chunks))
        self._buffered_chunks: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._target: Any | None = None
        self._closed = False
        self._sleep_buffering = False
        self._sleep_wake_started = False
        chunk_duration = max(0.001, float(chunk_size) / max(1, int(sample_rate)))
        self._sleep_pre_roll_chunks = max(1, int(math.ceil(max(0.0, float(sleep_pre_roll_seconds)) / chunk_duration)))
        sleep_detection_chunks = max(
            1,
            int(math.ceil(max(0.0, float(sleep_speech_window_seconds)) / chunk_duration)),
        )
        self._sleep_pre_wake_chunks = max(
            self._sleep_pre_roll_chunks,
            self._sleep_pre_roll_chunks + sleep_detection_chunks,
        )
        self._sleep_gate = (
            TolerantSpeechActivityGate(
                idle_timeout_seconds=float(sleep_idle_seconds),
                speech_grace_seconds=sleep_speech_grace_seconds,
                speech_window_seconds=sleep_speech_window_seconds,
            )
            if sleep_idle_seconds is not None and float(sleep_idle_seconds) > 0
            else None
        )
        backend = (vad_backend or DEFAULT_VAD_BACKEND or "ten_vad").strip().lower()
        self._silence_detector = self._build_silence_detector(
            backend,
            sample_rate=sample_rate,
            silence_hold_seconds=silence_hold_seconds,
            vad_speech_threshold=vad_speech_threshold,
        )

    @staticmethod
    def _build_silence_detector(
        backend: str,
        *,
        sample_rate: int,
        silence_hold_seconds: float,
        vad_speech_threshold: float,
    ):
        """Build the configured VAD detector, falling back to energy on failure.

        "ten_vad" is the default/shipped backend. "silero" is an optional
        alternative kept for quick switching (requires silero-vad-lite).
        """
        if backend == "silero":
            detector_cls = SileroVadLiteSilenceDetector
            label = "Silero VAD Lite"
        else:
            detector_cls = TenVadSilenceDetector
            label = "TEN VAD"
        try:
            return detector_cls(
                sample_rate=sample_rate,
                silence_hold_seconds=silence_hold_seconds,
                speech_threshold=vad_speech_threshold,
            )
        except Exception as error:
            print(f"⚠️  {label} unavailable for stream rollover, using energy silence detector: {error}")
            return EnergySilenceDetector(
                sample_rate=sample_rate,
                silence_hold_seconds=silence_hold_seconds,
            )

    def _buffer_locked(self, payload: bytes) -> None:
        if len(self._buffered_chunks) >= self._max_buffered_chunks:
            self._buffered_chunks.popleft()
        self._buffered_chunks.append(payload)

    def _buffer_sleep_preroll_locked(self, payload: bytes) -> None:
        while len(self._buffered_chunks) >= self._sleep_pre_wake_chunks:
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
                    self._sleep_buffering = False
                    self._sleep_wake_started = False
                    if self._sleep_gate is not None:
                        self._sleep_gate.reset_after_wake()
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

    def enter_sleep_buffering(self, target: Any | None = None) -> bool:
        """Detach from Soniox and keep only local pre-roll until speech returns."""
        with self._lock:
            if (
                target is not None
                and self._target is not target
                and self._target is not None
            ):
                return False
            self._target = None
            self._buffered_chunks.clear()
            self._sleep_buffering = True
            self._sleep_wake_started = False
        if self._sleep_gate is not None:
            self._sleep_gate.reset_for_dormant()
        return True

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

    def sleep_ready(self) -> bool:
        if self._sleep_gate is None:
            return False
        return self._sleep_gate.sleep_ready()

    def sleep_confirmed_silence_seconds(self) -> float:
        if self._sleep_gate is None:
            return 0.0
        return self._sleep_gate.confirmed_silence_seconds

    def wake_ready(self) -> bool:
        with self._lock:
            return self._sleep_wake_started

    def send(self, payload: bytes) -> None:
        """Send audio to the active target, or buffer it if there isn't one."""
        self._silence_detector.update(payload)
        wake_ready = False
        if self._sleep_gate is not None:
            observed_seconds = float(getattr(self._silence_detector, "last_observed_seconds", 0.0) or 0.0)
            speech_seconds = float(getattr(self._silence_detector, "last_speech_seconds", 0.0) or 0.0)
            wake_ready = self._sleep_gate.update(observed_seconds, speech_seconds)

        with self._lock:
            if self._closed:
                return
            target = self._target
            if target is None:
                if self._sleep_buffering and not self._sleep_wake_started:
                    self._buffer_sleep_preroll_locked(payload)
                    if wake_ready:
                        self._sleep_wake_started = True
                    return
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
