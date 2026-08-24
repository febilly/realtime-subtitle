"""On-device Qwen3-ASR + Hy-MT2 provider integrated with RealtimeSubtitle."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from typing import Optional

import config
import soniox_session
from local_inference.model_manager import get_all_local_models_status
from local_inference.recognizer import LocalQwenRecognizer
from soniox_session import SonioxSession
from streaming_translation import HyMT2API

logger = logging.getLogger(__name__)
ipc_server = None


def bind_ipc_server(server) -> None:
    """Bind IPC to the inherited Soniox display/logging implementation."""
    global ipc_server
    ipc_server = server
    soniox_session.ipc_server = server


class LocalInferenceSession(SonioxSession):
    """Drop-in session using local models and the existing subtitle protocol.

    The inference pipeline is serialized in ASR event order.  This is important
    for Hy-MT2's mutable per-utterance revision state: a final update can never
    overtake an earlier partial update.
    """

    def __init__(self, logger_obj, broadcast_callback):
        super().__init__(logger_obj, broadcast_callback)
        self._recognizer: LocalQwenRecognizer | None = None
        self._translator: HyMT2API | None = None
        self._presentation_executor: ThreadPoolExecutor | None = None
        self._all_final_tokens: list[dict] = []
        self._local_stop_event: threading.Event | None = None
        self._startup_error: str | None = None
        self._llm_refine_mode = "off"
        self._suppress_soniox_translation = False
        self._segment_mode = "punctuation"

    def get_translation_mode(self) -> str:
        return "fast"

    def set_translation_mode(self, mode: str) -> tuple[bool, str, bool]:
        # Hy-MT2 is the provider's built-in fast path; cloud LLM modes do not
        # apply to an offline provider.
        return True, "fast", False

    def set_llm_refine_mode(self, mode: str) -> tuple[bool, str]:
        self._llm_refine_mode = "off"
        return True, "off"

    def _source_drives_segmentation(self, source_tokens: list[dict]) -> bool:
        if str(self.translation or "").strip().lower() == "none":
            return True
        return super()._source_drives_segmentation(source_tokens)

    def _pairing_expects_translation(self, entry) -> bool:
        if str(self.translation or "").strip().lower() == "none":
            return False
        return super()._pairing_expects_translation(entry)

    def _broadcast_local_error(self, error: Exception | str) -> None:
        message = str(error)
        logger.error("Local inference session error: %s", message)
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback(
                    {"type": "error", "code": "local_inference_error", "message": message}
                ),
                loop,
            )
        except Exception:
            logger.debug("Failed to broadcast local inference error", exc_info=True)

    @staticmethod
    def _token(
        text: str,
        *,
        final: bool,
        language: str,
        source_language: str,
        translation: bool,
    ) -> dict:
        return {
            "text": text,
            "is_final": bool(final),
            "speaker": "0",
            "translation_status": "translation" if translation else "original",
            "language": language,
            "source_language": source_language,
        }

    def _on_recognition_result(self, text: str, is_final: bool, raw: dict | None) -> None:
        executor = self._presentation_executor
        if executor is None or self._local_stop_event is None:
            return
        if self._local_stop_event.is_set() and not is_final:
            return
        detected = config.normalize_language_code((raw or {}).get("language")) or "en"
        try:
            executor.submit(self._present_recognition, text, is_final, detected)
        except RuntimeError:
            pass

    def _present_recognition(self, text: str, is_final: bool, source_lang: str) -> None:
        if not text or self.loop is None:
            return
        target_lang = config.normalize_language_code(self.get_translation_target_lang()) or "zh"
        tokens = [
            self._token(
                text,
                final=is_final,
                language=source_lang,
                source_language=source_lang,
                translation=False,
            )
        ]

        mode = str(self.translation or "one_way").strip().lower()
        if mode != "none" and source_lang != target_lang and self._translator is not None:
            translated = self._translator.translate(
                text,
                source_language=source_lang,
                target_language=target_lang,
                is_partial=not is_final,
            ).strip()
            if translated.startswith("[ERROR]"):
                self._broadcast_local_error(translated)
            elif translated:
                tokens.append(
                    self._token(
                        translated,
                        final=is_final,
                        language=target_lang,
                        source_language=source_lang,
                        translation=True,
                    )
                )

        response = {"tokens": tokens, "endpoint_detected": bool(is_final)}
        self.last_sent_count, _should_end, _reason = self._process_soniox_response(
            response,
            self._all_final_tokens,
            self.last_sent_count,
            self.loop,
        )

    def _run_local_session(self) -> None:
        translator: HyMT2API | None = None
        recognizer: LocalQwenRecognizer | None = None
        try:
            status = get_all_local_models_status()
            asr_status = status["asr"]["qwen3-asr"]
            if not asr_status["ready"]:
                issues = ", ".join(asr_status.get("runtime_issues") or [])
                raise RuntimeError("Qwen3-ASR 未就绪" + (f"（缺少: {issues}）" if issues else ""))

            translation_enabled = str(self.translation or "one_way").strip().lower() != "none"
            if translation_enabled and not status["translation"]["hymt2"]["ready"]:
                hymt = status["translation"]["hymt2"]
                issues = ", ".join(hymt.get("runtime_issues") or [])
                raise RuntimeError(
                    "Hy-MT2 未就绪；请把 GGUF 放入 "
                    + str(hymt.get("install_dir") or "local_models/hymt2")
                    + (f"（缺少: {issues}）" if issues else "")
                )

            if translation_enabled:
                translator = HyMT2API(
                    backend="local",
                    local_device=config.LOCAL_TRANSLATION_DEVICE,
                )
                translator.load_local_engine()
                self._translator = translator

            recognizer = LocalQwenRecognizer(
                self._on_recognition_result,
                self._broadcast_local_error,
                source_language="auto",
            )
            recognizer.start()
            self._recognizer = recognizer
            self.ws = recognizer
            self._start_audio_streamer(recognizer)
            logger.info("Local Qwen3-ASR session started")

            stop_event = self._local_stop_event
            while stop_event is not None and not stop_event.wait(0.2):
                pass
        except Exception as error:
            self._startup_error = str(error)
            self._broadcast_local_error(error)
        finally:
            self._stop_audio_streamer()
            if recognizer is not None:
                try:
                    recognizer.stop()
                except Exception:
                    logger.exception("Failed to stop local Qwen recognizer")
            self._recognizer = None
            self.ws = None

            presentation = self._presentation_executor
            if presentation is not None:
                presentation.shutdown(wait=True)
            self._presentation_executor = None

            if translator is not None:
                translator.close()
            self._translator = None
            logger.info("Local inference session stopped")

    def start(
        self,
        api_key: Optional[str],
        audio_format: str,
        translation: str,
        loop: asyncio.AbstractEventLoop,
        translation_target_lang: Optional[str] = None,
    ) -> bool:
        if self.thread and self.thread.is_alive():
            return False
        status = get_all_local_models_status()
        asr_status = status["asr"]["qwen3-asr"]
        if not asr_status["ready"]:
            issues = ", ".join(asr_status.get("runtime_issues") or [])
            raise RuntimeError(
                "Qwen3-ASR 未就绪" + (f"（缺少: {issues}）" if issues else "")
            )
        requested_translation = str(translation or "one_way").strip().lower()
        if requested_translation != "none" and not status["translation"]["hymt2"]["ready"]:
            hymt = status["translation"]["hymt2"]
            issues = ", ".join(hymt.get("runtime_issues") or [])
            raise RuntimeError(
                "Hy-MT2 未就绪；请把 GGUF 放入 "
                + str(hymt.get("install_dir") or "local_models/hymt2")
                + (f"（缺少: {issues}）" if issues else "")
            )
        if translation_target_lang is not None:
            ok, message = self.set_translation_target_lang(translation_target_lang)
            if not ok:
                raise ValueError(message)

        self.api_key = "local-on-device"
        self.audio_format = audio_format
        self.translation = requested_translation
        if self.translation == "two_way":
            self.translation = "one_way"
        self.loop = loop
        self.is_paused = False
        self.last_sent_count = 0
        self._all_final_tokens = []
        self._startup_error = None
        self._sentence_buffers.clear()
        self._pending_endpoint_speakers.clear()
        self._pairer.flush_all()
        self._pending_boundaries.clear()
        self._reset_osc_live_state()
        if self.logger.enabled and self.logger.log_file is None:
            self.logger.init_log_file()
        self._local_stop_event = threading.Event()
        self.stop_event = self._local_stop_event
        self._presentation_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="local-subtitle"
        )
        self.thread = threading.Thread(
            target=self._run_local_session,
            name="LocalInferenceSession",
            daemon=True,
        )
        self.thread.start()
        return True

    def stop(self) -> None:
        stop_event = self._local_stop_event
        if stop_event is not None:
            stop_event.set()
        self._stop_audio_streamer()
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=15.0)
        if thread and not thread.is_alive():
            self.thread = None
        self._local_stop_event = None
        self.stop_event = None
        self.ws = None
        self._sentence_buffers.clear()
        self._pending_endpoint_speakers.clear()
        self._pairer.flush_all()
        self._pending_boundaries.clear()
        self._reset_osc_live_state()

    def pause(self) -> bool:
        if self.is_paused:
            return False
        self.is_paused = True
        self.stop()
        return True

    def resume(
        self,
        api_key: Optional[str] = None,
        audio_format: Optional[str] = None,
        translation: Optional[str] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        translation_target_lang: Optional[str] = None,
    ) -> bool:
        if not self.is_paused:
            return False
        return self.start(
            "local-on-device",
            audio_format or self.audio_format or "pcm_s16le",
            translation or self.translation or "one_way",
            loop or self.loop,
            translation_target_lang=translation_target_lang or self.translation_target_lang,
        )
