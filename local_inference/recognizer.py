"""Streaming local Qwen3-ASR recognizer for RealtimeSubtitle.

This is the Qwen-only form of FunASR's local recognizer.  It keeps the proven
512-sample Silero VAD path, bounded audio queue, serialized inference, partial
refresh policy and final-result reuse, while exposing a small callback API that
fits this application's Provider/AudioStreamer lifecycle.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
import queue
import threading
import time
from typing import Callable
import unicodedata

import numpy as np

import config
from . import get_engine_runtime_issues
from .asr_qwen3 import Qwen3ASREngine
from .boundary_scout import (
    NemotronBoundaryScout,
    align_confirmed_prefix_to_scout,
)
from .model_manager import (
    get_semantic_boundary_model_path,
    is_asr_cached,
    is_asr_models_ready,
    is_silero_cached,
)
from .semantic_boundary import (
    BoundaryCommit,
    LocalAgreementBoundaryDetector,
    deduplicate_normalized_replay,
)
from .vad_processor import VADProcessor

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
VAD_CHUNK_SAMPLES = 512
VAD_CHUNK_DURATION = VAD_CHUNK_SAMPLES / SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class _TranscriptionRequest:
    audio: np.ndarray
    is_final: bool
    present: bool
    voiced_seq: int
    stream_id: int


class LocalQwenRecognizer:
    """Local VAD + Qwen3-ASR with streaming partial/final callbacks."""

    def __init__(
        self,
        on_result: Callable[[str, bool, dict | None], None],
        on_error: Callable[[Exception], None],
        *,
        source_language: str = "auto",
        corpus_text: str | None = None,
        inference_backend: str | None = None,
        server_url: str | None = None,
        remote_timeout: float | None = None,
    ) -> None:
        self._on_result = on_result
        self._on_error = on_error
        self._source_language = source_language or "auto"
        self._corpus_text = (corpus_text or "").strip()
        self._inference_backend = inference_backend or getattr(config, "LOCAL_INFERENCE_BACKEND", "local")
        self._server_url = server_url if server_url is not None else getattr(config, "LOCAL_INFERENCE_SERVER_URL", "")
        self._remote_timeout = remote_timeout if remote_timeout is not None else getattr(config, "LOCAL_INFERENCE_REMOTE_TIMEOUT_SECONDS", 60)
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=128)
        # ``send`` must never block behind model inference, but lifecycle
        # transitions still need a short gate around queue admission.  The
        # worker dequeues under ``_lock`` + this gate, so stop/pause cannot drain
        # later chunks before an earlier chunk that the worker has removed but
        # not yet processed.
        self._queue_gate = threading.Lock()
        self._audio_ready = threading.Event()
        self._worker: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._active_future: Future | None = None
        self._waiting_partial: _TranscriptionRequest | None = None
        self._waiting_finals: deque[_TranscriptionRequest] = deque()
        self._running = False
        self._paused = False
        self._lock = threading.RLock()
        self._transcription_idle = threading.Condition(self._lock)
        self._engine: Qwen3ASREngine | None = None
        self._vad = self._create_vad()
        self._pending_samples = np.array([], dtype=np.float32)
        self._last_partial_text = ""
        self._last_emitted_partial_text = ""
        self._last_partial_raw: dict | None = None
        self._last_partial_time = 0.0
        self._last_boundary_scan_samples = 0
        self._silence_trigger_armed = True
        self._voiced_chunk_seq = 0
        self._last_partial_voiced_seq = -1
        self._stream_id = 0
        self._timeline_contiguous = True
        self._replayed_committed_text = ""
        self._replay_expected_suffix = ""
        self._replay_max_lexical_chars = 0
        self._boundary_detector = LocalAgreementBoundaryDetector(
            agreement_count=int(
                getattr(config, "LOCAL_SEMANTIC_BOUNDARY_STABLE_UPDATES", 2)
            ),
            min_prefix_nonspace_chars=4,
            min_right_context_nonspace_chars=int(
                getattr(config, "LOCAL_SEMANTIC_BOUNDARY_MIN_RIGHT_CHARS", 2)
            ),
            require_safe_replay_evidence=True,
            prefer_latest=True,
        )
        self._boundary_scout: NemotronBoundaryScout | None = None
        self._boundary_scout_disabled = False
        self._scout_window_start_seconds = 0.0
        self._scout_vad_offset_seconds = 0.0
        self._semantic_stats = {
            "commits": 0,
            "trimmed_samples": 0,
            "scout_cuts": 0,
            "qwen_locator_cuts": 0,
            "locator_failures": 0,
        }

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
        if self._inference_backend == "remote":
            from .remote_client import RemoteASREngine
            engine = RemoteASREngine(self._server_url, timeout=self._remote_timeout,
                                     corpus_text=self._corpus_text or None)
            engine.set_language(self._source_language)
            self._engine = engine
            return engine
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

    @staticmethod
    def _semantic_boundary_enabled() -> bool:
        return bool(
            getattr(config, "LOCAL_INCREMENTAL_ASR", True)
            and getattr(config, "LOCAL_SEMANTIC_BOUNDARY_ENABLED", False)
        )

    def _ensure_boundary_scout(self) -> NemotronBoundaryScout | None:
        if self._inference_backend == "remote":
            return None  # Keep optional extra model inference off the client.
        if (
            not self._semantic_boundary_enabled()
            or not getattr(config, "LOCAL_SEMANTIC_BOUNDARY_SCOUT_ENABLED", True)
            or self._boundary_scout_disabled
        ):
            return None
        if self._boundary_scout is not None:
            return self._boundary_scout

        model_dir = get_semantic_boundary_model_path(
            getattr(config, "LOCAL_SEMANTIC_BOUNDARY_MODEL_DIR", "")
        )
        if model_dir is None:
            logger.info(
                "[semantic-boundary] Nemotron timestamp sidecar model not found; "
                "using the Qwen prefix locator fallback"
            )
            self._boundary_scout_disabled = True
            return None
        try:
            self._boundary_scout = NemotronBoundaryScout(
                model_dir,
                num_threads=int(
                    getattr(config, "LOCAL_SEMANTIC_BOUNDARY_SCOUT_THREADS", 2)
                ),
                language=self._source_language,
            )
        except Exception as error:
            self._disable_boundary_scout(error)
            return None
        logger.info(
            "[semantic-boundary] Nemotron timestamp sidecar loaded from %s",
            model_dir,
        )
        return self._boundary_scout

    def _disable_boundary_scout(self, error: Exception) -> None:
        if not self._boundary_scout_disabled:
            logger.warning(
                "[semantic-boundary] Timestamp sidecar disabled; "
                "falling back to Qwen prefix location: %s",
                error,
            )
        self._boundary_scout = None
        self._boundary_scout_disabled = True

    def _start_boundary_scout_window(self, audio: np.ndarray) -> None:
        scout = self._ensure_boundary_scout()
        if scout is None:
            return
        try:
            snapshot = scout.reset()
            self._scout_window_start_seconds = snapshot.start_time
            self._scout_vad_offset_seconds = 0.0
            scout.feed(audio)
        except Exception as error:
            self._disable_boundary_scout(error)

    def _feed_boundary_scout(self, audio: np.ndarray) -> None:
        scout = self._boundary_scout
        if scout is None:
            return
        try:
            scout.feed(audio)
        except Exception as error:
            self._disable_boundary_scout(error)

    def _reset_boundary_state(self) -> None:
        self._boundary_detector.reset()
        self._last_boundary_scan_samples = 0

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

    @staticmethod
    def _lexical_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
        return "".join(
            char
            for char in normalized
            if not char.isspace()
            and not unicodedata.category(char).startswith("P")
        )

    def _qwen_prefix_locator(
        self, audio: np.ndarray, confirmed_prefix: str
    ) -> float | None:
        """Bracket the end of a confirmed prefix with independent Qwen probes."""
        max_probes = int(
            getattr(config, "LOCAL_SEMANTIC_BOUNDARY_QWEN_LOCATOR_PROBES", 4)
        )
        if max_probes <= 0 or audio.size < SAMPLE_RATE:
            return None
        target = self._lexical_text(confirmed_prefix)
        if len(target) < 4:
            return None

        engine = self._ensure_engine()

        def contains_prefix(sample_count: int) -> bool:
            result = engine.probe_transcribe(audio[:sample_count])
            candidate = str((result or {}).get("text") or "")
            if self._replayed_committed_text:
                candidate = deduplicate_normalized_replay(
                    self._replayed_committed_text,
                    candidate,
                    expected_suffix=self._replay_expected_suffix,
                    max_overlap_lexical_chars=self._replay_max_lexical_chars,
                )
            return self._lexical_text(candidate).startswith(target)

        low = min(audio.size - 1, max(VAD_CHUNK_SAMPLES, SAMPLE_RATE // 2))
        try:
            # A predicate that is already true at 0.5s is not a useful audio
            # bracket; it is likely a language-model completion, so refuse to
            # turn it into a physical cut.
            if contains_prefix(low):
                return None
            high = audio.size
            # The live partial that produced ``confirmed_prefix`` may have used
            # a speculative draft, while locator probes deliberately do not.
            # Require the independent full-window probe to reproduce the
            # prefix before treating the predicate as a valid binary-search
            # bracket.  Otherwise an all-false search would incorrectly leave
            # ``high`` at the end of the window and cut there.
            if not contains_prefix(high):
                return None
            resolution = int(
                max(
                    0.08,
                    float(
                        getattr(
                            config,
                            "LOCAL_SEMANTIC_BOUNDARY_QWEN_LOCATOR_RESOLUTION_MS",
                            160,
                        )
                    )
                    / 1000.0,
                )
                * SAMPLE_RATE
            )
            for _ in range(max_probes):
                if high - low <= resolution:
                    break
                middle = (low + high) // 2
                if contains_prefix(middle):
                    high = middle
                else:
                    low = middle
            if high <= low:
                return None
            return high / SAMPLE_RATE
        except Exception:
            logger.warning(
                "[semantic-boundary] Qwen prefix locator failed",
                exc_info=True,
            )
            return None

    def _locate_boundary_seconds(
        self, commit: BoundaryCommit, audio: np.ndarray
    ) -> tuple[float, str] | None:
        if not self._timeline_contiguous:
            return None

        scout = self._boundary_scout
        if scout is not None:
            try:
                alignment = align_confirmed_prefix_to_scout(
                    commit.prefix, scout.snapshot
                )
            except Exception as error:
                self._disable_boundary_scout(error)
                alignment = None
            if alignment is not None:
                relative = (
                    self._scout_vad_offset_seconds
                    + alignment.cut_seconds
                    - self._scout_window_start_seconds
                )
                if 0.5 <= relative < self._vad.speech_samples / SAMPLE_RATE:
                    return relative, "nemotron_timestamp"

        located = self._qwen_prefix_locator(audio, commit.prefix)
        if located is not None:
            return located, "qwen_prefix_search"
        return None

    def _emit_final(
        self, text: str, raw: dict | None, *, keep_replay: bool = False
    ) -> None:
        if raw is None:
            raw = self._last_partial_raw
        self._finish_stream_state(keep_replay=keep_replay)
        self._on_result(text, True, raw)

    def _finish_stream_state(self, *, keep_replay: bool = False) -> None:
        """End one ASR utterance even if it produced no presentable final."""
        self._last_partial_text = ""
        self._last_emitted_partial_text = ""
        self._last_partial_raw = None
        self._last_partial_voiced_seq = -1
        if not keep_replay:
            self._replayed_committed_text = ""
            self._replay_expected_suffix = ""
            self._replay_max_lexical_chars = 0
        self._reset_engine_draft()

    def _deduplicate_replay(
        self, text: str, raw: dict | None
    ) -> tuple[str, dict | None]:
        if not self._replayed_committed_text:
            return text, raw
        deduplicated = deduplicate_normalized_replay(
            self._replayed_committed_text,
            text,
            expected_suffix=self._replay_expected_suffix,
            max_overlap_lexical_chars=self._replay_max_lexical_chars,
        )
        if deduplicated == text:
            return text, raw
        updated_raw = dict(raw or {})
        updated_raw["text"] = deduplicated
        updated_raw["semantic_overlap_removed"] = True
        return deduplicated, updated_raw

    def _commit_semantic_boundary(
        self,
        commit: BoundaryCommit,
        *,
        audio: np.ndarray,
        raw: dict | None,
    ) -> bool:
        location = self._locate_boundary_seconds(commit, audio)
        if location is None:
            self._semantic_stats["locator_failures"] += 1
            self._boundary_detector.reset()
            logger.info(
                "[semantic-boundary] Stable text boundary was not mapped safely; "
                "keeping the existing VAD/timeout path"
            )
            return False

        boundary_seconds, method = location
        peek = self._vad.peek_buffer()
        if peek is None:
            self._boundary_detector.reset()
            return False
        current_audio, _duration = peek
        boundary_sample = int(round(boundary_seconds * SAMPLE_RATE))
        replay_samples = int(
            float(getattr(config, "LOCAL_SEMANTIC_BOUNDARY_REPLAY_MS", 800))
            / 1000.0
            * SAMPLE_RATE
        )
        trim_sample = max(0, boundary_sample - replay_samples)
        # Never turn a dubious locator into a near-empty prefix or tail.
        if trim_sample < SAMPLE_RATE // 2 or boundary_sample >= current_audio.size:
            self._semantic_stats["locator_failures"] += 1
            self._boundary_detector.reset()
            return False

        removed = self._vad.trim_prefix(trim_sample)
        tail_peek = self._vad.peek_buffer()
        if removed <= 0 or tail_peek is None:
            self._semantic_stats["locator_failures"] += 1
            self._boundary_detector.reset()
            return False
        tail_audio, _tail_duration = tail_peek
        boundary_offset = min(
            tail_audio.size, max(0, boundary_sample - removed)
        )

        # Any queued partial still describes the pre-cut window.  Invalidate it
        # before presenting the final-prefix -> suffix-partial barrier.
        self._waiting_partial = None
        self._stream_id += 1
        self._reset_boundary_state()
        self._replayed_committed_text = commit.prefix
        self._replay_expected_suffix = commit.suffix
        lexical_prefix_length = len(self._lexical_text(commit.prefix))
        lexical_per_second = lexical_prefix_length / max(0.5, boundary_seconds)
        self._replay_max_lexical_chars = max(
            4,
            int(
                round(
                    lexical_per_second
                    * (boundary_offset / SAMPLE_RATE)
                    * 2.0
                    + 4
                )
            ),
        )

        scout = self._boundary_scout
        if scout is not None:
            try:
                replay_for_scout = tail_audio[boundary_offset:]
                snapshot = scout.reset(replay_audio=replay_for_scout)
                self._scout_window_start_seconds = snapshot.start_time
                self._scout_vad_offset_seconds = boundary_offset / SAMPLE_RATE
            except Exception as error:
                self._disable_boundary_scout(error)

        self._semantic_stats["commits"] += 1
        self._semantic_stats["trimmed_samples"] += removed
        if method == "nemotron_timestamp":
            self._semantic_stats["scout_cuts"] += 1
        else:
            self._semantic_stats["qwen_locator_cuts"] += 1

        final_raw = dict(raw or {})
        final_raw["text"] = commit.prefix
        # The presentation layer uses this exact known suffix to hand the
        # same subtitle stream from a semantic final to its live replacement.
        final_raw["semantic_suffix_text"] = commit.suffix
        final_raw["semantic_boundary"] = {
            "method": method,
            "agreement_count": commit.agreement_count,
            "boundary_seconds": round(boundary_seconds, 4),
            "decision_window_seconds": round(audio.size / SAMPLE_RATE, 4),
            "trimmed_seconds": round(removed / SAMPLE_RATE, 4),
            "replay_seconds": round(boundary_offset / SAMPLE_RATE, 4),
        }
        logger.info(
            "[semantic-boundary] commit method=%s boundary=%.2fs trim=%.2fs "
            "replay=%.2fs prefix=%r",
            method,
            boundary_seconds,
            removed / SAMPLE_RATE,
            boundary_offset / SAMPLE_RATE,
            commit.prefix,
        )
        try:
            self._emit_final(commit.prefix, final_raw, keep_replay=True)

            # Do not make the user wait for another Qwen pass over the replay
            # tail. The detector's newest hypothesis already contains the
            # suffix, and the queued tail pass can revise it if needed.
            if commit.suffix.strip():
                suffix_raw = dict(raw or {})
                suffix_raw["text"] = commit.suffix
                self._last_partial_text = commit.suffix
                self._last_partial_raw = suffix_raw
                self._last_emitted_partial_text = commit.suffix
                # This is a text handoff, not a fresh recognition snapshot;
                # endpointing must still enqueue the real tail final.
                self._last_partial_voiced_seq = -1
                self._on_result(commit.suffix, False, suffix_raw)
        finally:
            # Preserve the actual replay PCM even if presentation raises. The
            # serialized queue remains the authority for later correction and
            # finalization.
            self._last_partial_time = time.monotonic()
            if tail_audio.size >= int(0.4 * SAMPLE_RATE):
                self._last_boundary_scan_samples = tail_audio.size
                self._enqueue_transcribe(tail_audio, is_final=False, present=True)
        return True

    def _on_transcription_done(
        self, future: Future, *, request: _TranscriptionRequest
    ) -> None:
        failure: Exception | None = None
        try:
            payload = future.result()
        except Exception as error:  # runtime safety
            logger.exception("Local Qwen ASR failed")
            failure = error
            payload = None

        with self._lock:
            try:
                if payload is not None:
                    text, raw = payload
                    if request.is_final:
                        text, raw = self._deduplicate_replay(text, raw)
                        if text:
                            self._emit_final(text, raw)
                    elif request.stream_id == self._stream_id:
                        text, raw = self._deduplicate_replay(text, raw)
                        self._last_partial_voiced_seq = request.voiced_seq
                        self._last_partial_raw = raw
                        self._last_partial_text = text
                        decision: BoundaryCommit | None = None
                        if (
                            text
                            and self._semantic_boundary_enabled()
                            and self._vad.is_speaking
                            and request.audio.size >= self._semantic_cut_min_samples()
                        ):
                            decision = self._boundary_detector.observe(text)

                        # Present the complete current hypothesis before any
                        # physical boundary lookup. The Qwen-prefix locator can
                        # run several independent probes, so it must never
                        # delay source text we already know.
                        if (
                            request.present
                            and text
                            and text != self._last_emitted_partial_text
                        ):
                            self._last_emitted_partial_text = text
                            self._on_result(text, False, raw)
                        if decision is not None:
                            self._commit_semantic_boundary(
                                decision, audio=request.audio, raw=raw
                            )
                elif request.is_final or request.stream_id == self._stream_id:
                    self._last_partial_voiced_seq = -1
            except Exception as error:
                failure = error
                logger.exception("Local Qwen ASR result callback failed")
            finally:
                if request.is_final:
                    # A semantic cut's pre-roll belongs only to the suffix
                    # utterance closed by this final request.  End that replay
                    # epoch even when ASR returns None/empty or raises, or the
                    # next independent utterance could lose a coincidentally
                    # matching prefix.
                    self._finish_stream_state()
                # One bad presentation callback must not strand later finals or
                # leave stop() waiting forever on a non-empty FIFO.
                self._try_start_transcribe_locked()

        if failure is not None:
            try:
                self._on_error(failure)
            except Exception:
                logger.exception("Local Qwen ASR error callback failed")

    def _try_start_transcribe_locked(self) -> None:
        if self._executor is None:
            self._transcription_idle.notify_all()
            return
        if self._active_future is not None and not self._active_future.done():
            return
        if self._waiting_finals:
            request = self._waiting_finals.popleft()
        elif self._waiting_partial is not None:
            request, self._waiting_partial = self._waiting_partial, None
        else:
            self._active_future = None
            self._transcription_idle.notify_all()
            return
        self._active_future = self._executor.submit(
            self._transcribe, request.audio, is_final=request.is_final
        )
        self._active_future.add_done_callback(
            lambda done, req=request: self._on_transcription_done(done, request=req)
        )

    def _wait_for_transcriptions(self) -> None:
        """Let the serialized partial/final request chain drain before shutdown."""
        with self._transcription_idle:
            while (
                self._waiting_finals
                or self._waiting_partial is not None
                or (
                    self._active_future is not None
                    and not self._active_future.done()
                )
            ):
                self._transcription_idle.wait(timeout=0.2)

    def _enqueue_transcribe(
        self,
        audio: np.ndarray,
        *,
        is_final: bool,
        present: bool = True,
        stream_id: int | None = None,
    ) -> None:
        if audio.size == 0:
            return
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen-asr")
            request_stream_id = self._stream_id if stream_id is None else int(stream_id)
            request = _TranscriptionRequest(
                audio=audio.copy(),
                is_final=is_final,
                present=bool(present),
                voiced_seq=self._voiced_chunk_seq,
                stream_id=request_stream_id,
            )
            if is_final:
                self._waiting_finals.append(request)
                if (
                    self._waiting_partial is not None
                    and self._waiting_partial.stream_id == request_stream_id
                ):
                    self._waiting_partial = None
            else:
                previous = self._waiting_partial
                if previous is not None and previous.stream_id == request_stream_id:
                    request = _TranscriptionRequest(
                        audio=request.audio,
                        is_final=False,
                        present=request.present or previous.present,
                        voiced_seq=request.voiced_seq,
                        stream_id=request.stream_id,
                    )
                self._waiting_partial = request
            self._try_start_transcribe_locked()

    def _semantic_cut_min_samples(self) -> int:
        # Prefix location runs extra ASR probes. Amortize that work over a
        # useful audio span, then cut at the latest confirmed sentence end.
        # A shorter user-configured hard cap should still leave time to cut.
        seconds = max(1.0, float(getattr(config, "LOCAL_SEMANTIC_BOUNDARY_MIN_AUDIO_SECONDS", 8.0)))
        return min(int(seconds * SAMPLE_RATE), max(SAMPLE_RATE, self._input_cap_samples() // 2))

    def _maybe_emit_partial(self) -> None:
        if not config.LOCAL_INCREMENTAL_ASR or self._audio_queue.qsize() >= 8:
            return
        silence = self._vad.current_silence_duration
        if silence <= 0:
            self._silence_trigger_armed = True
        window_samples = self._vad.speech_samples
        if window_samples < SAMPLE_RATE:
            return
        now = time.monotonic()
        elapsed = now - self._last_partial_time
        min_interval = float(config.LOCAL_INCREMENTAL_MIN_UPDATE_INTERVAL)
        fallback = max(min_interval, float(config.LOCAL_INCREMENTAL_MAX_UPDATE_INTERVAL))
        trigger_silence = float(config.LOCAL_INCREMENTAL_TRIGGER_SILENCE_MS) / 1000.0
        silence_hit = trigger_silence > 0 and self._silence_trigger_armed and silence >= trigger_silence
        present = (silence_hit and elapsed >= min_interval) or (
            not silence_hit and elapsed >= fallback
        )

        scan_due = False
        if self._semantic_boundary_enabled():
            scan_interval = float(
                getattr(config, "LOCAL_SEMANTIC_BOUNDARY_SCAN_INTERVAL", 1.0)
            )
            if self._boundary_detector.has_provisional_boundary:
                scan_interval = min(
                    scan_interval,
                    float(
                        getattr(
                            config,
                            "LOCAL_SEMANTIC_BOUNDARY_CONFIRM_INTERVAL",
                            0.35,
                        )
                    ),
                )
            else:
                # Ordinary scans must not bypass the pause-first update
                # cadence. A known boundary may still get a quick confirming
                # scan so safe audio trimming does not wait another full gap.
                scan_interval = max(scan_interval, fallback)
            scan_interval_samples = int(
                scan_interval * SAMPLE_RATE
            )
            scan_due = (
                window_samples - self._last_boundary_scan_samples
                >= max(VAD_CHUNK_SAMPLES, scan_interval_samples)
            )
        # Semantic scans are live source hypotheses, not invisible background
        # work. Their result may revise the displayed text and is also what
        # supplies the second agreement observation for a safe commit.
        present = present or scan_due
        if not present:
            return
        # Building the VAD snapshot copies the full growing window. Do it only
        # after the time/size gates above say that an ASR request is due.
        peek = self._vad.peek_buffer()
        if peek is None:
            return
        audio, _duration = peek
        if present and silence_hit:
            self._silence_trigger_armed = False
        if present:
            self._last_partial_time = now
        if scan_due or present:
            self._last_boundary_scan_samples = audio.size
        self._enqueue_transcribe(audio, is_final=False, present=present)

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

    def _finish_vad_segment(self, segment: np.ndarray) -> None:
        old_stream_id = self._stream_id
        self._stream_id += 1
        if (
            self._waiting_partial is not None
            and self._waiting_partial.stream_id == old_stream_id
        ):
            self._waiting_partial = None
        self._reset_boundary_state()
        self._last_partial_time = time.monotonic()
        self._silence_trigger_armed = True
        self._timeline_contiguous = True

        reused = self._try_reuse_partial_as_final()
        if reused is not None:
            self._emit_final(reused, self._last_partial_raw)
        else:
            self._enqueue_transcribe(
                segment,
                is_final=True,
                present=True,
                stream_id=old_stream_id,
            )

    def _process_chunk(self, chunk: np.ndarray) -> None:
        if self._vad.is_speaking and self._vad.speech_samples >= self._input_cap_samples():
            # Close the capped window before consuming this real chunk.  The
            # old zero-padding approach discarded live speech until the VAD
            # accumulated enough fake silence to endpoint.
            capped = self._vad.force_flush()
            if capped is not None:
                self._finish_vad_segment(capped)
        was_speaking = self._vad.is_speaking
        segment = self._vad.process_chunk(chunk)
        if self._vad.is_speaking and self._vad._silence_counter == 0:
            self._voiced_chunk_seq += 1
        if segment is not None:
            self._finish_vad_segment(segment)
            return
        if self._vad.is_speaking:
            if not was_speaking:
                self._last_partial_time = time.monotonic()
                peek = self._vad.peek_buffer()
                if peek is not None:
                    self._start_boundary_scout_window(peek[0])
            else:
                self._feed_boundary_scout(chunk)
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
            self._finish_vad_segment(segment)
        self._last_partial_time = time.monotonic()
        self._silence_trigger_armed = True

    def _worker_loop(self) -> None:
        try:
            while True:
                self._audio_ready.wait(timeout=0.2)
                with self._lock:
                    with self._queue_gate:
                        if not self._running:
                            return
                        if self._paused:
                            self._audio_ready.clear()
                            continue
                        try:
                            data = self._audio_queue.get_nowait()
                        except queue.Empty:
                            self._audio_ready.clear()
                            continue
                        if self._audio_queue.empty():
                            self._audio_ready.clear()
                    self._feed_samples(self._pcm_to_float32(data))
        except Exception as error:
            logger.exception("Local audio worker failed")
            self._on_error(error)

    def start(self) -> None:
        with self._lock:
            self._ensure_engine()
            self._ensure_boundary_scout()
            if self._running:
                return
            with self._queue_gate:
                # Never carry capture bytes from an earlier stopped/error
                # session into a new ASR timeline.
                self._audio_queue = queue.Queue(maxsize=128)
                self._audio_ready.clear()
                self._paused = False
                self._running = True
            self._stream_id = 0
            self._pending_samples = np.array([], dtype=np.float32)
            self._reset_engine_draft()
            self._last_partial_text = ""
            self._last_emitted_partial_text = ""
            self._last_partial_raw = None
            self._last_partial_time = time.monotonic()
            self._last_boundary_scan_samples = 0
            self._silence_trigger_armed = True
            self._voiced_chunk_seq = 0
            self._last_partial_voiced_seq = -1
            self._timeline_contiguous = True
            self._replayed_committed_text = ""
            self._replay_expected_suffix = ""
            self._replay_max_lexical_chars = 0
            self._reset_boundary_state()
            self._waiting_partial = None
            self._waiting_finals.clear()
            self._active_future = None
            for name in self._semantic_stats:
                self._semantic_stats[name] = 0
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen-asr")
            self._worker = threading.Thread(target=self._worker_loop, name="LocalQwenAudio", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        # Close admission before draining.  A send already inside the tiny gate
        # completes first and is therefore included in the drain; later sends
        # observe ``_running=False`` and are rejected.
        with self._queue_gate:
            self._running = False
            self._paused = False
        self._audio_ready.set()
        with self._lock:
            self._drain_audio_queue_locked()
            self._finalize_current_segment_locked()
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        if self._executor is not None:
            # A final queued by ``_finalize_current_segment_locked`` can sit
            # behind an already-running partial.  Keep the executor accepting
            # callback-scheduled work until that FIFO chain is truly empty.
            self._wait_for_transcriptions()
            self._executor.shutdown(wait=True)
            self._executor = None
        self._active_future = None
        self._waiting_partial = None
        self._waiting_finals.clear()
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.unload()
                finally:
                    self._engine = None
            logger.info("[semantic-boundary] session stats: %s", self._semantic_stats)
            self._boundary_scout = None
            self._vad.reset()
            self._reset_boundary_state()

    def pause(self) -> None:
        with self._queue_gate:
            self._paused = True
        with self._lock:
            self._drain_audio_queue_locked()
            self._finalize_current_segment_locked()

    def resume(self) -> None:
        with self._queue_gate:
            self._paused = False
        self._audio_ready.set()

    def send(self, data: bytes) -> None:
        """WebSocket-compatible sink used directly by AudioStreamer."""
        if not data:
            return
        with self._queue_gate:
            if not self._running or self._paused:
                return
            try:
                self._audio_queue.put_nowait(data)
            except queue.Full:
                self._timeline_contiguous = False
                logger.warning(
                    "[local-asr] Audio queue overflow: semantic audio trimming is "
                    "disabled until the next VAD segment"
                )
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._audio_queue.put_nowait(data)
                except queue.Full:
                    return
            self._audio_ready.set()
