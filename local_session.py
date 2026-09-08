"""On-device Qwen3-ASR + Hy-MT2 provider integrated with RealtimeSubtitle."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

import config
import soniox_session
from local_inference.model_manager import get_all_local_models_status
from local_inference.recognizer import LocalQwenRecognizer
from local_inference.subtitle_pipeline import LocalSubtitlePipeline
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

    ASR publishes sticky source rows immediately. Translation runs separately,
    with one revision chain per row and one shared local model.
    """

    def __init__(self, logger_obj, broadcast_callback):
        super().__init__(logger_obj, broadcast_callback)
        self._recognizer: LocalQwenRecognizer | None = None
        self._translator: HyMT2API | None = None
        self._subtitle_pipeline: LocalSubtitlePipeline | None = None
        self._local_live_rows: dict[str, dict] = {}
        self._local_stop_event: threading.Event | None = None
        self._startup_error: str | None = None
        self._inference_backend = getattr(config, "LOCAL_INFERENCE_BACKEND", "local")
        self._server_url = getattr(config, "LOCAL_INFERENCE_SERVER_URL", "")
        self._remote_timeout = getattr(config, "LOCAL_INFERENCE_REMOTE_TIMEOUT_SECONDS", 60)
        self._translation_device = config.LOCAL_TRANSLATION_DEVICE
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
        if self._subtitle_pipeline is None or self._local_stop_event is None:
            return
        if self._local_stop_event.is_set() and not is_final:
            return
        detected = config.normalize_language_code((raw or {}).get("language")) or "en"
        self._present_recognition(text, is_final, detected, raw)

    def _present_recognition(
        self, text: str, is_final: bool, source_lang: str, raw: dict | None = None,
    ) -> None:
        pipeline = self._subtitle_pipeline
        if not text or self.loop is None or pipeline is None:
            return
        target_lang = config.normalize_language_code(self.get_translation_target_lang()) or "zh"
        mode = str(self.translation or "one_way").strip().lower()
        pipeline.update(
            text, is_final, source_lang, target_lang,
            translation_enabled=mode != "none",
            semantic_suffix=(raw or {}).get("semantic_suffix_text") if is_final else None,
        )

    def _make_row_translator(self) -> HyMT2API:
        # Independent draft/history state; load_local_engine acquires the same
        # registry entry held by the prewarmed session translator.
        translator = HyMT2API(
            backend=self._inference_backend, local_device=self._translation_device,
            websocket_url=self._server_url if self._inference_backend == "remote" else "",
            timeout=self._remote_timeout, max_retries=0 if self._inference_backend == "remote" else 3,
        )
        translator.load_local_engine()
        return translator

    def _check_models(self, translation_enabled: bool, *, prepare_remote: bool = False) -> None:
        if self._inference_backend == "remote":
            from local_inference.remote_client import normalize_server_url, probe_remote_server, remote_readiness_error
            if not normalize_server_url(self._server_url):
                raise RuntimeError("远程推理服务器地址为空")
            if prepare_remote:
                status = probe_remote_server(self._server_url)
                error = remote_readiness_error(status, translation_enabled=translation_enabled)
                if error:
                    raise RuntimeError(error)
                if config.VAD_ENABLED and config.LOCAL_VAD_MODE == "silero":
                    # Only the small CPU pause detector remains on this PC.
                    from local_inference.model_manager import download_silero
                    download_silero()
            return
        status = get_all_local_models_status()
        asr_status = status["asr"]["qwen3-asr"]
        if not asr_status["ready"]:
            issues = ", ".join(asr_status.get("runtime_issues") or [])
            raise RuntimeError("Qwen3-ASR 未就绪" + (f"（缺少: {issues}）" if issues else ""))
        if translation_enabled and not status["translation"]["hymt2"]["ready"]:
            hymt = status["translation"]["hymt2"]
            issues = ", ".join(hymt.get("runtime_issues") or [])
            raise RuntimeError("Hy-MT2 未就绪；请把 GGUF 放入 "
                               + str(hymt.get("install_dir") or "local_models/hymt2")
                               + (f"（缺少: {issues}）" if issues else ""))

    def _local_row_tokens(self, row: dict) -> list[dict]:
        source = self._token(
            row["source"], final=row["is_final"], language=row["source_language"],
            source_language=row["source_language"], translation=False,
        )
        source["llm_sentence_id"] = row["id"]
        tokens = [source]
        if row["translation"]:
            translated = self._token(
                row["translation"], final=row["is_final"], language=row["target_language"],
                source_language=row["source_language"], translation=True,
            )
            translated["llm_sentence_id"] = row["id"]
            tokens.append(translated)
        return tokens

    def _publish_local_frame(self, frame: dict) -> None:
        """Keep local row identities end-to-end; ordinary IPC tokens coexist."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        for row_id in frame.get("local_removed_segment_ids", []):
            self._local_live_rows.pop(row_id, None)
        for row in frame.get("local_segments", []):
            if row["is_final"]:
                self._local_live_rows.pop(row["id"], None)
            else:
                self._local_live_rows[row["id"]] = row
        with self._ipc_lock:
            if self._ipc_pending_final:
                frame["final_tokens"] = [self._minify_token({
                    "text": self._ipc_pending_final, "speaker": "0",
                    "translation_status": "original", "is_final": True,
                }, is_final=True)]
                self._ipc_pending_final = ""
            frame["non_final_tokens"] = ([self._minify_token({
                "text": self._ipc_ongoing_text, "speaker": "0",
                "translation_status": "original", "is_final": False,
            }, is_final=False)] if self._ipc_ongoing_text else [])
        asyncio.run_coroutine_threadsafe(self.broadcast_callback(frame), loop)
        if self.get_osc_translation_enabled():
            lines = []
            for row in sorted(self._local_live_rows.values(), key=lambda value: value["order"]):
                tokens = self._local_row_tokens(row)
                text = self._select_osc_text(row["translation"], row["source"], tokens[:1])
                if text:
                    lines.append(text)
            key = "\n".join(lines)
            if lines and self._osc_live_last_text_by_speaker.get("0") != key:
                self._osc_live_last_text_by_speaker["0"] = key
                soniox_session.osc_manager.send_preview_messages_with_history(lines, ongoing=True, speaker="0")

    def _finalize_local_row(self, row: dict) -> None:
        tokens = self._local_row_tokens(row)
        if not self.is_paused:
            self.logger.write_to_log(tokens)
        if self.loop is not None and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._finalize_sentence_async("0", tokens[:1], tokens[1:], row["id"]), self.loop,
            )

    def _run_local_session(self) -> None:
        run_stop_event = self._local_stop_event
        translator: HyMT2API | None = None
        recognizer: LocalQwenRecognizer | None = None
        try:
            translation_enabled = str(self.translation or "one_way").strip().lower() != "none"
            self._check_models(translation_enabled, prepare_remote=True)

            if translation_enabled:
                translator = self._make_row_translator()
                self._translator = translator

            self._subtitle_pipeline = LocalSubtitlePipeline(
                self._make_row_translator, self._publish_local_frame,
                self._finalize_local_row, self._broadcast_local_error,
            )
            recognizer = LocalQwenRecognizer(
                self._on_recognition_result,
                self._broadcast_local_error,
                source_language="auto",
                inference_backend=self._inference_backend,
                server_url=self._server_url,
                remote_timeout=self._remote_timeout,
            )
            recognizer.start()
            self._recognizer = recognizer
            self.ws = recognizer
            self._start_audio_streamer(recognizer)
            logger.info("Qwen3-ASR session started (inference=%s)", self._inference_backend)

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

            pipeline = self._subtitle_pipeline
            if pipeline is not None:
                pipeline.close()
            self._subtitle_pipeline = None

            if translator is not None:
                translator.close()
            self._translator = None
            self._clear_stopped_local_state(run_stop_event)
            logger.info("Local inference session stopped")

    def _clear_stopped_local_state(
        self, expected_stop_event: threading.Event | None
    ) -> None:
        """Clear one completed run without clobbering a newer session."""
        if self._local_stop_event is not expected_stop_event:
            return
        self._local_stop_event = None
        self.stop_event = None
        self.ws = None
        self.thread = None
        self._sentence_buffers.clear()
        self._pending_endpoint_speakers.clear()
        self._pairer.flush_all()
        self._pending_boundaries.clear()
        self._reset_osc_live_state()
        self._local_live_rows.clear()

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
        self._inference_backend = getattr(config, "LOCAL_INFERENCE_BACKEND", "local")
        self._server_url = getattr(config, "LOCAL_INFERENCE_SERVER_URL", "")
        self._remote_timeout = getattr(config, "LOCAL_INFERENCE_REMOTE_TIMEOUT_SECONDS", 60)
        self._translation_device = config.LOCAL_TRANSLATION_DEVICE
        requested_translation = str(translation or "one_way").strip().lower()
        self._check_models(requested_translation != "none")
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
        if thread is None or not thread.is_alive():
            self._clear_stopped_local_state(stop_event)
        else:
            # recognizer.stop() deliberately drains the serialized ASR/final
            # chain.  Keep its event and subtitle pipeline reachable until
            # the run thread finishes, even when a slow CPU final exceeds this
            # caller's bounded join.
            logger.warning(
                "Local inference session is still draining after 15 seconds; "
                "final state will be cleared by the run thread"
            )

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
