"""Streaming local Qwen3-ASR recognizer for RealtimeSubtitle.

This is the Qwen-only form of FunASR's local recognizer.  It keeps the proven
512-sample Silero VAD path, bounded audio queue, serialized inference, partial
refresh policy and final-result reuse, while exposing a small callback API that
fits this application's Provider/AudioStreamer lifecycle.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

import config
from . import get_engine_runtime_issues
from .asr_qwen3 import Qwen3ASREngine
from .model_manager import is_asr_cached, is_asr_models_ready, is_silero_cached
from .vad_processor import VADProcessor

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
VAD_CHUNK_SAMPLES = 512
VAD_CHUNK_DURATION = VAD_CHUNK_SAMPLES / SAMPLE_RATE


class LocalQwenRecognizer:
    """Local VAD + Qwen3-ASR with streaming partial/final callbacks."""

    def __init__(
        self,
        on_result: Callable[[str, bool, dict | None], None],
        on_error: Callable[[Exception], None],
        *,
        source_language: str = "auto",
        corpus_text: str | None = None,
    ) -> None:
        self._on_result = on_result
        self._on_error = on_error
        self._source_language = source_language or "auto"
        self._corpus_text = (corpus_text or "").strip()
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=128)
        self._worker: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._active_future: Future | None = None
        self._waiting_partial: np.ndarray | None = None
        self._waiting_final: np.ndarray | None = None
        self._running = False
        self._paused = False
        self._lock = threading.RLock()
        self._engine: Qwen3ASREngine | None = None
        self._vad = self._create_vad()
        self._pending_samples = np.array([], dtype=np.float32)
        self._last_partial_text = ""
        self._last_partial_raw: dict | None = None
        self._last_partial_time = 0.0
        self._silence_trigger_armed = True
        self._voiced_chunk_seq = 0
        self._last_partial_voiced_seq = -1
        self._partial_pending_voiced_seq = -1
        self._stream_id = 0

    @staticmethod
    def _create_vad() -> VADProcessor:
        vad = VADProcessor(
            sample_rate=SAMPLE_RATE,
            threshold=float(config.LOCAL_VAD_THRESHOLD),
            min_speech_duration=float(config.LOCAL_VAD_MIN_SPEECH_DURATION),
            chunk_duration=VAD_CHUNK_DURATION,
            pre_speech_duration=float(config.VAD_PRE_SPEECH_DURATION),
        )
        vad.update_settings(
            {
                "vad_mode": config.LOCAL_VAD_MODE if config.VAD_ENABLED else "disabled",
                "vad_threshold": float(config.LOCAL_VAD_THRESHOLD),
                "min_speech_duration": float(config.LOCAL_VAD_MIN_SPEECH_DURATION),
                "silence_duration": max(0.05, float(config.LOCAL_VAD_SILENCE_DURATION)),
                "pre_speech_duration": float(config.VAD_PRE_SPEECH_DURATION),
            }
        )
        return vad

    @staticmethod
    def _input_cap_samples() -> int:
        return int(max(1.0, float(config.LOCAL_VAD_MAX_SPEECH_DURATION)) * SAMPLE_RATE)

    def _ensure_engine(self) -> Qwen3ASREngine:
        if self._engine is not None:
            return self._engine
        if not is_asr_cached("qwen3-asr"):
            if not is_silero_cached():
                raise RuntimeError("Silero VAD 尚未就绪，请先在本地模型设置中下载。")
            issues = get_engine_runtime_issues("qwen3-asr")
            if is_asr_models_ready("qwen3-asr") and issues:
                raise RuntimeError(
                    "Qwen3-ASR 模型已找到，但缺少本地推理依赖: " + ", ".join(issues)
                )
            raise RuntimeError("Qwen3-ASR 模型或 llama.cpp 运行时尚未就绪。")
        engine = Qwen3ASREngine(corpus_text=self._corpus_text or None)
        engine.set_language(self._source_language)
        self._engine = engine
        return engine

    def _transcribe(self, audio: np.ndarray, *, is_final: bool) -> tuple[str, dict] | None:
        started = time.monotonic()
        result = self._ensure_engine().transcribe(audio, update_context=is_final)
        logger.info(
            "[local-asr] %s %.2fs audio in %.0fms",
            "final" if is_final else "partial",
            audio.size / SAMPLE_RATE,
            (time.monotonic() - started) * 1000,
        )
        if not result:
            return None
        text = str(result.get("text") or "").strip()
        return (text, result) if text else None

    def _emit_final(self, text: str, raw: dict | None) -> None:
        if raw is None:
            raw = self._last_partial_raw
        self._last_partial_text = ""
        self._last_partial_raw = None
        self._last_partial_voiced_seq = -1
        self._stream_id += 1
        self._reset_engine_draft()
        self._on_result(text, True, raw)

    def _on_transcription_done(self, future: Future, *, stream_id: int, is_final: bool) -> None:
        try:
            payload = future.result()
        except Exception as error:  # runtime safety
            logger.exception("Local Qwen ASR failed")
            self._on_error(error)
            payload = None

        with self._lock:
            if payload is not None:
                text, raw = payload
                if is_final:
                    self._emit_final(text, raw)
                elif stream_id == self._stream_id:
                    self._last_partial_voiced_seq = self._partial_pending_voiced_seq
                    self._last_partial_raw = raw
                    if text != self._last_partial_text:
                        self._last_partial_text = text
                        self._on_result(text, False, raw)
            elif is_final or stream_id == self._stream_id:
                self._last_partial_voiced_seq = -1
            self._try_start_transcribe_locked()

    def _try_start_transcribe_locked(self) -> None:
        if self._executor is None:
            return
        if self._active_future is not None and not self._active_future.done():
            return
        if self._waiting_final is not None:
            audio, self._waiting_final, is_final = self._waiting_final, None, True
        elif self._waiting_partial is not None:
            audio, self._waiting_partial, is_final = self._waiting_partial, None, False
        else:
            self._active_future = None
            return
        stream_id = self._stream_id
        self._active_future = self._executor.submit(self._transcribe, audio, is_final=is_final)
        self._active_future.add_done_callback(
            lambda done, sid=stream_id, final=is_final: self._on_transcription_done(
                done, stream_id=sid, is_final=final
            )
        )

    def _enqueue_transcribe(self, audio: np.ndarray, *, is_final: bool) -> None:
        if audio.size == 0:
            return
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen-asr")
            if is_final:
                self._waiting_final = audio.copy()
            else:
                self._waiting_partial = audio.copy()
                self._partial_pending_voiced_seq = self._voiced_chunk_seq
            self._try_start_transcribe_locked()

    def _maybe_emit_partial(self) -> None:
        if not config.LOCAL_INCREMENTAL_ASR or self._audio_queue.qsize() >= 8:
            return
        silence = self._vad.current_silence_duration
        if silence <= 0:
            self._silence_trigger_armed = True
        peek = self._vad.peek_buffer()
        if peek is None:
            return
        audio, duration = peek
        if duration < 1.0:
            return
        now = time.monotonic()
        elapsed = now - self._last_partial_time
        min_interval = float(config.LOCAL_INCREMENTAL_MIN_UPDATE_INTERVAL)
        fallback = max(min_interval, float(config.LOCAL_INCREMENTAL_MAX_UPDATE_INTERVAL))
        trigger_silence = float(config.LOCAL_INCREMENTAL_TRIGGER_SILENCE_MS) / 1000.0
        silence_hit = trigger_silence > 0 and self._silence_trigger_armed and silence >= trigger_silence
        if not silence_hit and elapsed < fallback:
            return
        if silence_hit and elapsed < min_interval:
            return
        if silence_hit:
            self._silence_trigger_armed = False
        self._last_partial_time = now
        self._enqueue_transcribe(audio, is_final=False)

    def _try_reuse_partial_as_final(self) -> str | None:
        if not config.LOCAL_INCREMENTAL_ASR:
            return None
        if self._voiced_chunk_seq != self._last_partial_voiced_seq:
            return None
        return self._last_partial_text.strip() or None

    def _reset_engine_draft(self) -> None:
        if self._engine is not None:
            try:
                self._engine.reset_draft()
            except Exception:
                logger.debug("Failed to reset Qwen ASR draft", exc_info=True)

    def _process_chunk(self, chunk: np.ndarray) -> None:
        if self._vad.is_speaking and self._vad._speech_samples >= self._input_cap_samples():
            chunk = np.zeros_like(chunk)
        was_speaking = self._vad.is_speaking
        segment = self._vad.process_chunk(chunk)
        if self._vad.is_speaking and self._vad._silence_counter == 0:
            self._voiced_chunk_seq += 1
        if segment is not None:
            self._last_partial_time = time.monotonic()
            self._silence_trigger_armed = True
            reused = self._try_reuse_partial_as_final()
            if reused is not None:
                self._emit_final(reused, self._last_partial_raw)
            else:
                self._enqueue_transcribe(segment, is_final=True)
            return
        if self._vad.is_speaking:
            if not was_speaking:
                self._last_partial_time = time.monotonic()
            self._maybe_emit_partial()

    def _feed_samples(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        if self._pending_samples.size:
            samples = np.concatenate([self._pending_samples, samples])
        offset = 0
        while offset + VAD_CHUNK_SAMPLES <= len(samples):
            self._process_chunk(samples[offset : offset + VAD_CHUNK_SAMPLES])
            offset += VAD_CHUNK_SAMPLES
        self._pending_samples = samples[offset:].copy()

    @staticmethod
    def _pcm_to_float32(data: bytes) -> np.ndarray:
        return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

    def _drain_audio_queue_locked(self) -> None:
        chunks: list[np.ndarray] = []
        while True:
            try:
                chunks.append(self._pcm_to_float32(self._audio_queue.get_nowait()))
            except queue.Empty:
                break
        if chunks:
            self._feed_samples(np.concatenate(chunks))

    def _finalize_current_segment_locked(self) -> None:
        if self._pending_samples.size:
            padded = np.pad(self._pending_samples, (0, VAD_CHUNK_SAMPLES - len(self._pending_samples)))
            self._process_chunk(padded)
            self._pending_samples = np.array([], dtype=np.float32)
        segment = self._vad.force_flush() if self._vad.is_speaking else self._vad.flush()
        if segment is not None:
            reused = self._try_reuse_partial_as_final()
            if reused is not None:
                self._emit_final(reused, self._last_partial_raw)
            else:
                self._enqueue_transcribe(segment, is_final=True)
        self._last_partial_text = ""
        self._last_partial_time = time.monotonic()
        self._silence_trigger_armed = True

    def _worker_loop(self) -> None:
        try:
            while self._running:
                try:
                    data = self._audio_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if not self._paused:
                    with self._lock:
                        self._feed_samples(self._pcm_to_float32(data))
        except Exception as error:
            logger.exception("Local audio worker failed")
            self._on_error(error)

    def start(self) -> None:
        with self._lock:
            self._ensure_engine()
            if self._running:
                return
            self._paused = False
            self._running = True
            self._stream_id = 0
            self._reset_engine_draft()
            self._last_partial_text = ""
            self._last_partial_raw = None
            self._last_partial_time = time.monotonic()
            self._silence_trigger_armed = True
            self._voiced_chunk_seq = 0
            self._last_partial_voiced_seq = -1
            self._partial_pending_voiced_seq = -1
            self._waiting_partial = None
            self._waiting_final = None
            self._active_future = None
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen-asr")
            self._worker = threading.Thread(target=self._worker_loop, name="LocalQwenAudio", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        with self._lock:
            self._drain_audio_queue_locked()
            self._finalize_current_segment_locked()
            self._running = False
            self._paused = False
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        self._active_future = None
        self._waiting_partial = None
        self._waiting_final = None
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.unload()
                finally:
                    self._engine = None
            self._vad.reset()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._drain_audio_queue_locked()
            self._finalize_current_segment_locked()

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def send(self, data: bytes) -> None:
        """WebSocket-compatible sink used directly by AudioStreamer."""
        if not self._running or self._paused or not data:
            return
        try:
            self._audio_queue.put_nowait(data)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(data)
            except queue.Full:
                pass
