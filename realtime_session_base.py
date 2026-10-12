"""Shared base for the realtime recognition sessions (Soniox, Gemini).

Everything provider-neutral lives here: lifecycle (start/stop/pause/resume),
audio capture and devices, token helpers, OSC text selection and live
preview, LLM refine/translate plumbing, and the stream rollover / silence
sleep helpers driven by stream_session_runtime.run_stream_session.

Subclasses set PROVIDER_NAME / CONFIG_PREFIX / CLIENT_MODULE / CHUNK_SIZE and
implement the provider hooks (_open_stream_state, _process_stream_response,
_run_session, _close_ws, _request_stream_finalize,
_warmup_response_ends_stream, _two_way_pair) plus their own segmentation.
"""

import asyncio
import importlib
import inspect
import json
import logging
import threading
import time
import uuid
from typing import Optional, Tuple

from websockets import ConnectionClosed, ConnectionClosedOK

import config
from config import (
    FFMPEG_PATH,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_REFINE_CONTEXT_MIN_COUNT,
    LLM_REFINE_DEFAULT_ENABLED,
    LLM_REFINE_DEFAULT_MODE,
    LLM_REQUEST_HEADERS,
    LLM_REQUEST_JSON,
    MICROPHONE_DEVICE_ID,
    MUTE_MIC_WHEN_VRCHAT_SELF_MUTED,
    OSC_SEND_TEXT_MODE,
    OUTPUT_DEVICE_ID,
    SLEEP_IDLE_SECONDS,
    TWITCH_CHANNEL,
    TWITCH_STREAM_QUALITY,
    USE_TWITCH_AUDIO_STREAM,
    get_llm_api_key,
    normalize_language_code,
)
from audio_capture import AudioStreamer
from audio_router import AudioSendRouter
from hosted_llm import HostedLlmTransport
from llm_client import LlmConfig, chat_completion
from osc_manager import osc_manager
from stream_session_runtime import (
    RealtimeSilenceSender,
    StreamState,
    STREAM_ROLLOVER_FINALIZE_TIMEOUT_SECONDS,
    STREAM_ROLLOVER_FORCE_GUARD_SECONDS,
    STREAM_ROLLOVER_NEAR_LIMIT_RATIO,
    STREAM_ROLLOVER_SWITCH_PATIENCE_SECONDS,
    STREAM_ROLLOVER_WARMUP_DRAIN_LIMIT,
    normalize_east_asian_translation_spacing,
)
import llm_refine
import sentence_segmentation

# Aliases kept so the moved method bodies read exactly as before.
_StreamState = StreamState
_RealtimeSilenceSender = RealtimeSilenceSender

# IPC server (VRChat companion etc.); injected by server.py.
ipc_server = None

logger = logging.getLogger(__name__)

LLM_REFINE_MODES = ("off", "refine", "translate")

# Soniox control markers, also synthesized by the Gemini adapter.
INTERNAL_TOKEN_TEXTS = {"<end>", "<fin>"}


class RealtimeSessionBase:
    """Provider-neutral realtime session; see the module docstring."""

    PROVIDER_NAME = "Realtime"
    # Config key prefix for SLEEP_ON_SILENCE / USES_TEMP_API_KEY / TEMP_KEY_URL.
    CONFIG_PREFIX = ""
    # Module whose get_api_key() mints a fresh temporary key at rollover.
    CLIENT_MODULE = ""
    # Audio samples per captured chunk (16 kHz).
    CHUNK_SIZE = 1600
    # Whether the live OSC preview is kept per speaker.
    OSC_PREVIEW_BY_SPEAKER = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, logger, broadcast_callback):
        self.stop_event = None
        self.thread = None
        self.last_sent_count = 0
        self.logger = logger
        self.broadcast_callback = broadcast_callback
        self.is_paused = False  # 暂停状态标志
        self.ws = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.api_key: Optional[str] = None
        self.audio_format: Optional[str] = None
        self.translation: Optional[str] = None
        self.translation_target_lang: str = "en"
        self.relay_run_id: Optional[str] = None
        self._relay_session_active = False
        self.last_disconnect_payload: Optional[dict] = None
        self.sample_rate = 16000
        self.chunk_size = self.CHUNK_SIZE
        self.audio_source = "twitch" if USE_TWITCH_AUDIO_STREAM else "system"
        self.microphone_device_id = str(MICROPHONE_DEVICE_ID or "").strip()
        self.output_device_id = str(OUTPUT_DEVICE_ID or "").strip()
        self.audio_streamer: Optional[object] = None
        self.audio_lock = threading.Lock()
        self._stream_close_lock = threading.Lock()
        self._vrchat_self_muted = False
        self.osc_translation_enabled = False
        # Display sentence buffer per speaker: feeds line-break decisions only.
        self._sentence_buffers: dict[str, dict] = {}
        self._pending_boundaries = sentence_segmentation.PendingBoundaryState()
        self._llm_sentence_session_id = f"llm-{time.time_ns()}"
        self._llm_sentence_counter = 0
        self._osc_live_last_text_by_speaker: dict[str, str] = {}
        self._refine_context_history: list[dict] = []
        self._llm_context_cycle_count = int(LLM_REFINE_CONTEXT_MIN_COUNT)
        default_mode = str(LLM_REFINE_DEFAULT_MODE or "").strip().lower()
        if default_mode not in LLM_REFINE_MODES:
            default_mode = "refine" if bool(LLM_REFINE_DEFAULT_ENABLED) else "off"
        self._llm_refine_mode = default_mode
        self._osc_send_text_mode = str(OSC_SEND_TEXT_MODE or "smart").strip().lower()
        # 准确 (accurate) mode: the provider runs STT-only and the LLM does the
        # translation, so its built-in translation is suppressed. Gemini always
        # translates, so the flag stays False there.
        self._suppress_soniox_translation = False
        # Hosted (relay) LLM transport: LLM calls ride the STT relay socket.
        self._hosted_llm = HostedLlmTransport()
        self._hosted_llm.on_credits = self._on_hosted_llm_credits
        self._hosted_llm.on_disabled = self._on_hosted_llm_disabled

        try:
            from config import TRANSLATION_TARGET_LANG
            self.translation_target_lang = str(TRANSLATION_TARGET_LANG)
        except Exception:
            self.translation_target_lang = "en"

        if MUTE_MIC_WHEN_VRCHAT_SELF_MUTED:
            osc_manager.set_mute_callback(self._handle_vrchat_mute_self)

        self._ipc_lock = threading.Lock()
        self._ipc_ongoing_text = ""
        self._ipc_pending_final = ""

    def _clear_segmentation_state(self) -> None:
        """Drop all in-flight sentence/line-break state (start and stop)."""
        self._sentence_buffers.clear()
        self._pending_boundaries.clear()

    def _reset_run_state(self) -> None:
        """Provider-specific per-run state reset at start()."""

    def start(
        self,
        api_key: Optional[str],
        audio_format: str,
        translation: str,
        loop: asyncio.AbstractEventLoop,
        translation_target_lang: Optional[str] = None,
    ):
        """启动新的识别会话"""
        if self.thread and self.thread.is_alive():
            print(f"⚠️  {self.PROVIDER_NAME} session already running, start request ignored")
            return False

        if not api_key:
            print(f"❌ Cannot start {self.PROVIDER_NAME} session: API key is missing.")
            self.api_key = None # Clear any previous invalid key
            return False

        self.last_sent_count = 0
        self.is_paused = False
        self.last_disconnect_payload = None
        self.api_key = api_key
        self.audio_format = audio_format
        self.translation = translation
        self.loop = loop
        self._clear_segmentation_state()
        self._llm_sentence_session_id = f"llm-{time.time_ns()}"
        self._llm_sentence_counter = 0
        # One id for this whole run. Silence sleep and stream rollover each open
        # a fresh relay stream underneath; without this the server counts them as
        # separate short sessions.
        self.relay_run_id = uuid.uuid4().hex
        self._refine_context_history.clear()
        self._llm_context_cycle_count = int(LLM_REFINE_CONTEXT_MIN_COUNT)
        self._reset_run_state()
        self._reset_osc_live_state()

        if MUTE_MIC_WHEN_VRCHAT_SELF_MUTED and not USE_TWITCH_AUDIO_STREAM and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(osc_manager.start_server(app_name="RealtimeSubtitle"), self.loop)
            except Exception as error:
                print(f"⚠️  Failed to start OSC listener for MuteSelf: {error}")

        if translation_target_lang is not None:
            self.set_translation_target_lang(translation_target_lang)
        osc_manager.clear_history()

        # 初始化日志文件（如果启用且还没有创建）
        if self.logger.enabled and self.logger.log_file is None:
            self.logger.init_log_file()

        self.thread = threading.Thread(
            target=self._run_session,
            args=(api_key, audio_format, translation, self.translation_target_lang, loop),
            daemon=True
        )
        self.thread.start()
        return True

    def stop(self):
        """停止当前会话"""
        self._relay_session_active = False
        self.last_disconnect_payload = None
        with self._stream_close_lock:
            if self.stop_event:
                self.stop_event.set()

        self._stop_audio_streamer()

        with self._stream_close_lock:
            if self.ws:
                try:
                    # The one close the user actually asked for: a short session
                    # tagged this way is a deliberate stop, not a failure.
                    self._close_ws(self.ws, "user_stop")
                except Exception as close_error:
                    print(f"⚠️  Error while closing {self.PROVIDER_NAME} connection: {close_error}")
                finally:
                    self.ws = None

        thread = self.thread

        if thread and thread.is_alive():
            thread.join(timeout=3.0)
            if thread.is_alive():
                print(f"⚠️  {self.PROVIDER_NAME} session thread did not terminate within timeout")

        # Only forget the thread we joined: a concurrent start() may already
        # have replaced it, and clearing that one would orphan a live stream.
        if thread and not thread.is_alive() and self.thread is thread:
            self.thread = None

        if self.thread is None:
            self.stop_event = None
            osc_manager.clear_history()
            self._clear_segmentation_state()
            self._reset_osc_live_state()
            with self._ipc_lock:
                self._ipc_ongoing_text = ""
                self._ipc_pending_final = ""

    def pause(self):
        """暂停识别"""
        if self.is_paused:
            print("Pause requested but session already paused")
            return False

        self.is_paused = True
        print("⏸️  Recognition paused (connection closing)")
        self.stop()
        return True

    def resume(self, api_key: Optional[str] = None, audio_format: Optional[str] = None,
               translation: Optional[str] = None, loop: Optional[asyncio.AbstractEventLoop] = None,
               translation_target_lang: Optional[str] = None):
        """恢复识别"""
        if not self.is_paused:
            print("Resume requested but session is not paused")
            return False

        if api_key:
            self.api_key = api_key
        if audio_format:
            self.audio_format = audio_format
        if translation:
            self.translation = translation
        if loop:
            self.loop = loop

        if translation_target_lang is not None:
            ok, message = self.set_translation_target_lang(translation_target_lang)
            if not ok:
                print(f"⚠️  {message}")

        if not all([self.api_key, self.audio_format, self.translation, self.loop]):
            print("❌ Cannot resume: missing session configuration")
            return False

        started = self.start(
            self.api_key,
            self.audio_format,
            self.translation,
            self.loop,
            translation_target_lang=self.translation_target_lang,
        )
        if started:
            print("▶️  Recognition resumed (new connection)")
        return started

    def update_ipc_message(self, text: str, ongoing: bool) -> None:
        safe_text = (text or "").strip()
        if not safe_text:
            return
        with self._ipc_lock:
            if ongoing:
                self._ipc_ongoing_text = safe_text
            else:
                self._ipc_pending_final = safe_text
                self._ipc_ongoing_text = ""

    def get_translation_target_lang(self) -> str:
        return str(self.translation_target_lang or "en")

    # ------------------------------------------------------------------
    # Provider hooks
    # ------------------------------------------------------------------

    def _close_ws(self, ws, reason: str) -> None:
        """Close a provider WebSocket with a short reason tag."""
        raise NotImplementedError

    def _request_stream_finalize(self, ws) -> None:
        """Ask the provider to finalize pending tokens (before rollover)."""
        raise NotImplementedError

    def _warmup_response_ends_stream(self, stream: StreamState, res) -> bool:
        """Whether a response read while draining a warmup stream ends it."""
        raise NotImplementedError

    def _two_way_pair(self) -> tuple[str, str]:
        """The configured two-way language pair (un-normalized)."""
        raise NotImplementedError

    def _live_osc_preview_suppressed(self) -> bool:
        """Whether there is no live translation to preview over OSC."""
        return False

    def _on_llm_refine_mode_changed(self, mode: str) -> None:
        """React to a new LLM refine mode (after it is stored)."""

    # ------------------------------------------------------------------
    # Audio capture and devices
    # ------------------------------------------------------------------

    def get_audio_source(self) -> str:
        """返回当前配置的音频源"""
        with self.audio_lock:
            return self.audio_source

    def set_audio_source(self, source: str) -> Tuple[bool, str]:
        """切换音频源。

        返回 (是否成功, 描述信息)
        """
        if USE_TWITCH_AUDIO_STREAM:
            return False, "Twitch streaming mode is enabled; audio source switching is disabled."

        if source not in ("system", "microphone", "mix"):
            return False, "Invalid audio source (expected 'system', 'microphone' or 'mix')."

        with self.audio_lock:
            previous_source = self.audio_source
            self.audio_source = source
            streamer = self.audio_streamer

        if streamer:
            try:
                changed = streamer.set_source(source)
                if changed:
                    print(f"🎚️  Audio source switched from '{previous_source}' to '{source}'")
                if changed:
                    return True, f"Audio source switched to '{source}'."
                return True, f"Audio source already set to '{source}'."
            except ValueError as error:
                return False, str(error)

        if source != previous_source:
            print(f"🎚️  Audio source set to '{source}' (will apply on next session)")

        return True, f"Audio source saved as '{source}'. The change will apply when a session is active."

    def get_microphone_device_id(self) -> str:
        with self.audio_lock:
            if self.audio_streamer and hasattr(self.audio_streamer, "get_microphone_device_id"):
                self.microphone_device_id = self.audio_streamer.get_microphone_device_id()
            return str(self.microphone_device_id or "")

    def set_microphone_device_id(self, device_id: str) -> Tuple[bool, str]:
        """Set the microphone device used by microphone and mixed capture."""
        if USE_TWITCH_AUDIO_STREAM:
            return False, "Twitch streaming mode is enabled; microphone device switching is disabled."

        next_id = str(device_id or "").strip()
        with self.audio_lock:
            previous_id = self.microphone_device_id
            self.microphone_device_id = next_id
            streamer = self.audio_streamer

        if streamer and hasattr(streamer, "set_microphone_device_id"):
            changed = bool(streamer.set_microphone_device_id(next_id))
            if changed:
                print("🎙️  Microphone device switched")
                return True, "Microphone device switched."
            return True, "Microphone device already selected."

        if next_id != previous_id:
            print("🎙️  Microphone device saved (will apply on next session)")
        return True, "Microphone device saved. The change will apply when a session is active."

    def get_output_device_id(self) -> str:
        with self.audio_lock:
            if self.audio_streamer and hasattr(self.audio_streamer, "get_output_device_id"):
                self.output_device_id = self.audio_streamer.get_output_device_id()
            return str(self.output_device_id or "")

    def set_output_device_id(self, device_id: str) -> Tuple[bool, str]:
        if USE_TWITCH_AUDIO_STREAM:
            return False, "Twitch streaming mode is enabled; output device switching is disabled."
        next_id = str(device_id or "").strip()
        with self.audio_lock:
            self.output_device_id = next_id
            streamer = self.audio_streamer
        changed = bool(streamer.set_output_device_id(next_id)) if streamer and hasattr(streamer, "set_output_device_id") else False
        return True, "Output device switched." if changed else "Output device saved."

    def _start_audio_streamer(self, ws) -> None:
        with self.audio_lock:
            existing_streamer = self.audio_streamer
            self.audio_streamer = None

        if existing_streamer:
            existing_streamer.stop()

        if USE_TWITCH_AUDIO_STREAM:
            from twitch_audio_streamer import TwitchAudioStreamer

            streamer = TwitchAudioStreamer(
                ws,
                channel=TWITCH_CHANNEL,
                quality=TWITCH_STREAM_QUALITY,
                ffmpeg_path=FFMPEG_PATH,
                sample_rate=self.sample_rate,
                chunk_size=self.chunk_size,
            )
        else:
            streamer = AudioStreamer(
                ws,
                initial_source=self.get_audio_source(),
                sample_rate=self.sample_rate,
                chunk_size=self.chunk_size,
                mute_mic_when_vrchat_muted=bool(MUTE_MIC_WHEN_VRCHAT_SELF_MUTED),
                microphone_device_id=self.get_microphone_device_id(),
                output_device_id=self.get_output_device_id(),
            )
            streamer.set_vrchat_mic_muted(self._vrchat_self_muted)

        with self.audio_lock:
            self.audio_streamer = streamer

        streamer.start()

    def _stop_audio_streamer(self) -> None:
        with self.audio_lock:
            streamer = self.audio_streamer
            self.audio_streamer = None

        if streamer:
            streamer.stop()

    def _handle_vrchat_mute_self(self, mute_value) -> None:
        muted = bool(mute_value)
        self._vrchat_self_muted = muted

        with self.audio_lock:
            streamer = self.audio_streamer

        if streamer and hasattr(streamer, "set_vrchat_mic_muted"):
            try:
                streamer.set_vrchat_mic_muted(muted)
            except Exception as error:
                print(f"⚠️  Failed to update microphone mute state from OSC: {error}")

    # ------------------------------------------------------------------
    # Tokens, languages and sentence helpers
    # ------------------------------------------------------------------

    def _is_internal_token(self, token_or_text) -> bool:
        if isinstance(token_or_text, dict):
            text = token_or_text.get("text")
        else:
            text = token_or_text
        return text in INTERNAL_TOKEN_TEXTS

    @staticmethod
    def _token_speaker(token: dict) -> str | None:
        """The token's speaker label, or None when the provider sent none."""
        speaker = token.get("speaker")
        return str(speaker) if (speaker is not None and speaker != "") else None

    def _last_token_speaker(self, tokens: list[dict], *, translation_only: bool = False) -> str | None:
        """Speaker of the last token (optionally: last translation token) that has one."""
        for token in reversed(tokens):
            if translation_only and token.get("translation_status") != "translation":
                continue
            speaker = self._token_speaker(token)
            if speaker:
                return speaker
        return None

    def _any_buffered_speaker(self) -> str | None:
        """Fallback speaker: the first one with an open display sentence."""
        return next(iter(self._sentence_buffers.keys()), None)

    def _make_separator_token(self, separator_type: str) -> dict:
        return {
            "is_separator": True,
            "is_final": True,
            "separator_type": separator_type,
        }

    def _minify_token(self, token: dict, *, is_final: Optional[bool] = None) -> dict:
        """将 token 精简为前端需要的字段以减少带宽。"""
        if token.get("is_separator"):
            return {
                "is_separator": True,
                "is_final": True,
                "separator_type": token.get("separator_type", "translation"),
            }

        value: dict = {
            "text": token.get("text", ""),
            "speaker": token.get("speaker", "?"),
            "translation_status": token.get("translation_status", "original"),
            "language": token.get("language"),
            "source_language": token.get("source_language"),
        }
        llm_sentence_id = token.get("llm_sentence_id")
        if llm_sentence_id:
            value["llm_sentence_id"] = str(llm_sentence_id)
        if is_final is None:
            value["is_final"] = bool(token.get("is_final", False))
        else:
            value["is_final"] = bool(is_final)
        return value

    def _join_token_texts(self, tokens: list[dict]) -> str:
        return "".join(
            str(t.get("text", ""))
            for t in (tokens or [])
            if t.get("text") is not None and not self._is_internal_token(t)
        ).strip()

    def _infer_source_language(self, tokens: list[dict]) -> str:
        for token in reversed(tokens or []):
            source_language = normalize_language_code(token.get("source_language") or "")
            if source_language:
                return source_language
            language = normalize_language_code(token.get("language") or "")
            if language:
                return language
        return ""

    def _two_way_partner_lang(self, lang: str) -> str:
        """Return the other language in the two-way pair, or '' if unknown."""
        code = normalize_language_code(lang or "")
        pair = self._two_way_pair()
        l1 = normalize_language_code(pair[0])
        l2 = normalize_language_code(pair[1])
        if not (l1 and l2):
            return ""
        if code == l1:
            return l2
        if code == l2:
            return l1
        return ""

    def _osc_translation_language_for_comparison(self, source_tokens: list[dict]) -> str:
        """
        Language code used for smart OSC / finalize: "recognition language == this ⇒ use source
        when there is no translation text".

        - one_way / none / etc.: fixed session translation target (TRANSLATION_TARGET_LANG).
        - two_way: per-utterance target is the partner of the detected source within
          TARGET_LANG_1/TARGET_LANG_2; if source is outside the pair, fall back to session target.
        """
        mode = (self.translation or "").strip().lower()
        t_sess = normalize_language_code(self.get_translation_target_lang())

        if mode != "two_way":
            return t_sess

        pair = self._two_way_pair()
        l1 = normalize_language_code(pair[0])
        l2 = normalize_language_code(pair[1])
        fallback = t_sess if t_sess in (l1, l2) else (l2 or l1 or t_sess)

        source_lang = self._infer_source_language(source_tokens)
        if not (l1 and l2) or not source_lang:
            return fallback
        partner = self._two_way_partner_lang(source_lang)
        return partner or fallback

    def _expects_translation_stream(self, source_tokens: list[dict]) -> bool:
        """Whether the provider will stream a translation for this source text.

        When it will not, the source itself drives line breaks (and, for
        Soniox, semantic pairing): same-language speech has no translation."""
        return not self._can_use_source_as_translation(source_tokens)

    def _can_use_source_as_translation(self, source_tokens: list[dict]) -> bool:
        mode = (self.translation or "").strip().lower()
        # In two-way translation mode, speech is translated bidirectionally between the two languages.
        # Source text must never be treated as translation output.
        if mode == "two_way":
            return False

        source_lang = self._infer_source_language(source_tokens)
        if not source_lang:
            return False

        t_sess = normalize_language_code(self.get_translation_target_lang())
        return bool(t_sess and source_lang == t_sess)

    def _is_sentence_ending_punctuation(self, text: str) -> bool:
        """检测是否为句末标点"""
        return sentence_segmentation.is_sentence_ending_punctuation(text)

    def _is_sentence_ender_at(self, value: str, index: int) -> bool:
        return sentence_segmentation.is_sentence_ender_at(value, index)

    def _split_into_sentence_lines(self, text: str) -> list[str]:
        """Split in-progress text into per-sentence lines for the OSC preview,
        e.g. "甲。乙丙！丁" -> ["甲。", "乙丙！", "丁"]. A run of ending punctuation
        stays attached to its sentence; the trailing partial (if any) is the last
        line."""
        return sentence_segmentation.split_into_sentence_lines(text)

    def _sentence_context_text_for_token(self, token: dict) -> str:
        speaker = str(token.get("speaker", "?"))
        buffer = self._sentence_buffers.get(speaker) or {}
        token_text = str(token.get("text") or "")
        if token.get("translation_status") == "translation":
            return f"{self._join_token_texts(buffer.get('translation_tokens') or [])}{token_text}"
        return f"{self._join_token_texts(buffer.get('original_tokens') or [])}{token_text}"

    def _token_text_is_sentence_ending(
        self,
        tokens: list[dict],
        index: int,
        *,
        source_as_output: bool = False,
    ) -> bool:
        token = tokens[index]
        return self._pending_boundaries.is_token_sentence_ending(
            tokens,
            index,
            context_text=self._sentence_context_text_for_token(token),
            is_internal_token=self._is_internal_token,
            source_as_output=source_as_output,
        )

    def _clear_pending_boundaries_for_speaker(self, speaker: str) -> None:
        self._pending_boundaries.clear_speaker(speaker)

    # ------------------------------------------------------------------
    # OSC output and IPC
    # ------------------------------------------------------------------

    def get_osc_translation_enabled(self) -> bool:
        return self.osc_translation_enabled

    def set_osc_translation_enabled(self, enabled: bool):
        """开启或关闭翻译结果通过 OSC 发送"""
        value = bool(enabled)
        self.osc_translation_enabled = value
        if not value:
            osc_manager.clear_history()
            self._reset_osc_live_state()
        
        if ipc_server and hasattr(ipc_server, "broadcast_osc_state"):
            try:
                asyncio.create_task(ipc_server.broadcast_osc_state(value))
            except Exception as e:
                logger.warning("[IPC] Failed to broadcast OSC state: %s", e)

    def _select_osc_text(self, translation_text: str, source_text: str, source_tokens: list[dict]) -> str:
        mode = str(self._osc_send_text_mode or "smart").strip().lower()
        translation_value = normalize_east_asian_translation_spacing((translation_text or "").strip())
        source_value = (source_text or "").strip()

        if (self.translation or "").strip().lower() == "two_way":
            # In two-way translation mode, OSC must always send the translated text.
            return translation_value

        if mode == "translation_only":
            return translation_value
        if mode == "source_only":
            return source_value

        if translation_value:
            return translation_value
        if source_value and self._can_use_source_as_translation(source_tokens):
            return source_value
        return ""

    def _reset_osc_live_state(self, speaker: Optional[str] = None) -> None:
        if speaker is None:
            self._osc_live_last_text_by_speaker.clear()
            return
        self._osc_live_last_text_by_speaker.pop(str(speaker), None)

    def _maybe_send_live_osc_translation(self, non_final_tokens: list[dict]) -> None:
        """在非 Pure LLM 模式下，实时发送最新翻译（ongoing=True）。"""
        if not self.get_osc_translation_enabled():
            return
        if self._live_osc_preview_suppressed():
            return

        non_final_translation_by_speaker: dict[str, list[str]] = {}
        non_final_original_by_speaker: dict[str, list[dict]] = {}
        for token in non_final_tokens or []:
            text = token.get("text")
            if text is None or self._is_internal_token(text):
                continue
            speaker = str(token.get("speaker", "?"))
            if token.get("translation_status") == "translation":
                non_final_translation_by_speaker.setdefault(speaker, []).append(str(text))
            else:
                non_final_original_by_speaker.setdefault(speaker, []).append(token)

        active_speakers = (
            set(self._sentence_buffers.keys())
            | set(non_final_translation_by_speaker.keys())
            | set(non_final_original_by_speaker.keys())
        )

        for speaker in active_speakers:
            buffer = self._sentence_buffers.get(speaker) or {}
            final_translation_tokens = buffer.get("translation_tokens") or []
            final_original_tokens = buffer.get("original_tokens") or []

            final_translation_text = self._join_token_texts(final_translation_tokens)
            non_final_translation_text = "".join(non_final_translation_by_speaker.get(speaker, []))
            translation_text = f"{final_translation_text}{non_final_translation_text}".strip()

            source_tokens = list(final_original_tokens) + list(non_final_original_by_speaker.get(speaker, []))
            source_text = self._join_token_texts(source_tokens)
            current_text = self._select_osc_text(translation_text, source_text, source_tokens)
            if not current_text:
                continue

            # Speculatively split the in-progress text into per-sentence preview
            # lines so a completed sentence shows on its own line as soon as the
            # period appears. These stay in the unconfirmed preview layer (not
            # recorded to history) and keep updating as the non-final text is
            # revised; only finalize commits a sentence to history.
            preview_lines = self._split_into_sentence_lines(current_text)
            dedup_key = "\n".join(preview_lines)
            if self._osc_live_last_text_by_speaker.get(speaker) == dedup_key:
                continue

            self._osc_live_last_text_by_speaker[speaker] = dedup_key

            try:
                if self.OSC_PREVIEW_BY_SPEAKER:
                    osc_manager.send_preview_messages_with_history(preview_lines, ongoing=True, speaker=speaker)
                else:
                    osc_manager.send_preview_messages_with_history(preview_lines, ongoing=True)
            except Exception as error:
                print(f"OSC live send failed: {error}")

        for speaker in list(self._osc_live_last_text_by_speaker.keys()):
            if speaker not in active_speakers:
                self._osc_live_last_text_by_speaker.pop(speaker, None)

    def _broadcast_foreign_speech(self, source: str, original_tokens: list[dict]) -> None:
        """Fire-and-forget the finalized source sentence to IPC clients."""
        server = ipc_server
        if not (server and hasattr(server, "broadcast_foreign_speech")):
            return
        try:
            detected_lang = self._infer_source_language(original_tokens)
            asyncio.create_task(server.broadcast_foreign_speech(source, detected_lang))
        except Exception as e:
            print(f"⚠️ [IPC] Failed to fire-and-forget broadcast: {e}")

    # ------------------------------------------------------------------
    # LLM refine / translate plumbing
    # ------------------------------------------------------------------

    def get_llm_refine_mode(self) -> str:
        return self._llm_refine_mode

    def set_llm_refine_mode(self, mode: str) -> tuple[bool, str]:
        value = (mode or "").strip().lower()
        if value not in LLM_REFINE_MODES:
            return False, f"Invalid LLM refine mode: {mode}"

        self._llm_refine_mode = value
        self._reset_osc_live_state()
        self._on_llm_refine_mode_changed(value)
        return True, "ok"

    def get_llm_refine_enabled(self) -> bool:
        return self._llm_refine_mode != "off"

    def set_llm_refine_enabled(self, enabled: bool):
        mode = "refine" if bool(enabled) else "off"
        self.set_llm_refine_mode(mode)

    def _context_bounds(self) -> tuple[int, int]:
        """Effective (min, max) context item counts (server-delivered in relay mode)."""
        return config.llm_context_bounds()

    def _get_dynamic_context_items(self) -> list[dict]:
        """Return context items with cyclical window: min -> ... -> max -> min."""
        history = list(self._refine_context_history)
        if not history:
            return []

        bound_min, bound_max = self._context_bounds()
        min_count = max(0, int(bound_min))
        max_count = max(min_count, int(bound_max))
        if max_count == 0:
            return []

        if len(history) < min_count:
            return history

        current = int(self._llm_context_cycle_count)
        current = max(min_count, min(current, max_count))

        use_count = min(current, len(history))
        items = [] if use_count == 0 else history[-use_count:]

        if current >= max_count:
            self._llm_context_cycle_count = min_count
        else:
            self._llm_context_cycle_count = current + 1

        return items

    async def _llm_chat(self, system_prompt: str, user_prompt: str, *, temperature: float, max_tokens: int) -> str:
        """One chat completion — via the relay in hosted mode, or a local key in
        own-key mode. Raises HostedLlmError / LlmError on failure."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if config.llm_is_hosted():
            self._hosted_llm.configure(self.loop, self._hosted_llm_send)
            return await self._hosted_llm.chat(
                messages,
                temperature=float(temperature),
                max_tokens=int(config.llm_max_output_tokens()),
                timeout_seconds=config.llm_timeout_seconds(),
            )
        llm_config = LlmConfig(
            base_url=(LLM_BASE_URL or "").strip(),
            api_key=get_llm_api_key(),
            model=(LLM_MODEL or "").strip(),
            extra_headers=LLM_REQUEST_HEADERS or None,
            extra_json=LLM_REQUEST_JSON or None,
        )
        return await chat_completion(
            llm_config,
            messages,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            timeout_seconds=config.llm_timeout_seconds(),
        )

    async def _perform_refine(self, source: str, translation: str, context_items: list, target_lang: str | None = None) -> dict:
        """执行 LLM 翻译改进（共享逻辑见 llm_refine.perform_refine）。"""
        return await llm_refine.perform_refine(
            self._llm_chat,
            source,
            translation,
            context_items,
            target_lang=target_lang if (isinstance(target_lang, str) and target_lang.strip())
            else self.get_translation_target_lang(),
        )

    async def _perform_translate(self, source: str, context_items: list, target_lang: str | None = None) -> dict:
        """执行 LLM 直接翻译。`target_lang` overrides the session target — used for
        two-way, where each utterance translates into its partner language."""
        return await llm_refine.perform_translate(
            self._llm_chat,
            source,
            context_items,
            target_lang=target_lang if (isinstance(target_lang, str) and target_lang.strip())
            else self.get_translation_target_lang(),
        )

    @staticmethod
    def _target_lang_kwargs(fn, target_lang: str) -> dict:
        """{"target_lang": ...} when `fn` accepts it (tests swap in doubles
        with the older signature), else {}."""
        try:
            sig = inspect.signature(fn)
        except Exception:
            return {"target_lang": target_lang}
        if "target_lang" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            return {"target_lang": target_lang}
        return {}

    def _remember_refine_context(self, item: dict) -> None:
        """Append a finished sentence to the LLM context history, trimmed to
        the current maximum context size."""
        self._refine_context_history.append(item)
        max_history = max(0, int(self._context_bounds()[1]))
        if max_history == 0:
            self._refine_context_history.clear()
        elif len(self._refine_context_history) > max_history:
            self._refine_context_history = self._refine_context_history[-max_history:]

    def _hosted_llm_send(self, text: str) -> None:
        """Send a text frame on the current active relay stream (LLM multiplex)."""
        ws = self.ws
        if ws is not None:
            ws.send(text)

    def _on_hosted_llm_credits(self, credits: float) -> None:
        """Relay reported credits for a hosted LLM request; forward to the UI so
        it can fold the cost into the running session total."""
        if not self.loop:
            return
        try:
            self.loop.create_task(self.broadcast_callback({"type": "llm_cost", "credits": float(credits)}))
        except Exception:
            pass

    def _on_hosted_llm_disabled(self, reason: str) -> None:
        """Prepaid exhausted: drop to fast mode and tell the UI to lock there.
        `needs_restart` is set when the provider's built-in translation must be
        re-enabled."""
        self._llm_refine_mode = "off"
        needs_restart = self._suppress_soniox_translation
        self._suppress_soniox_translation = False
        if not self.loop:
            return
        try:
            self.loop.create_task(self.broadcast_callback({
                "type": "translation_mode_fallback",
                "reason": str(reason or ""),
                "needs_restart": bool(needs_restart),
            }))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Stream rollover / silence sleep
    # ------------------------------------------------------------------

    def _provider_config(self, suffix: str):
        """Read a provider config flag dynamically (hot-switch can change it)."""
        import config as live_config
        return getattr(live_config, f"{self.CONFIG_PREFIX}_{suffix}")

    def _sleep_idle_seconds(self) -> float | None:
        # Read dynamically: the active key (and thus whether silence-sleep is
        # enabled) can change at runtime via provider/key hot-switch.
        if not self._provider_config("SLEEP_ON_SILENCE"):
            return None
        try:
            value = float(SLEEP_IDLE_SECONDS)
        except Exception:
            return None
        if value <= 0:
            return None
        return value

    def _stream_is_near_rollover_limit(self, started_at: float | None, rollover_seconds: float | None) -> bool:
        if started_at is None or rollover_seconds is None:
            return False
        return (time.monotonic() - started_at) >= (rollover_seconds * STREAM_ROLLOVER_NEAR_LIMIT_RATIO)

    def _stream_rollover_prepare_age(self, rollover_seconds: float) -> float:
        return max(0.0, self._stream_rollover_force_age(rollover_seconds) - self._stream_rollover_switch_patience(rollover_seconds))

    def _stream_rollover_switch_patience(self, rollover_seconds: float) -> float:
        return max(0.0, min(STREAM_ROLLOVER_SWITCH_PATIENCE_SECONDS, rollover_seconds * 0.5))

    def _stream_rollover_force_age(self, rollover_seconds: float) -> float:
        guard_seconds = min(
            STREAM_ROLLOVER_FORCE_GUARD_SECONDS,
            max(0.5, rollover_seconds * 0.1),
        )
        return max(0.0, rollover_seconds - guard_seconds)

    def _should_prepare_rollover_stream(
        self,
        started_at: float | None,
        rollover_seconds: float | None,
    ) -> bool:
        if started_at is None or rollover_seconds is None:
            return False
        return (time.monotonic() - started_at) >= self._stream_rollover_prepare_age(rollover_seconds)

    def _should_force_rollover_switch(
        self,
        started_at: float | None,
        rollover_seconds: float | None,
    ) -> bool:
        if started_at is None or rollover_seconds is None:
            return False
        return (time.monotonic() - started_at) >= self._stream_rollover_force_age(rollover_seconds)

    def _make_rollover_silence_sender(self, ws) -> RealtimeSilenceSender:
        bytes_per_chunk = int(self.chunk_size) * 2
        chunk_interval_seconds = int(self.chunk_size) / max(1, int(self.sample_rate))
        return RealtimeSilenceSender(
            ws,
            bytes_per_chunk=bytes_per_chunk,
            chunk_interval_seconds=chunk_interval_seconds,
            session_stop_event=self.stop_event,
            thread_name=f"{self.PROVIDER_NAME}RolloverSilence",
        )

    def _close_stream_state(
        self,
        stream: StreamState | None,
        reason: str = "stream_close",
    ) -> None:
        """Close a stream, telling the relay why it ended.

        `reason` rides the WebSocket close frame, which is the only channel the
        server can use to tell a deliberate stop from a dropped connection. Keep
        it a short tag — the server stores `^[a-z0-9][a-z0-9_-]{0,39}$` and
        discards anything else.
        """
        if stream is None:
            return
        if stream.silence_sender is not None:
            stream.silence_sender.stop()
            stream.silence_sender = None
        try:
            self._close_ws(stream.ws, reason)
        except Exception as close_error:
            print(f"⚠️  Error closing {self.PROVIDER_NAME} stream #{stream.index}: {close_error}")

    def _drain_warmup_stream(self, stream: StreamState) -> bool:
        """Read and discard silence warmup responses. Returns False if stream ended."""
        for _ in range(STREAM_ROLLOVER_WARMUP_DRAIN_LIMIT):
            try:
                message = stream.ws.recv(timeout=0.001)
            except TimeoutError:
                return True
            except ConnectionClosedOK:
                return False
            except ConnectionClosed as error:
                print(f"⚠️  {self.PROVIDER_NAME} warmup stream #{stream.index} closed: {error}")
                return False
            except Exception as error:
                print(f"⚠️  Error reading {self.PROVIDER_NAME} warmup stream #{stream.index}: {error}")
                return False

            try:
                res = json.loads(message)
            except Exception as error:
                print(f"⚠️  Failed to parse {self.PROVIDER_NAME} warmup response: {error}")
                continue

            if self._warmup_response_ends_stream(stream, res):
                return False

        return True

    def _prepare_warmup_stream(
        self,
        current_api_key: str,
        stream_index: int,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
    ) -> _StreamState:
        """Run in background thread: fetch key + connect WebSocket + send config.

        All blocking operations (HTTP request for temp key, WebSocket connect,
        send config) happen here so the main session loop stays responsive.
        Returns a _StreamState with a pre-created silence_sender (not started).
        """
        next_api_key = self._fetch_api_key_for_next_stream(current_api_key)
        ws_state = self._open_stream_state(
            next_api_key, stream_index, audio_format, translation,
            translation_target_lang, warming=True,
        )
        ws_state.silence_sender = self._make_rollover_silence_sender(ws_state.ws)
        return ws_state

    def _fetch_api_key_for_next_stream(self, current_api_key: str) -> str:
        """Refresh temp keys between stream rollovers while preserving permanent keys."""
        import config as live_config

        if live_config.RELAY_MODE:
            return current_api_key
        if not self._provider_config("USES_TEMP_API_KEY"):
            return current_api_key
        if not self._provider_config("TEMP_KEY_URL"):
            return current_api_key

        try:
            get_api_key = importlib.import_module(self.CLIENT_MODULE).get_api_key
            next_key = get_api_key()
        except Exception as error:
            print(f"⚠️  Failed to refresh {self.PROVIDER_NAME} API key for stream rollover: {error}")
            return current_api_key

        next_key = (next_key or "").strip()
        if next_key:
            self.api_key = next_key
            return next_key
        return current_api_key

    def _open_and_switch_to_replacement_stream(
        self,
        audio_router: AudioSendRouter,
        old_stream: StreamState,
        current_api_key: str,
        stream_index: int,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
        loop: asyncio.AbstractEventLoop,
        reason: str,
    ) -> tuple[StreamState, str, int] | None:
        next_stream_index = stream_index + 1
        replacement_stream: StreamState | None = None
        try:
            next_api_key = self._fetch_api_key_for_next_stream(current_api_key)
            replacement_stream = self._open_stream_state(
                next_api_key,
                next_stream_index,
                audio_format,
                translation,
                translation_target_lang,
            )
            self._broadcast_preserve_existing_subtitles(loop)
            print(
                f"🔁 Switching {self.PROVIDER_NAME} audio from stream #{old_stream.index} "
                f"to stream #{replacement_stream.index} at {reason}."
            )
            if not audio_router.switch_target(
                replacement_stream.ws,
                expected_current=old_stream.ws,
            ):
                self._close_stream_state(replacement_stream)
                return None

            old_stream.sent_count = self._finalize_stream_before_rollover(
                old_stream.ws,
                old_stream.all_final_tokens,
                old_stream.sent_count,
                loop,
            )
            self._close_stream_state(old_stream, "rollover")
            return replacement_stream, next_api_key, next_stream_index
        except Exception as error:
            if replacement_stream is not None:
                self._close_stream_state(replacement_stream)
            print(f"⚠️  Failed to switch {self.PROVIDER_NAME} stream at {reason}: {error}")
            return None

    def _finalize_stream_before_rollover(
        self,
        ws,
        all_final_tokens: list[dict],
        sent_count: int,
        loop: asyncio.AbstractEventLoop,
    ) -> int:
        """Ask the provider to finalize pending tokens before switching streams."""
        try:
            self._request_stream_finalize(ws)
        except Exception as error:
            print(f"⚠️  Failed to request {self.PROVIDER_NAME} finalization before stream rollover: {error}")
            return sent_count

        deadline = time.monotonic() + STREAM_ROLLOVER_FINALIZE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = ws.recv(timeout=remaining)
            except TimeoutError:
                break
            except ConnectionClosedOK:
                break
            except Exception as error:
                print(f"⚠️  Error while waiting for {self.PROVIDER_NAME} rollover finalization: {error}")
                break

            try:
                res = json.loads(message)
            except Exception as error:
                print(f"⚠️  Failed to parse {self.PROVIDER_NAME} finalization response: {error}")
                continue

            if isinstance(res, dict) and self._hosted_llm.handle_frame(res):
                continue

            sent_count, should_end, _reason = self._process_stream_response(
                res,
                all_final_tokens,
                sent_count,
                loop,
            )
            if should_end:
                break

        return sent_count

    def _finalize_and_close_stream(self, old_stream: _StreamState, loop: asyncio.AbstractEventLoop) -> None:
        """Finalize and close an old stream after rollover, in a background thread.

        This avoids blocking the main loop on old-stream finalization so that
        the new stream's responses are processed without delay.
        """
        try:
            old_stream.sent_count = self._finalize_stream_before_rollover(
                old_stream.ws, old_stream.all_final_tokens, old_stream.sent_count, loop,
            )
        except Exception as error:
            print(f"⚠️  Error finalizing old stream #{old_stream.index}: {error}")
        self._close_stream_state(old_stream, "rollover")

    def _broadcast_preserve_existing_subtitles(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({
                    "type": "clear",
                    "message": "Recognition reconnecting...",
                    "preserve_existing": True,
                }),
                loop,
            )
        except Exception as error:
            print(f"⚠️  Failed to notify clients about stream rollover: {error}")

    def _notify_relay_session(self, loop: asyncio.AbstractEventLoop, event: str) -> None:
        """Tell clients when the billed relay link goes live/idle.

        The frontend "this session" cost meter only counts while we are
        actually connected to the relay (and thus being billed). Emitted only
        in hosted/relay mode; direct mode ignores these events.
        """
        import config as live_config

        if not getattr(live_config, "RELAY_MODE", False):
            return
        self._relay_session_active = (event == "session_connected")
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({"type": event}),
                loop,
            )
        except Exception as error:
            print(f"⚠️  Failed to notify clients about relay session {event}: {error}")
