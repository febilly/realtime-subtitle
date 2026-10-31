"""On-device Qwen3-ASR + Hy-MT2 provider integrated with RealtimeSubtitle."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Optional

import config
import realtime_session_base
import soniox_session
from local_inference.model_manager import get_all_local_models_status
from local_inference.recognizer import LocalQwenRecognizer
from local_inference.subtitle_pipeline import LocalSubtitlePipeline
from local_relay import LocalRelayLink
from soniox_session import SonioxSession
from streaming_translation import HyMT2API

logger = logging.getLogger(__name__)
ipc_server = None


def bind_ipc_server(server) -> None:
    """Bind IPC to the inherited session display/logging implementation."""
    global ipc_server
    ipc_server = server
    realtime_session_base.ipc_server = server


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
        # Hy-MT2 is this provider's built-in translation: 快速 shows it, 混合
        # refines it with the LLM at sentence end, 准确 skips it entirely and
        # translates with the LLM only (_suppress_soniox_translation).
        self._llm_refine_mode = "off"
        self._suppress_soniox_translation = False
        self._segment_mode = "punctuation"
        self._mt_lock = threading.Lock()
        self._mt_generation = 0
        # Hosted mode only: LLM-only relay session for the whole run (see
        # local_relay). Carries hosted LLM frames and session telemetry.
        self._relay_link: LocalRelayLink | None = None

    def _machine_translation_wanted(self) -> bool:
        mode = str(self.translation or "one_way").strip().lower()
        return mode != "none" and not self._suppress_soniox_translation

    def set_translation_mode(self, mode: str) -> tuple[bool, str, bool]:
        # Same mapping as soniox; the local switch never needs a restart because
        # Hy-MT2 is loaded or released in place below.
        ok, normalized, _needs_restart = super().set_translation_mode(mode)
        if ok:
            self._apply_machine_translation()
        return ok, normalized, False

    def _apply_machine_translation(self) -> None:
        """Load or release Hy-MT2 to match the current mode while running.

        Leaving 快速/混合 releases it at once: users pick 准确 to free VRAM, so
        a delayed unload would look like the switch did nothing.
        """
        pipeline = self._subtitle_pipeline
        if pipeline is None or self._local_stop_event is None:
            return
        wanted = self._machine_translation_wanted()
        pipeline.set_machine_translation(wanted)
        with self._mt_lock:
            self._mt_generation += 1
            generation = self._mt_generation
            translator = self._translator
            if not wanted:
                self._translator = None
        if not wanted:
            if translator is not None:
                translator.close()
            return
        if translator is not None:
            return
        threading.Thread(
            target=self._load_machine_translation, args=(generation,),
            name="LocalTranslationLoad", daemon=True,
        ).start()

    def _load_machine_translation(self, generation: int) -> None:
        try:
            self._check_models(True)
            translator = self._make_row_translator()
        except Exception as error:
            self._broadcast_local_error(error)
            return
        with self._mt_lock:
            current = generation == self._mt_generation and self._local_stop_event is not None
            if current:
                self._translator = translator
        if not current:
            # The mode changed again (or the run stopped) while loading.
            translator.close()

    def _start_relay_link(self) -> None:
        if not config.RELAY_MODE or not config.RELAY_TOKEN or self._relay_link is not None:
            return
        self.relay_run_id = uuid.uuid4().hex
        link = LocalRelayLink(self._hosted_llm.handle_frame, self._on_relay_link_state, self.relay_run_id)
        self._relay_link = link
        link.start()

    def _stop_relay_link(self) -> None:
        link, self._relay_link = self._relay_link, None
        if link is not None:
            link.stop()

    def _on_relay_link_state(self, connected: bool, terminal_tag: str | None) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        self._notify_relay_session(loop, "session_connected" if connected else "session_disconnected")
        if terminal_tag:
            # Recognition keeps running; only hosted LLM translation is lost.
            self._broadcast_local_error(f"托管服务器连接已断开（{terminal_tag}），LLM 翻译不可用")

    def _hosted_llm_send(self, text: str) -> None:
        link = self._relay_link
        if link is None:
            raise RuntimeError("hosted relay is not connected")
        link.send(text)

    def _on_hosted_llm_disabled(self, reason: str) -> None:
        # Falling back to 快速 means Hy-MT2 again; it loads in place, so the
        # UI must not be told to restart (that would reload the ASR model).
        self._suppress_soniox_translation = False
        super()._on_hosted_llm_disabled(reason)
        self._apply_machine_translation()

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
            if not hymt.get("model_ready"):
                raise RuntimeError("Hy-MT2 翻译模型未下载；请在设置里下载，或改用准确模式")
            raise RuntimeError("Hy-MT2 未就绪" + (f"（缺少: {issues}）" if issues else ""))

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
        recognizer: LocalQwenRecognizer | None = None
        try:
            machine_translation = self._machine_translation_wanted()
            self._check_models(machine_translation, prepare_remote=True)

            if machine_translation:
                # Held for the whole run so per-row translators share one
                # loaded engine; released early when switching to 准确.
                self._translator = self._make_row_translator()

            pipeline = LocalSubtitlePipeline(
                self._make_row_translator, self._publish_local_frame,
                self._finalize_local_row, self._broadcast_local_error,
            )
            self._subtitle_pipeline = pipeline
            # Also catches a mode switch that landed while the models loaded.
            self._apply_machine_translation()
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

            with self._mt_lock:
                # Abandon any in-flight background load; it closes its own result.
                self._mt_generation += 1
                translator, self._translator = self._translator, None
            if translator is not None:
                translator.close()
            if self._local_stop_event is run_stop_event:
                self._stop_relay_link()
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
        self._check_models(requested_translation != "none" and not self._suppress_soniox_translation)
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
        self._start_relay_link()
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
        self._stop_relay_link()
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
