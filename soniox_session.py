"""
Soniox会话模块 - 管理与Soniox服务的WebSocket会话
"""
import json
import threading
import asyncio
import time
import re
import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from websockets import ConnectionClosed, ConnectionClosedOK
from websockets.sync.client import connect as sync_connect

import config
from config import (
    SONIOX_STREAM_DURATION_SECONDS,
    SLEEP_IDLE_SECONDS,
    SLEEP_PRE_ROLL_SECONDS,
    SLEEP_SPEECH_GRACE_SECONDS,
    SLEEP_SPEECH_WINDOW_SECONDS,
    SLEEP_VAD_THRESHOLD,
    USE_TWITCH_AUDIO_STREAM,
    MUTE_MIC_WHEN_VRCHAT_SELF_MUTED,
    TWITCH_CHANNEL,
    TWITCH_STREAM_QUALITY,
    FFMPEG_PATH,
    DEFAULT_SEGMENT_MODE,
    is_llm_refine_available,
    LLM_REFINE_CONTEXT_MIN_COUNT,
    LLM_REFINE_CONTEXT_MAX_COUNT,
    LLM_PROMPT_SUFFIX,
    LLM_REFINE_MAX_TOKENS,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_REQUEST_HEADERS,
    LLM_REQUEST_JSON,
    get_llm_api_key,
    normalize_language_code,
    OSC_SEND_TEXT_MODE,
    LLM_REFINE_DEFAULT_ENABLED,
    LLM_REFINE_DEFAULT_MODE,
)
ipc_server = None
from audio_router import AudioSendRouter
from relay_errors import relay_close_info
from soniox_client import get_config
from audio_capture import AudioStreamer
from osc_manager import osc_manager
from llm_client import LlmConfig, chat_completion, extract_answer_tag, LlmError

logger = logging.getLogger(__name__)


LLM_REFINE_MODES = ("off", "refine", "translate")


def _is_api_key_error_reason(reason: str) -> bool:
    """Heuristically detect API-key/auth failures from a disconnect reason string."""
    text = str(reason or "").lower()
    if not text:
        return False
    needles = (
        "api key", "api_key", "apikey", "unauthorized", "authentication",
        "invalid key", "invalid api", "permission", "forbidden",
        "401", "403",
    )
    return any(needle in text for needle in needles)
EAST_ASIAN_TIGHT_SPACING_CLASS = (
    r"\u3000-\u303F"
    r"\u3040-\u30FF"
    r"\u31F0-\u31FF"
    r"\u3400-\u4DBF"
    r"\u4E00-\u9FFF"
    r"\uF900-\uFAFF"
    r"\uFF01-\uFF60"
    r"\uFF66-\uFF9D"
    r"\uFFE0-\uFFEE"
)
EAST_ASIAN_TIGHT_SPACING_RE = re.compile(
    rf"([{EAST_ASIAN_TIGHT_SPACING_CLASS}])\s+([{EAST_ASIAN_TIGHT_SPACING_CLASS}])"
)
STREAM_ROLLOVER_RECV_TIMEOUT_SECONDS = 0.25
STREAM_ROLLOVER_FINALIZE_TIMEOUT_SECONDS = 1.5
STREAM_ROLLOVER_AUDIO_BUFFER_CHUNKS = 200
STREAM_ROLLOVER_NEAR_LIMIT_RATIO = 0.8
STREAM_ROLLOVER_SWITCH_PATIENCE_SECONDS = 25.0
STREAM_ROLLOVER_FORCE_GUARD_SECONDS = 2.0
STREAM_ROLLOVER_SILENCE_HOLD_SECONDS = 0.7
STREAM_ROLLOVER_WARMUP_DRAIN_LIMIT = 8
SONIOX_INTERNAL_TOKEN_TEXTS = {"<end>", "<fin>"}


class _RealtimeSilenceSender:
    """Send realtime-paced PCM silence to a warming Soniox stream."""

    def __init__(
        self,
        ws,
        *,
        bytes_per_chunk: int,
        chunk_interval_seconds: float,
        session_stop_event: threading.Event | None,
    ):
        self.ws = ws
        self.payload = b"\0" * max(2, int(bytes_per_chunk))
        self.chunk_interval_seconds = max(0.01, float(chunk_interval_seconds))
        self.session_stop_event = session_stop_event
        self.error: Exception | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="SonioxRolloverSilence",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        next_send_at = time.monotonic()
        while not self._stop_event.is_set():
            if self.session_stop_event and self.session_stop_event.is_set():
                break

            try:
                self.ws.send(self.payload)
            except Exception as error:
                self.error = error
                break

            next_send_at += self.chunk_interval_seconds
            delay = next_send_at - time.monotonic()
            if delay < 0:
                next_send_at = time.monotonic()
                delay = self.chunk_interval_seconds
            self._stop_event.wait(delay)


@dataclass
class _SonioxStreamState:
    ws: Any
    index: int
    api_key: str
    started_at: float
    all_final_tokens: list[dict]
    sent_count: int = 0
    ready_at: float | None = None
    silence_sender: _RealtimeSilenceSender | None = None
    silence_started_at: float = 0.0  # monotonic timestamp when silence sender started


def normalize_east_asian_translation_spacing(text: str) -> str:
    value = "" if text is None else str(text)
    if not value:
        return ""
    return EAST_ASIAN_TIGHT_SPACING_RE.sub(r"\1\2", value)


class SonioxSession:
    """Soniox会话管理器"""
    
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
        self._relay_session_active = False
        self.last_disconnect_payload: Optional[dict] = None
        self.sample_rate = 16000
        self.chunk_size = 3840
        self.audio_source = "twitch" if USE_TWITCH_AUDIO_STREAM else "system"
        self.audio_streamer: Optional[object] = None
        self.audio_lock = threading.Lock()
        self._vrchat_self_muted = False
        self.osc_translation_enabled = False
        self._segment_mode = DEFAULT_SEGMENT_MODE if DEFAULT_SEGMENT_MODE in ("translation", "endpoint", "punctuation") else "punctuation"
        self._sentence_buffers: dict[str, dict] = {}
        # Speakers whose <end> fired before the sentence's translation was ready.
        # The line break is deferred until the translation arrives (or the next
        # sentence starts) so the late translation stays attached to this
        # sentence instead of merging into the next one. See the interleaved
        # punctuation pass in _process_soniox_response.
        self._pending_endpoint_speakers: set[str] = set()
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

        try:
            from config import TRANSLATION_TARGET_LANG
            self.translation_target_lang = str(TRANSLATION_TARGET_LANG)
        except Exception:
            self.translation_target_lang = "en"

        # Two-way translation language pair (instance-level so it can be changed
        # at runtime from the UI without restarting the process).
        self.target_lang_1: str = normalize_language_code(config.TARGET_LANG_1) or "en"
        self.target_lang_2: str = normalize_language_code(config.TARGET_LANG_2) or "zh"

        if MUTE_MIC_WHEN_VRCHAT_SELF_MUTED:
            osc_manager.set_mute_callback(self._handle_vrchat_mute_self)

        self._ipc_lock = threading.Lock()
        self._ipc_ongoing_text = ""
        self._ipc_pending_final = ""

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

    def start(
        self,
        api_key: Optional[str],
        audio_format: str,
        translation: str,
        loop: asyncio.AbstractEventLoop,
        translation_target_lang: Optional[str] = None,
    ):
        """启动新的Soniox会话"""
        if self.thread and self.thread.is_alive():
            print("⚠️  Soniox session already running, start request ignored")
            return False

        if not api_key:
            print("❌ Cannot start Soniox session: API key is missing.")
            self.api_key = None # Clear any previous invalid key
            return False

        self.last_sent_count = 0
        self.is_paused = False
        self.last_disconnect_payload = None
        self.api_key = api_key
        self.audio_format = audio_format
        self.translation = translation
        self.loop = loop
        self._sentence_buffers.clear()
        self._pending_endpoint_speakers.clear()
        self._llm_sentence_session_id = f"llm-{time.time_ns()}"
        self._llm_sentence_counter = 0
        self._refine_context_history.clear()
        self._llm_context_cycle_count = int(LLM_REFINE_CONTEXT_MIN_COUNT)
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

    def get_translation_target_lang(self) -> str:
        return str(self.translation_target_lang or "en")

    def set_translation_target_lang(self, lang: str) -> tuple[bool, str]:
        from config import normalize_language_code, is_supported_language_code

        normalized = normalize_language_code(lang)
        if not is_supported_language_code(normalized):
            return False, f"Unsupported translation target language: {lang}"

        previous = self.translation_target_lang
        self.translation_target_lang = normalized
        if previous != normalized:
            print(f"🌐 Translation target language updated: {previous} -> {normalized}")
        return True, "ok"

    def get_target_langs(self) -> tuple[str, str]:
        return (str(self.target_lang_1 or "en"), str(self.target_lang_2 or "zh"))

    def set_target_langs(self, lang_a: str, lang_b: str) -> tuple[bool, str]:
        """Set the two-way translation language pair (validated)."""
        from config import normalize_language_code, is_supported_language_code

        a = normalize_language_code(lang_a)
        b = normalize_language_code(lang_b)
        if not is_supported_language_code(a):
            return False, f"Unsupported language: {lang_a}"
        if not is_supported_language_code(b):
            return False, f"Unsupported language: {lang_b}"
        if a == b:
            return False, "Two-way translation requires two different languages"
        self.target_lang_1 = a
        self.target_lang_2 = b
        return True, "ok"

    def pause(self):
        """暂停识别"""
        if self.is_paused:
            print("Pause requested but session already paused")
            return False

        self.is_paused = True
        print("⏸️  Recognition paused (connection closing)")
        self.stop()
        return True

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

    def get_osc_translation_enabled(self) -> bool:
        return self.osc_translation_enabled
    
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
    
    def stop(self):
        """停止当前会话"""
        self._relay_session_active = False
        self.last_disconnect_payload = None
        if self.stop_event:
            self.stop_event.set()

        self._stop_audio_streamer()

        if self.ws:
            try:
                self.ws.close()
            except Exception as close_error:
                print(f"⚠️  Error while closing Soniox connection: {close_error}")
            finally:
                self.ws = None

        thread = self.thread

        if thread and thread.is_alive():
            thread.join(timeout=3.0)
            if thread.is_alive():
                print("⚠️  Soniox session thread did not terminate within timeout")

        if thread and not thread.is_alive():
            self.thread = None

        if self.thread is None:
            self.stop_event = None
            osc_manager.clear_history()
            self._sentence_buffers.clear()
            self._pending_endpoint_speakers.clear()
            self._reset_osc_live_state()
            with self._ipc_lock:
                self._ipc_ongoing_text = ""
                self._ipc_pending_final = ""

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
            )
            streamer.set_vrchat_mic_muted(self._vrchat_self_muted)

        with self.audio_lock:
            self.audio_streamer = streamer

        streamer.start()

    def _handle_vrchat_mute_self(self, mute_value) -> None:
        muted = bool(mute_value)
        previous = self._vrchat_self_muted
        self._vrchat_self_muted = muted

        with self.audio_lock:
            streamer = self.audio_streamer

        if streamer and hasattr(streamer, "set_vrchat_mic_muted"):
            try:
                streamer.set_vrchat_mic_muted(muted)
            except Exception as error:
                print(f"⚠️  Failed to update microphone mute state from OSC: {error}")

        if previous != muted:
            state_text = "muted" if muted else "unmuted"
            # print(f"🔇 VRChat MuteSelf changed: microphone is now {state_text} in capture pipeline")

    def _stop_audio_streamer(self) -> None:
        with self.audio_lock:
            streamer = self.audio_streamer
            self.audio_streamer = None

        if streamer:
            streamer.stop()

    def _make_separator_token(self, separator_type: str) -> dict:
        return {
            "is_separator": True,
            "is_final": True,
            "separator_type": separator_type,
        }

    def _is_internal_soniox_token(self, token_or_text) -> bool:
        if isinstance(token_or_text, dict):
            text = token_or_text.get("text")
        else:
            text = token_or_text
        return text in SONIOX_INTERNAL_TOKEN_TEXTS

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

    def _process_token_for_sentence(self, token: dict) -> None:
        """缓存 token 供后续断句/改进使用。"""
        text = token.get("text", "")
        if self._is_internal_soniox_token(text):
            return

        speaker = str(token.get("speaker", "?"))
        translation_status = token.get("translation_status", "original")

        if speaker not in self._sentence_buffers:
            self._sentence_buffers[speaker] = {
                "original_tokens": [],
                "translation_tokens": [],
            }

        buffer = self._sentence_buffers[speaker]

        if translation_status == "translation":
            buffer["translation_tokens"].append(token)
        else:
            buffer["original_tokens"].append(token)

    def _join_token_texts(self, tokens: list[dict]) -> str:
        return "".join(
            str(t.get("text", ""))
            for t in (tokens or [])
            if t.get("text") is not None and not self._is_internal_soniox_token(t)
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
        l1 = normalize_language_code(self.target_lang_1)
        l2 = normalize_language_code(self.target_lang_2)
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

        source_lang = self._infer_source_language(source_tokens)
        l1 = normalize_language_code(self.target_lang_1)
        l2 = normalize_language_code(self.target_lang_2)
        if not (l1 and l2) or not source_lang:
            return t_sess
        if source_lang not in (l1, l2):
            return t_sess
        return self._two_way_partner_lang(source_lang)

    def _can_use_source_as_translation(self, source_tokens: list[dict]) -> bool:
        source_lang = self._infer_source_language(source_tokens)
        if not source_lang:
            return False

        mode = (self.translation or "").strip().lower()
        t_sess = normalize_language_code(self.get_translation_target_lang())
        if mode != "two_way":
            return bool(t_sess and source_lang == t_sess)

        l1 = normalize_language_code(self.target_lang_1)
        l2 = normalize_language_code(self.target_lang_2)
        if not (l1 and l2):
            return bool(t_sess and source_lang == t_sess)

        cmp_lang = self._osc_translation_language_for_comparison(source_tokens)
        if cmp_lang and source_lang == cmp_lang:
            return True

        # Recognizer + session both say we are in the UI language, but translation tokens are
        # empty: treat like one-way "already target language". For two-way, suppress this when
        # TARGET_LANG_1 matches both speech and session — that is the "speaking L1 while waiting
        # for partner(L1)" case (e.g. en + T=en + pair en/zh) which must not pass English through.
        # Default TARGET_LANG_1=en / TARGET_LANG_2=zh keeps zh + T=zh passing here; if you swap
        # TARGET_LANG_1/2, put the code you mostly wait for translation *from* in TARGET_LANG_1.
        if t_sess and source_lang == t_sess:
            if l1 and source_lang == l1 and t_sess == l1:
                return False
            return True

        return False

    def _select_osc_text(self, translation_text: str, source_text: str, source_tokens: list[dict]) -> str:
        mode = str(self._osc_send_text_mode or "smart").strip().lower()
        translation_value = normalize_east_asian_translation_spacing((translation_text or "").strip())
        source_value = (source_text or "").strip()

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
        if self.get_llm_refine_mode() == "translate":
            return

        non_final_translation_by_speaker: dict[str, list[str]] = {}
        non_final_original_by_speaker: dict[str, list[dict]] = {}
        for token in non_final_tokens or []:
            text = token.get("text")
            if text is None or self._is_internal_soniox_token(text):
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
                osc_manager.send_preview_messages_with_history(preview_lines, ongoing=True, speaker=speaker)
            except Exception as error:
                print(f"OSC live send failed: {error}")

        for speaker in list(self._osc_live_last_text_by_speaker.keys()):
            if speaker not in active_speakers:
                self._osc_live_last_text_by_speaker.pop(speaker, None)

    def _is_sentence_ending_punctuation(self, text: str) -> bool:
        """检测是否为句末标点"""
        value = (text or "").strip()
        if not value:
            return False
        ending_chars = ("。", "！", "？", ".", "!", "?", "︒", "︕", "︖", "…")
        return value.endswith(ending_chars)

    def _split_into_sentence_lines(self, text: str) -> list[str]:
        """Split in-progress text into per-sentence lines for the OSC preview,
        e.g. "甲。乙丙！丁" -> ["甲。", "乙丙！", "丁"]. A run of ending punctuation
        stays attached to its sentence; the trailing partial (if any) is the last
        line."""
        value = text or ""
        if not value:
            return []
        ender_set = {"。", "！", "？", ".", "!", "?", "︒", "︕", "︖", "…"}
        lines: list[str] = []
        start = 0
        length = len(value)
        for i, ch in enumerate(value):
            if ch in ender_set and (i + 1 >= length or value[i + 1] not in ender_set):
                lines.append(value[start:i + 1])
                start = i + 1
        if start < length:
            lines.append(value[start:])
        return [segment for segment in (line.strip() for line in lines) if segment]

    def _trigger_sentence_finalization(self, speaker: str) -> bool:
        """触发句子完成处理"""
        buffer = self._sentence_buffers.get(speaker)
        if not buffer:
            return False

        original_tokens = buffer.get("original_tokens") or []
        translation_tokens = buffer.get("translation_tokens") or []

        if not original_tokens:
            return False

        if not translation_tokens and not self._can_use_source_as_translation(original_tokens):
            return False

        self._sentence_buffers.pop(speaker, None)
        self._pending_endpoint_speakers.discard(speaker)
        self._reset_osc_live_state(speaker)

        self._llm_sentence_counter += 1
        sentence_id = f"{self._llm_sentence_session_id}-{self._llm_sentence_counter}"
        for token in original_tokens + translation_tokens:
            if isinstance(token, dict):
                token["llm_sentence_id"] = sentence_id

        if not self.loop:
            return False

        asyncio.run_coroutine_threadsafe(
            self._finalize_sentence_async(speaker, original_tokens, translation_tokens, sentence_id),
            self.loop,
        )
        return True

    async def _finalize_sentence_async(self, speaker: str, original_tokens: list, translation_tokens: list, sentence_id: str | None = None):
        """异步执行 LLM 改进与 OSC 发送"""
        source = self._join_token_texts(original_tokens)
        translation = self._join_token_texts(translation_tokens)

        if not source:
            return

        if ipc_server and hasattr(ipc_server, "broadcast_foreign_speech"):
            try:
                detected_lang = self._infer_source_language(original_tokens)
                asyncio.create_task(ipc_server.broadcast_foreign_speech(source, detected_lang))
            except Exception as e:
                print(f"⚠️ [IPC] Failed to fire-and-forget broadcast: {e}")

        fallback_to_source = not translation and self._can_use_source_as_translation(original_tokens)
        display_translation = translation if translation else (source if fallback_to_source else "")
        if not display_translation:
            return

        context_items = self._get_dynamic_context_items()

        refined_translation = display_translation
        no_change = True

        mode = self.get_llm_refine_mode()
        if is_llm_refine_available() and mode != "off" and translation:
            try:
                if mode == "refine":
                    result = await self._perform_refine(source, translation, context_items)
                    if result.get("status") == "ok" and not result.get("no_change"):
                        refined_translation = result.get("refined_translation") or translation
                        no_change = False
                elif mode == "translate":
                    result = await self._perform_translate(source, context_items)
                    if result.get("status") == "ok":
                        translated = (result.get("translation") or "").strip()
                        if translated:
                            refined_translation = translated
                        else:
                            refined_translation = translation
                        no_change = False
            except Exception as error:
                if mode == "translate":
                    refined_translation = translation
                    no_change = False
                print(f"LLM refine error: {error}")

        await self.broadcast_callback({
            "type": "refine_result",
            "sentence_id": sentence_id,
            "source": source,
            "original_translation": display_translation,
            "refined_translation": refined_translation if not no_change else None,
            "no_change": no_change,
        })

        if self.get_osc_translation_enabled():
            try:
                osc_text = self._select_osc_text(refined_translation, source, original_tokens)
                if osc_text:
                    osc_manager.add_message_and_send(osc_text, ongoing=False, speaker=speaker)
            except Exception as error:
                print(f"OSC send failed: {error}")

        self._refine_context_history.append({"source": source, "translation": refined_translation})
        max_history = max(1, int(LLM_REFINE_CONTEXT_MAX_COUNT))
        if len(self._refine_context_history) > max_history:
            self._refine_context_history = self._refine_context_history[-max_history:]

    def _get_dynamic_context_items(self) -> list[dict]:
        """Return context items with cyclical window: min -> ... -> max -> min."""
        history = list(self._refine_context_history)
        if not history:
            return []

        min_count = max(1, int(LLM_REFINE_CONTEXT_MIN_COUNT))
        max_count = max(min_count, int(LLM_REFINE_CONTEXT_MAX_COUNT))

        if len(history) < min_count:
            return history

        current = int(self._llm_context_cycle_count)
        current = max(min_count, min(current, max_count))

        use_count = min(current, len(history))
        items = history[-use_count:]

        if current >= max_count:
            self._llm_context_cycle_count = min_count
        else:
            self._llm_context_cycle_count = current + 1

        return items

    async def _perform_refine(self, source: str, translation: str, context_items: list) -> dict:
        """执行 LLM 翻译改进"""
        NO_CHANGE_MARKER = "__NO_CHANGE__"
        PLACEHOLDER_ANSWER = "...corrected translation..."
        DEFAULT_SEVERITY = "low"
        MAX_REFINE_ATTEMPTS = 3

        source = (source or "").strip()
        translation = (translation or "").strip()
        if not source or not translation:
            return {"status": "error", "no_change": True}

        target_lang_value = ""
        try:
            tl = self.get_translation_target_lang()
            if isinstance(tl, str) and tl.strip():
                target_lang_value = tl.strip().lower()[:16]
        except Exception:
            target_lang_value = ""

        normalized_context: list[dict[str, str]] = []
        if isinstance(context_items, list) and LLM_REFINE_CONTEXT_MAX_COUNT > 0:
            max_items = max(1, int(LLM_REFINE_CONTEXT_MAX_COUNT))
            for item in context_items[-max_items:]:
                if not isinstance(item, dict):
                    continue
                ctx_source = item.get("source")
                ctx_translation = item.get("translation")
                if not isinstance(ctx_source, str) or not isinstance(ctx_translation, str):
                    continue
                ctx_source = ctx_source.strip()
                ctx_translation = ctx_translation.strip()
                if not ctx_source or not ctx_translation:
                    continue
                if len(ctx_source) > 5000 or len(ctx_translation) > 5000:
                    continue
                normalized_context.append({"source": ctx_source, "translation": ctx_translation})

        context_block = ""
        if normalized_context:
            lines = [
                "Context (for coherence only; do NOT quote it; do NOT merge or rewrite it into the current translation; "
                "even if the source/translation is short, do NOT output the context; use it only to resolve pronouns, references, and coherence):",
            ]
            for idx, item in enumerate(normalized_context, start=1):
                lines.append(f"{idx}. Source: {item['source']}")
                lines.append(f"   Translation: {item['translation']}")
            context_block = "\n".join(lines) + "\n\n"

        prompt_suffix = (LLM_PROMPT_SUFFIX or "").strip()
        suffix_block = f"\n{prompt_suffix}" if prompt_suffix else ""

        prompt = (
            f"Target language (ISO 639-1): {target_lang_value or 'unknown'}\n\n"
            "Role: Strict Translation QA. Strategy: Surgical corrections for accuracy violations only.\n\n"
            "## 1. IGNORE (Allow 'Ugly' but Accurate)\n"
            "Output <answer>{NO_CHANGE_MARKER}</answer> if the meaning is correct, even if:\n"
            " - Grammar is broken/pidgin but understandable (e.g., 'Me want buy').\n"
            " - Style/Tone is unnatural.\n"
            " - Input has extra spaces (streaming artifacts) or loose punctuation.\n\n"
            "## 2. MUST FIX (Accuracy Violations)\n"
            "You MUST correct the translation if:\n"
            " - MISTRANSLATION: Factual errors, wrong numbers/names, or opposite meaning.\n"
            " - HALLUCINATION: Information added that is NOT in the source.\n"
            " - LOGIC REVERSAL: Subject/Object flipped (e.g., 'Dog bites man' vs 'Man bites dog').\n"
            " - CONFUSING SYNTAX: Word order is so wrong that it causes misunderstanding.\n\n"
            "## 3. EDITING RULE: MINIMAL EDITS\n"
            "If you fix, apply SURGICAL edits only. Change the minimum number of words necessary to restore accuracy. DO NOT rewrite the whole sentence to improve flow.\n\n"
            "## 4. When in doubt, PREFER NO CHANGE.\n\n"
            "## Output Format\n"
            f" - NO CHANGE: <answer>{NO_CHANGE_MARKER}</answer>\n"
            f" - Correction: <answer>{PLACEHOLDER_ANSWER}</answer>\n"
            "   - Severity (only when Correction): <severity>low|medium|high|critical</severity>\n\n"
            "Do NOT explain. Do NOT add preamble.\n\n"
            f"{context_block}"
            "Source:\n```\n"
            f"{source}\n"
            "```\n\n"
            "Draft translation:\n```\n"
            f"{translation}\n"
            "```\n"
            f"{suffix_block}"
        )

        config = LlmConfig(
            base_url=(LLM_BASE_URL or "").strip(),
            api_key=get_llm_api_key(),
            model=(LLM_MODEL or "").strip(),
            extra_headers=LLM_REQUEST_HEADERS or None,
            extra_json=LLM_REQUEST_JSON or None,
        )

        for attempt in range(MAX_REFINE_ATTEMPTS):
            try:
                content = await chat_completion(
                    config,
                    messages=[
                        {"role": "system", "content": "You are a precise translation reviewer."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=float(LLM_TEMPERATURE),
                    max_tokens=int(LLM_REFINE_MAX_TOKENS),
                    timeout_seconds=60.0,
                )
            except (asyncio.CancelledError, Exception) as exc:
                if isinstance(exc, LlmError):
                    return {"status": "error", "message": str(exc), "no_change": True}
                return {"status": "error", "message": "LLM request failed", "no_change": True}

            raw_content = str(content or "").strip()
            refined = extract_answer_tag(raw_content).strip()
            severity = DEFAULT_SEVERITY
            severity_match = re.findall(r"<severity>(.*?)</severity>", raw_content or "", flags=re.IGNORECASE | re.DOTALL)
            if severity_match:
                severity = str(severity_match[-1]).strip().lower()
            if severity not in ("low", "medium", "high", "critical"):
                severity = DEFAULT_SEVERITY

            # print(f"severity={severity}, draft='{translation}', refined='{refined}'")

            if not refined:
                if attempt < MAX_REFINE_ATTEMPTS - 1:
                    continue
                return {"status": "ok", "no_change": True}

            if refined.startswith("```"):
                refined = re.sub(r"^```[^\n]*\n", "", refined)
                refined = re.sub(r"\n```$", "", refined.strip())
            refined = refined.strip("`").strip()

            if refined == PLACEHOLDER_ANSWER:
                if attempt < MAX_REFINE_ATTEMPTS - 1:
                    continue
                return {"status": "ok", "no_change": True}

            if refined == NO_CHANGE_MARKER:
                return {"status": "ok", "no_change": True}

            if severity not in ("high", "critical"):
                return {"status": "ok", "no_change": True}

            return {"status": "ok", "no_change": False, "refined_translation": refined}

        return {"status": "ok", "no_change": True}

    async def _perform_translate(self, source: str, context_items: list) -> dict:
        """执行 LLM 直接翻译"""
        PLACEHOLDER_ANSWER = "...translated text..."
        MAX_TRANSLATE_ATTEMPTS = 3

        source = (source or "").strip()
        if not source:
            return {"status": "error", "message": "empty source"}

        target_lang_value = ""
        try:
            tl = self.get_translation_target_lang()
            if isinstance(tl, str) and tl.strip():
                target_lang_value = tl.strip().lower()[:16]
        except Exception:
            target_lang_value = ""

        normalized_context: list[dict[str, str]] = []
        if isinstance(context_items, list) and LLM_REFINE_CONTEXT_MAX_COUNT > 0:
            max_items = max(1, int(LLM_REFINE_CONTEXT_MAX_COUNT))
            for item in context_items[-max_items:]:
                if not isinstance(item, dict):
                    continue
                ctx_source = item.get("source")
                ctx_translation = item.get("translation")
                if not isinstance(ctx_source, str) or not isinstance(ctx_translation, str):
                    continue
                ctx_source = ctx_source.strip()
                ctx_translation = ctx_translation.strip()
                if not ctx_source or not ctx_translation:
                    continue
                if len(ctx_source) > 5000 or len(ctx_translation) > 5000:
                    continue
                normalized_context.append({"source": ctx_source, "translation": ctx_translation})

        context_block = ""
        if normalized_context:
            lines = [
                "Context (for coherence only; do NOT quote it; do NOT merge or rewrite it into the current translation; "
                "even if the source is short, do NOT output the context; use it only to resolve pronouns, references, and coherence):",
            ]
            for idx, item in enumerate(normalized_context, start=1):
                lines.append(f"{idx}. Source: {item['source']}")
                lines.append(f"   Translation: {item['translation']}")
            context_block = "\n".join(lines) + "\n\n"

        prompt_suffix = (LLM_PROMPT_SUFFIX or "").strip()
        suffix_block = f"\n{prompt_suffix}" if prompt_suffix else ""

        prompt = (
            f"Target language (ISO 639-1): {target_lang_value or 'unknown'}\n\n"
            "You are a professional real-time translator. Translate the source text into the target language.\n"
            "\n"
            "Rules:\n"
            "1. Output ONLY the translation; no explanations or extra text.\n"
            "2. Preserve the original meaning, named entities, numbers, and tone.\n"
            "3. If the source is a question, keep it a question in the translation (preserve question intent and punctuation such as '?' where appropriate).\n"
            "4. Do NOT add or omit information.\n\n"
            "Output ONLY the translation wrapped exactly as:\n"
            f"<answer>{PLACEHOLDER_ANSWER}</answer>\n\n"
            f"{context_block}"
            "Source:\n```\n"
            f"{source}\n"
            "```\n"
            f"{suffix_block}"
        )

        config = LlmConfig(
            base_url=(LLM_BASE_URL or "").strip(),
            api_key=get_llm_api_key(),
            model=(LLM_MODEL or "").strip(),
            extra_headers=LLM_REQUEST_HEADERS or None,
            extra_json=LLM_REQUEST_JSON or None,
        )

        for attempt in range(MAX_TRANSLATE_ATTEMPTS):
            try:
                content = await chat_completion(
                    config,
                    messages=[
                        {"role": "system", "content": "You are a precise real-time translator."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=float(LLM_TEMPERATURE),
                    max_tokens=int(LLM_REFINE_MAX_TOKENS),
                    timeout_seconds=60.0,
                )
            except (asyncio.CancelledError, Exception) as exc:
                if isinstance(exc, LlmError):
                    return {"status": "error", "message": str(exc)}
                return {"status": "error", "message": "LLM request failed"}

            raw_content = str(content or "").strip()
            translated = extract_answer_tag(raw_content).strip()

            if not translated:
                if attempt < MAX_TRANSLATE_ATTEMPTS - 1:
                    continue
                return {"status": "error", "message": "empty translation"}

            if translated.startswith("```"):
                translated = re.sub(r"^```[^\n]*\n", "", translated)
                translated = re.sub(r"\n```$", "", translated.strip())
            translated = translated.strip("`").strip()

            if translated == PLACEHOLDER_ANSWER:
                if attempt < MAX_TRANSLATE_ATTEMPTS - 1:
                    continue
                return {"status": "error", "message": "placeholder translation"}

            return {"status": "ok", "translation": translated}

        return {"status": "error", "message": "translation failed"}

    def set_segment_mode(self, mode: str) -> tuple[bool, str]:
        """设置断句模式并广播给所有前端"""
        if mode not in ("translation", "endpoint", "punctuation"):
            return False, f"Invalid segment mode: {mode}"

        if mode == "translation" and self.get_llm_refine_mode() == "translate":
            return False, "Segment mode 'translation' is disabled when LLM translate mode is enabled"

        self._segment_mode = mode
        self._sentence_buffers.clear()
        self._pending_endpoint_speakers.clear()
        self._reset_osc_live_state()
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({
                    "type": "segment_mode_changed",
                    "mode": mode,
                }),
                self.loop,
            )
        return True, "ok"

    def get_segment_mode(self) -> str:
        return self._segment_mode

    def set_llm_refine_mode(self, mode: str) -> tuple[bool, str]:
        value = (mode or "").strip().lower()
        if value not in LLM_REFINE_MODES:
            return False, f"Invalid LLM refine mode: {mode}"

        self._llm_refine_mode = value
        self._reset_osc_live_state()

        if value == "translate" and self._segment_mode == "translation":
            # Force punctuation segmentation when LLM translate mode is enabled.
            self.set_segment_mode("punctuation")

        return True, "ok"

    def get_llm_refine_mode(self) -> str:
        return self._llm_refine_mode

    def set_llm_refine_enabled(self, enabled: bool):
        mode = "refine" if bool(enabled) else "off"
        self.set_llm_refine_mode(mode)

    def get_llm_refine_enabled(self) -> bool:
        return self._llm_refine_mode != "off"

    def _stream_rollover_seconds(self) -> float | None:
        if SONIOX_STREAM_DURATION_SECONDS is None:
            return None
        try:
            value = float(SONIOX_STREAM_DURATION_SECONDS)
        except Exception:
            return None
        if value <= 0:
            return None
        return value

    def _sleep_idle_seconds(self) -> float | None:
        # Read dynamically: the active key (and thus whether silence-sleep is
        # enabled) can change at runtime via provider/key hot-switch.
        if not config.SONIOX_SLEEP_ON_SILENCE:
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

    def _make_rollover_silence_sender(self, ws) -> _RealtimeSilenceSender:
        bytes_per_chunk = int(self.chunk_size) * 2
        chunk_interval_seconds = int(self.chunk_size) / max(1, int(self.sample_rate))
        return _RealtimeSilenceSender(
            ws,
            bytes_per_chunk=bytes_per_chunk,
            chunk_interval_seconds=chunk_interval_seconds,
            session_stop_event=self.stop_event,
        )

    def _open_soniox_stream_state(
        self,
        api_key: str,
        stream_index: int,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
        *,
        warming: bool = False,
    ) -> _SonioxStreamState:
        stream_config = get_config(
            api_key,
            audio_format,
            translation,
            translation_target_lang=translation_target_lang,
            target_lang_1=self.target_lang_1,
            target_lang_2=self.target_lang_2,
        )
        label = f"stream #{stream_index}"
        purpose = " warmup" if warming else ""
        # Read dynamically so a runtime region / relay switch takes effect on the
        # next stream.
        if config.RELAY_MODE:
            # Hosted mode: connect through the subtitle-server relay. The relay
            # injects its own upstream key, so the body key is blanked and the
            # account token is sent as the Authorization bearer.
            stream_config["api_key"] = ""
            relay_url = config.relay_ws_url("soniox")
            print(f"Connecting to relay Soniox ({label}{purpose}): {relay_url}")
            ws = sync_connect(
                relay_url,
                additional_headers={"Authorization": f"Bearer {config.RELAY_TOKEN}"},
            )
        else:
            print(f"Connecting to Soniox ({label}{purpose})...")
            ws = sync_connect(config.SONIOX_WEBSOCKET_URL)
        ws.send(json.dumps(stream_config))
        state = _SonioxStreamState(
            ws=ws,
            index=stream_index,
            api_key=api_key,
            started_at=time.monotonic(),
            ready_at=time.monotonic(),
            all_final_tokens=[],
        )
        print(f"Session started ({label}{purpose}).")
        return state

    def _close_soniox_stream_state(self, stream: _SonioxStreamState | None) -> None:
        if stream is None:
            return
        if stream.silence_sender is not None:
            stream.silence_sender.stop()
            stream.silence_sender = None
        try:
            stream.ws.close()
        except Exception as close_error:
            print(f"⚠️  Error closing Soniox stream #{stream.index}: {close_error}")

    def _drain_warmup_stream(self, stream: _SonioxStreamState) -> bool:
        """Read and discard silence warmup responses. Returns False if stream ended."""
        for _ in range(STREAM_ROLLOVER_WARMUP_DRAIN_LIMIT):
            try:
                message = stream.ws.recv(timeout=0.001)
            except TimeoutError:
                return True
            except ConnectionClosedOK:
                return False
            except ConnectionClosed as error:
                print(f"⚠️  Soniox warmup stream #{stream.index} closed: {error}")
                return False
            except Exception as error:
                print(f"⚠️  Error reading Soniox warmup stream #{stream.index}: {error}")
                return False

            try:
                res = json.loads(message)
            except Exception as error:
                print(f"⚠️  Failed to parse Soniox warmup response: {error}")
                continue

            if res.get("error_code") is not None:
                print(f"⚠️  Soniox warmup error {res.get('error_code')}: {res.get('error_message', '')}")
                return False
            if res.get("finished"):
                return False

        return True

    def _open_and_switch_to_replacement_stream(
        self,
        audio_router: AudioSendRouter,
        old_stream: _SonioxStreamState,
        current_api_key: str,
        stream_index: int,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
        loop: asyncio.AbstractEventLoop,
        reason: str,
    ) -> tuple[_SonioxStreamState, str, int] | None:
        next_stream_index = stream_index + 1
        replacement_stream: _SonioxStreamState | None = None
        try:
            next_api_key = self._fetch_api_key_for_next_stream(current_api_key)
            replacement_stream = self._open_soniox_stream_state(
                next_api_key,
                next_stream_index,
                audio_format,
                translation,
                translation_target_lang,
            )
            self._broadcast_preserve_existing_subtitles(loop)
            print(
                f"🔁 Switching Soniox audio from stream #{old_stream.index} "
                f"to stream #{replacement_stream.index} at {reason}."
            )
            if not audio_router.switch_target(
                replacement_stream.ws,
                expected_current=old_stream.ws,
            ):
                self._close_soniox_stream_state(replacement_stream)
                return None

            old_stream.sent_count = self._finalize_stream_before_rollover(
                old_stream.ws,
                old_stream.all_final_tokens,
                old_stream.sent_count,
                loop,
            )
            self._close_soniox_stream_state(old_stream)
            return replacement_stream, next_api_key, next_stream_index
        except Exception as error:
            if replacement_stream is not None:
                self._close_soniox_stream_state(replacement_stream)
            print(f"⚠️  Failed to switch Soniox stream at {reason}: {error}")
            return None

    def _fetch_api_key_for_next_stream(self, current_api_key: str) -> str:
        """Refresh temp keys between stream rollovers while preserving permanent keys."""
        if config.RELAY_MODE:
            return current_api_key
        if not config.SONIOX_USES_TEMP_API_KEY:
            return current_api_key
        if not config.SONIOX_TEMP_KEY_URL:
            return current_api_key

        try:
            from soniox_client import get_api_key

            next_key = get_api_key()
        except Exception as error:
            print(f"⚠️  Failed to refresh Soniox API key for stream rollover: {error}")
            return current_api_key

        next_key = (next_key or "").strip()
        if next_key:
            self.api_key = next_key
            return next_key
        return current_api_key

    def _prepare_warmup_stream(
        self,
        current_api_key: str,
        stream_index: int,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
    ) -> _SonioxStreamState:
        """Run in background thread: fetch key + connect WebSocket + send config.

        All blocking operations (HTTP request for temp key, WebSocket connect,
        send config) happen here so the main session loop stays responsive.
        Returns a _SonioxStreamState with a pre-created silence_sender (not started).
        """
        next_api_key = self._fetch_api_key_for_next_stream(current_api_key)
        ws_state = self._open_soniox_stream_state(
            next_api_key, stream_index, audio_format, translation,
            translation_target_lang, warming=True,
        )
        ws_state.silence_sender = self._make_rollover_silence_sender(ws_state.ws)
        return ws_state

    def _notify_relay_session(self, loop: asyncio.AbstractEventLoop, event: str) -> None:
        """Tell clients when the billed relay link goes live/idle.

        The frontend "this session" cost meter only counts while we are
        actually connected to the relay (and thus being billed). Emitted only
        in hosted/relay mode; direct mode ignores these events.
        """
        if not getattr(config, "RELAY_MODE", False):
            return
        self._relay_session_active = (event == "session_connected")
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({"type": event}),
                loop,
            )
        except Exception as error:
            print(f"⚠️  Failed to notify clients about relay session {event}: {error}")

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

    def _process_soniox_response(
        self,
        res: dict,
        all_final_tokens: list[dict],
        sent_count: int,
        loop: asyncio.AbstractEventLoop,
    ) -> tuple[int, bool, str | None]:
        """Process one Soniox response. Returns sent count, should end stream, reason."""
        if res.get("error_code") is not None:
            message = res.get("error_message", "")
            reason = f"server error {res['error_code']}: {message}"
            print(f"Error: {res['error_code']} - {message}")
            return sent_count, True, reason

        # Parse tokens from current response.
        non_final_tokens: list[dict] = []

        for token in res.get("tokens", []):
            text = token.get("text")
            if text:
                if token.get("is_final"):
                    # Final tokens累积添加
                    all_final_tokens.append(token)
                elif not self._is_internal_soniox_token(text):
                    # Non-final tokens每次重置
                    non_final_tokens.append(token)

        # 计算新增的final tokens（增量部分）
        new_final_tokens = all_final_tokens[sent_count:]
        endpoint_detected = bool(res.get("endpoint_detected", False))

        separator_tokens: list[dict] = []
        outgoing_final_tokens: list[dict] = []

        # In punctuation mode the line break must land at the position of the
        # ending punctuation, not at the end of the batch. Soniox sometimes
        # confirms the period together with the words that follow it in a single
        # batch (e.g. "…的。而你是法官"); appending one separator after the whole
        # batch would leave the next sentence stuck on the previous line. So we
        # walk the tokens in order, building the outgoing stream and finalizing /
        # inserting a separator exactly when we cross an ending punctuation.
        interleaved_punctuation = (self._segment_mode == "punctuation")

        if new_final_tokens and not interleaved_punctuation:
            for token in new_final_tokens:
                if token.get("text") is None:
                    continue
                self._process_token_for_sentence(token)

        if interleaved_punctuation and new_final_tokens:
            # When the speech is already in the target language there are no
            # translation tokens, so the source text is used as the output;
            # segment on punctuation in the original tokens too (gated by
            # _can_use_source_as_translation so we never cut before a real
            # translation arrives).
            source_as_output = self._can_use_source_as_translation(new_final_tokens)
            last_real_speaker = None
            for token in new_final_tokens:
                text = token.get("text")
                if text is None:
                    continue
                is_internal = self._is_internal_soniox_token(token)
                is_translation = token.get("translation_status") == "translation"

                # A pending endpoint means an earlier <end> fired before this
                # sentence's translation had streamed in, so the line break was
                # deferred (see the <end> branch below). The first *original*
                # token of the next sentence is the signal that the previous
                # sentence — together with whatever translation has since been
                # buffered — is complete: finalize it now and break the line
                # before this new token, so the late translation stays attached
                # to the previous sentence instead of merging into this one.
                if (not is_internal) and (not is_translation) and self._pending_endpoint_speakers:
                    spk = token.get("speaker")
                    pending_speaker = str(spk) if (spk is not None and spk != "") else last_real_speaker
                    if pending_speaker is None and len(self._pending_endpoint_speakers) == 1:
                        pending_speaker = next(iter(self._pending_endpoint_speakers))
                    if pending_speaker in self._pending_endpoint_speakers:
                        self._pending_endpoint_speakers.discard(pending_speaker)
                        if self._trigger_sentence_finalization(pending_speaker):
                            outgoing_final_tokens.append(
                                self._minify_token(self._make_separator_token("endpoint"))
                            )

                self._process_token_for_sentence(token)
                if not is_internal:
                    outgoing_final_tokens.append(self._minify_token(token, is_final=True))
                    spk = token.get("speaker")
                    if spk is not None and spk != "":
                        last_real_speaker = str(spk)
                is_boundary = (text == "<end>") or (
                    not is_internal
                    and (is_translation or source_as_output)
                    and self._is_sentence_ending_punctuation(text)
                )
                if not is_boundary:
                    continue
                spk = token.get("speaker")
                speaker_value = str(spk) if (spk is not None and spk != "") else last_real_speaker
                if speaker_value is None and self._sentence_buffers:
                    speaker_value = next(iter(self._sentence_buffers.keys()))
                if not speaker_value:
                    continue
                if self._trigger_sentence_finalization(speaker_value):
                    outgoing_final_tokens.append(
                        self._minify_token(self._make_separator_token("punctuation"))
                    )
                elif text == "<end>":
                    # Endpoint detected, but the translation for this sentence
                    # has not been finalized yet (Soniox streams translation
                    # tokens in after <end>). Defer the line break: mark the
                    # speaker pending so it is finalized once the translation
                    # arrives (via a later boundary) or, at the latest, when the
                    # next sentence's first original token is seen. This stops
                    # the next sentence's translation from running onto this one
                    # when the utterance was split by a pause rather than a
                    # sentence-ending period.
                    self._pending_endpoint_speakers.add(speaker_value)

        self._maybe_send_live_osc_translation(non_final_tokens)

        if (new_final_tokens or endpoint_detected) and not interleaved_punctuation:
            if self._segment_mode == "translation":
                translation_hit = any(
                    t.get("text") is not None
                    and not self._is_internal_soniox_token(t)
                    and t.get("translation_status") == "translation"
                    for t in new_final_tokens
                )
                # Same-language speech produces no translation tokens; fall back
                # to sentence-ending punctuation in the source (used as output)
                # so it still segments instead of growing without bound.
                if not translation_hit and self._can_use_source_as_translation(new_final_tokens):
                    translation_hit = any(
                        t.get("text")
                        and not self._is_internal_soniox_token(t)
                        and t.get("translation_status") != "translation"
                        and self._is_sentence_ending_punctuation(t.get("text", ""))
                        for t in new_final_tokens
                    )
                if translation_hit:
                    speaker_value = None
                    for token in reversed(new_final_tokens):
                        if token.get("translation_status") == "translation":
                            spk = token.get("speaker")
                            if spk is not None and spk != "":
                                speaker_value = str(spk)
                                break
                    if speaker_value is None:
                        for token in reversed(new_final_tokens):
                            spk = token.get("speaker")
                            if spk is not None and spk != "":
                                speaker_value = str(spk)
                                break
                    if speaker_value is None and self._sentence_buffers:
                        speaker_value = next(iter(self._sentence_buffers.keys()))
                    if speaker_value:
                        self._trigger_sentence_finalization(speaker_value)
                    separator_tokens.append(self._make_separator_token("translation"))

            elif self._segment_mode == "endpoint":
                endpoint_hit = endpoint_detected or any(
                    t.get("text") == "<end>"
                    for t in new_final_tokens
                )
                if endpoint_hit:
                    speaker_value = None
                    for token in reversed(new_final_tokens):
                        spk = token.get("speaker")
                        if spk is not None and spk != "":
                            speaker_value = str(spk)
                            break
                    if speaker_value is None and self._sentence_buffers:
                        speaker_value = next(iter(self._sentence_buffers.keys()))
                    if speaker_value:
                        self._trigger_sentence_finalization(speaker_value)
                    separator_tokens.append(self._make_separator_token("endpoint"))

            # punctuation mode is handled by the interleaved pass above so the
            # separator lands at the period's position within the batch.

        if new_final_tokens and not interleaved_punctuation:
            for token in new_final_tokens:
                if token.get("text") is None:
                    continue
                if not self._is_internal_soniox_token(token):
                    outgoing_final_tokens.append(self._minify_token(token, is_final=True))

        # 将新的final tokens写入日志
        loggable_tokens = [t for t in new_final_tokens if not self._is_internal_soniox_token(t)]
        if loggable_tokens and not self.is_paused:
            self.logger.write_to_log(loggable_tokens)

        # 混入 IPC 消息（作为 S0）
        ipc_final_tokens = []
        ipc_non_final_tokens = []
        with self._ipc_lock:
            if self._ipc_pending_final:
                ipc_final_tokens.append(self._minify_token({
                    "text": self._ipc_pending_final,
                    "speaker": "0",
                    "translation_status": "original",
                    "is_final": True,
                }, is_final=True))
                self._ipc_pending_final = ""
            if self._ipc_ongoing_text:
                ipc_non_final_tokens.append(self._minify_token({
                    "text": self._ipc_ongoing_text,
                    "speaker": "0",
                    "translation_status": "original",
                    "is_final": False,
                }, is_final=False))

        # 如果有新的数据，发送给前端（暂停时也显示，只是不记录）
        if outgoing_final_tokens or separator_tokens or non_final_tokens or ipc_final_tokens or ipc_non_final_tokens:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({
                    "type": "update",
                    "final_tokens": outgoing_final_tokens + [self._minify_token(t) for t in separator_tokens] + ipc_final_tokens,
                    "non_final_tokens": [self._minify_token(t, is_final=False) for t in non_final_tokens if not self._is_internal_soniox_token(t)] + ipc_non_final_tokens
                }),
                loop
            )

            # 更新已发送的计数
            sent_count = len(all_final_tokens)
            self.last_sent_count = sent_count

        if new_final_tokens:
            sent_count = len(all_final_tokens)
            self.last_sent_count = sent_count

        # Session finished.
        if res.get("finished"):
            print("Session finished.")
            return sent_count, True, "session finished"

        return sent_count, False, None

    def _finalize_stream_before_rollover(
        self,
        ws,
        all_final_tokens: list[dict],
        sent_count: int,
        loop: asyncio.AbstractEventLoop,
    ) -> int:
        """Ask Soniox to finalize pending tokens before switching streams."""
        try:
            ws.send(json.dumps({"type": "finalize"}))
        except Exception as error:
            print(f"⚠️  Failed to request Soniox finalization before stream rollover: {error}")
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
                print(f"⚠️  Error while waiting for Soniox rollover finalization: {error}")
                break

            try:
                res = json.loads(message)
            except Exception as error:
                print(f"⚠️  Failed to parse Soniox finalization response: {error}")
                continue

            sent_count, should_end, _reason = self._process_soniox_response(
                res,
                all_final_tokens,
                sent_count,
                loop,
            )
            if should_end:
                break

        return sent_count

    def _finalize_and_close_stream(self, old_stream: _SonioxStreamState, loop: asyncio.AbstractEventLoop) -> None:
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
        self._close_soniox_stream_state(old_stream)

    
    def _run_session(
        self,
        api_key: str,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
        loop: asyncio.AbstractEventLoop,
    ):
        """运行Soniox会话（内部方法）"""
        if not api_key:
            print("❌ _run_session called without API key. Exiting session thread.")
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({
                    "type": "error",
                    "code": "api_key",
                    "message": "Soniox API key is missing. Please configure it in Settings."
                }),
                loop
            )
            return

        rollover_seconds = self._stream_rollover_seconds()
        if rollover_seconds is not None:
            print(f"🔁 Soniox stream rollover enabled: {rollover_seconds:.1f}s per stream")
        sleep_idle_seconds = self._sleep_idle_seconds()
        if sleep_idle_seconds is not None:
            print(
                f"💤 Soniox silence sleep enabled: {sleep_idle_seconds:.1f}s idle, "
                f"{float(SLEEP_PRE_ROLL_SECONDS):.2f}s pre-roll, "
                f"{float(SLEEP_SPEECH_GRACE_SECONDS):.2f}s speech/"
                f"{float(SLEEP_SPEECH_WINDOW_SECONDS):.2f}s window"
            )

        self.stop_event = threading.Event()
        disconnect_reason = "connection ended"
        notify_disconnect = True
        relay_close = None  # (tag, terminal, message) when a relay code closes us
        current_api_key = api_key
        stream_index = 1
        audio_stream_started = False
        active_stream: _SonioxStreamState | None = None
        warmup_stream: _SonioxStreamState | None = None
        dormant_for_silence = False
        next_prepare_attempt_at = 0.0
        warmup_future: concurrent.futures.Future | None = None
        key_fetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="soniox-key")
        audio_router = AudioSendRouter(
            max_buffered_chunks=STREAM_ROLLOVER_AUDIO_BUFFER_CHUNKS,
            sample_rate=self.sample_rate,
            chunk_size=self.chunk_size,
            silence_hold_seconds=STREAM_ROLLOVER_SILENCE_HOLD_SECONDS,
            vad_speech_threshold=SLEEP_VAD_THRESHOLD,
            sleep_idle_seconds=sleep_idle_seconds,
            sleep_pre_roll_seconds=SLEEP_PRE_ROLL_SECONDS,
            sleep_speech_grace_seconds=SLEEP_SPEECH_GRACE_SECONDS,
            sleep_speech_window_seconds=SLEEP_SPEECH_WINDOW_SECONDS,
        )

        try:
            try:
                active_stream = self._open_soniox_stream_state(
                    current_api_key,
                    stream_index,
                    audio_format,
                    translation,
                    translation_target_lang,
                )
            except ConnectionClosed as error:
                info = relay_close_info(getattr(error, "code", None))
                if info is not None:
                    relay_close = info
                    disconnect_reason = f"relay: {info[0]}"
                else:
                    disconnect_reason = f"connection closed: {error}"
                return
            except Exception as error:
                info = relay_close_info(getattr(error, "code", None))
                if info is not None:
                    relay_close = info
                    disconnect_reason = f"relay: {info[0]}"
                else:
                    disconnect_reason = f"connection error: {error}"
                print(f"Error connecting to Soniox: {error}")
                return
            self.ws = active_stream.ws
            self.last_sent_count = 0
            self._notify_relay_session(loop, "session_connected")

            if not audio_router.set_target(active_stream.ws):
                disconnect_reason = "failed to attach audio to Soniox stream"
                return

            self._start_audio_streamer(audio_router)
            audio_stream_started = True

            while True:
                if self.stop_event and self.stop_event.is_set():
                    notify_disconnect = False
                    break

                if active_stream is None:
                    if dormant_for_silence:
                        if audio_router.wake_ready():
                            buffered_count = audio_router.buffered_count()
                            try:
                                next_api_key = self._fetch_api_key_for_next_stream(current_api_key)
                                resumed_stream = self._open_soniox_stream_state(
                                    next_api_key,
                                    stream_index + 1,
                                    audio_format,
                                    translation,
                                    translation_target_lang,
                                )
                                if not audio_router.set_target(resumed_stream.ws):
                                    self._close_soniox_stream_state(resumed_stream)
                                    disconnect_reason = "failed to attach audio after silence sleep"
                                    break
                                active_stream = resumed_stream
                                stream_index = active_stream.index
                                current_api_key = active_stream.api_key
                                self.ws = active_stream.ws
                                self.last_sent_count = active_stream.sent_count
                                dormant_for_silence = False
                                self._notify_relay_session(loop, "session_connected")
                                disconnect_reason = "silence sleep resumed"
                                print(
                                    f"▶️  Speech detected after silence; reopened Soniox stream "
                                    f"#{active_stream.index} and flushed {buffered_count} buffered chunks."
                                )
                            except Exception as error:
                                disconnect_reason = f"failed to reopen Soniox stream after silence: {error}"
                                print(f"⚠️  {disconnect_reason}")
                                break
                        else:
                            time.sleep(0.05)
                            continue
                    else:
                        disconnect_reason = "stream rollover failed"
                        break

                if (
                    sleep_idle_seconds is not None
                    and not dormant_for_silence
                    and active_stream is not None
                    and audio_router.sleep_ready()
                ):
                    if warmup_stream is not None:
                        if warmup_stream.silence_sender is not None:
                            warmup_stream.silence_sender.stop()
                            warmup_stream.silence_sender = None
                        self._close_soniox_stream_state(warmup_stream)
                        warmup_stream = None
                    if warmup_future is not None:
                        warmup_future.cancel()
                        warmup_future = None

                    print(
                        f"💤 No speech detected for "
                        f"{audio_router.sleep_confirmed_silence_seconds():.1f}s; closing Soniox stream."
                    )
                    sleeping_stream = active_stream
                    if not audio_router.enter_sleep_buffering(sleeping_stream.ws):
                        disconnect_reason = "failed to detach audio for silence sleep"
                        break
                    active_stream = None
                    self.ws = None
                    dormant_for_silence = True
                    self._notify_relay_session(loop, "session_idle")
                    sleeping_stream.sent_count = self._finalize_stream_before_rollover(
                        sleeping_stream.ws,
                        sleeping_stream.all_final_tokens,
                        sleeping_stream.sent_count,
                        loop,
                    )
                    self._close_soniox_stream_state(sleeping_stream)
                    continue

                if active_stream is None:
                    disconnect_reason = "stream rollover failed"
                    break

                if (
                    rollover_seconds is not None
                    and warmup_stream is None
                    and warmup_future is None
                    and time.monotonic() >= next_prepare_attempt_at
                    and self._should_prepare_rollover_stream(active_stream.started_at, rollover_seconds)
                ):
                    warmup_future = key_fetch_executor.submit(
                        self._prepare_warmup_stream,
                        current_api_key, stream_index, audio_format,
                        translation, translation_target_lang,
                    )
                    stream_index += 1
                    next_prepare_attempt_at = time.monotonic() + 1.0

                if warmup_future is not None:
                    if warmup_future.done():
                        try:
                            warmup_stream = warmup_future.result(timeout=0)
                            warmup_future = None
                            warmup_stream.silence_sender.start()
                            warmup_stream.silence_started_at = time.monotonic()
                            print(
                                f"🔁 Soniox stream #{warmup_stream.index} is warming with realtime silence; "
                                f"waiting for a quiet audio gap to switch."
                            )
                        except Exception as error:
                            print(f"⚠️  Failed to prepare next Soniox stream for rollover: {error}")
                            warmup_future = None
                            warmup_stream = None
                            stream_index -= 1
                            next_prepare_attempt_at = time.monotonic() + 1.0
                if (
                    rollover_seconds is not None
                    and warmup_stream is None
                    and self._should_force_rollover_switch(active_stream.started_at, rollover_seconds)
                ):
                    switched = self._open_and_switch_to_replacement_stream(
                        audio_router,
                        active_stream,
                        current_api_key,
                        stream_index,
                        audio_format,
                        translation,
                        translation_target_lang,
                        loop,
                        "rollover guard deadline without warmup",
                    )
                    if switched is None:
                        disconnect_reason = "failed to switch Soniox stream before configured duration"
                        break
                    active_stream, current_api_key, stream_index = switched
                    self.ws = active_stream.ws
                    self.last_sent_count = active_stream.sent_count
                    disconnect_reason = "stream rollover"
                    continue

                if warmup_stream is not None:
                    warmup_alive = True
                    silence_sender = warmup_stream.silence_sender
                    if silence_sender is not None and silence_sender.error is not None:
                        print(f"⚠️  Soniox warmup silence failed: {silence_sender.error}")
                        warmup_alive = False
                    elif not self._drain_warmup_stream(warmup_stream):
                        warmup_alive = False

                    if not warmup_alive:
                        self._close_soniox_stream_state(warmup_stream)
                        warmup_stream = None
                        next_prepare_attempt_at = time.monotonic() + 1.0
                    else:
                        switch_on_silence = audio_router.silence_ready(min_observed_at=warmup_stream.ready_at)
                        force_switch = self._should_force_rollover_switch(
                            active_stream.started_at,
                            rollover_seconds,
                        )
                        silence_elapsed = time.monotonic() - warmup_stream.silence_started_at if warmup_stream.silence_started_at else 0.0
                        if silence_elapsed >= 2.0 and (switch_on_silence or force_switch):
                            switch_reason = (
                                f"quiet gap ({audio_router.consecutive_silence_seconds():.2f}s, silence sent {silence_elapsed:.1f}s)"
                                if switch_on_silence
                                else "rollover guard deadline"
                            )
                            print(
                                f"🔁 Switching Soniox audio from stream #{active_stream.index} "
                                f"to stream #{warmup_stream.index} at {switch_reason}."
                            )

                            old_stream = active_stream
                            if warmup_stream.silence_sender is not None:
                                warmup_stream.silence_sender.stop()
                                warmup_stream.silence_sender = None

                            if not audio_router.switch_target(
                                warmup_stream.ws,
                                expected_current=old_stream.ws,
                            ):
                                disconnect_reason = "failed to switch audio to warmed Soniox stream"
                                break

                            active_stream = warmup_stream
                            warmup_stream = None
                            current_api_key = active_stream.api_key
                            self.ws = active_stream.ws
                            self.last_sent_count = active_stream.sent_count

                            # Finalize old stream in background so main loop
                            # immediately starts processing new stream responses.
                            threading.Thread(
                                target=self._finalize_and_close_stream,
                                args=(old_stream, loop),
                                daemon=True,
                                name=f"soniox-finalize-{old_stream.index}",
                            ).start()
                            disconnect_reason = "stream rollover"
                            continue

                try:
                    recv_timeout = (
                        STREAM_ROLLOVER_RECV_TIMEOUT_SECONDS
                        if rollover_seconds is not None or sleep_idle_seconds is not None
                        else None
                    )
                    message = active_stream.ws.recv(timeout=recv_timeout)
                except TimeoutError:
                    continue
                except ConnectionClosed as error:
                    if rollover_seconds is not None and self._stream_is_near_rollover_limit(
                        active_stream.started_at,
                        rollover_seconds,
                    ):
                        print(
                            f"🔁 Soniox stream #{active_stream.index} closed near configured duration; "
                            "rolling over..."
                        )
                        audio_router.clear_target(active_stream.ws)
                        self._close_soniox_stream_state(active_stream)

                        if warmup_stream is not None:
                            if warmup_stream.silence_sender is not None:
                                warmup_stream.silence_sender.stop()
                                warmup_stream.silence_sender = None
                            active_stream = warmup_stream
                            warmup_stream = None
                            current_api_key = active_stream.api_key
                            self.ws = active_stream.ws
                            self.last_sent_count = active_stream.sent_count
                            if not audio_router.set_target(active_stream.ws):
                                disconnect_reason = "failed to attach warmed Soniox stream after closure"
                                break
                            continue

                        try:
                            replacement = self._open_and_switch_to_replacement_stream(
                                audio_router,
                                active_stream,
                                current_api_key,
                                stream_index,
                                audio_format,
                                translation,
                                translation_target_lang,
                                loop,
                                "stream closed near configured duration",
                            )
                            if replacement is None:
                                disconnect_reason = "failed to attach replacement Soniox stream"
                                break
                            active_stream, current_api_key, stream_index = replacement
                            self.ws = active_stream.ws
                            self.last_sent_count = active_stream.sent_count
                            continue
                        except Exception as reconnect_error:
                            disconnect_reason = f"connection closed during rollover and reconnect failed: {reconnect_error}"
                            print(f"Error reconnecting to Soniox after rollover closure: {reconnect_error}")
                            break

                    info = relay_close_info(getattr(error, "code", None))
                    if info is not None:
                        relay_close = info
                        disconnect_reason = f"relay: {info[0]}"
                    else:
                        disconnect_reason = f"connection closed: {error}"
                    break
                except KeyboardInterrupt:
                    disconnect_reason = "interrupted by user"
                    notify_disconnect = False
                    print("\n⏹️ Interrupted by user.")
                    if self.stop_event:
                        self.stop_event.set()
                    break
                except Exception as error:
                    disconnect_reason = f"connection error: {error}"
                    print(f"Error connecting to Soniox: {error}")
                    break

                try:
                    res = json.loads(message)
                except Exception as error:
                    print(f"⚠️  Failed to parse Soniox response: {error}")
                    continue

                active_stream.sent_count, should_end, reason = self._process_soniox_response(
                    res,
                    active_stream.all_final_tokens,
                    active_stream.sent_count,
                    loop,
                )
                if should_end:
                    disconnect_reason = reason or "stream ended"
                    if rollover_seconds is not None and self._stream_is_near_rollover_limit(
                        active_stream.started_at,
                        rollover_seconds,
                    ):
                        print(
                            f"🔁 Soniox stream #{active_stream.index} ended near configured duration; "
                            "rolling over..."
                        )
                        audio_router.clear_target(active_stream.ws)
                        self._close_soniox_stream_state(active_stream)

                        if warmup_stream is not None:
                            if warmup_stream.silence_sender is not None:
                                warmup_stream.silence_sender.stop()
                                warmup_stream.silence_sender = None
                            active_stream = warmup_stream
                            warmup_stream = None
                            current_api_key = active_stream.api_key
                            self.ws = active_stream.ws
                            self.last_sent_count = active_stream.sent_count
                            if not audio_router.set_target(active_stream.ws):
                                disconnect_reason = "failed to attach warmed Soniox stream after finish"
                                break
                            continue
                        replacement = self._open_and_switch_to_replacement_stream(
                            audio_router,
                            active_stream,
                            current_api_key,
                            stream_index,
                            audio_format,
                            translation,
                            translation_target_lang,
                            loop,
                            "stream finished near configured duration",
                        )
                        if replacement is None:
                            disconnect_reason = "failed to attach replacement Soniox stream after finish"
                            break
                        active_stream, current_api_key, stream_index = replacement
                        self.ws = active_stream.ws
                        self.last_sent_count = active_stream.sent_count
                        disconnect_reason = "stream rollover"
                        continue
                    break

        finally:
            # Clean up background warmup future
            if warmup_future is not None:
                if warmup_future.done() and not warmup_future.cancelled():
                    try:
                        leaked = warmup_future.result(timeout=0)
                        self._close_soniox_stream_state(leaked)
                    except Exception:
                        pass
                else:
                    warmup_future.cancel()
                warmup_future = None
            key_fetch_executor.shutdown(wait=False)
            stop_requested = bool(self.stop_event and self.stop_event.is_set())
            if self.stop_event:
                self.stop_event.set()
            self.stop_event = None
            self.ws = None
            self._relay_session_active = False
            audio_router.close()
            self._stop_audio_streamer()
            if warmup_stream is not None:
                self._close_soniox_stream_state(warmup_stream)
            if active_stream is not None:
                self._close_soniox_stream_state(active_stream)
            self.thread = None
            if notify_disconnect and not stop_requested:
                try:
                    disconnect_payload = {
                        "type": "session_disconnected",
                        "reason": disconnect_reason,
                    }
                    if relay_close is not None:
                        tag, terminal, message = relay_close
                        disconnect_payload["code"] = tag
                        disconnect_payload["relay_terminal"] = bool(terminal)
                        disconnect_payload["message"] = message
                    elif _is_api_key_error_reason(disconnect_reason):
                        disconnect_payload["code"] = "api_key"
                    self.last_disconnect_payload = disconnect_payload
                    asyncio.run_coroutine_threadsafe(
                        self.broadcast_callback(disconnect_payload),
                        loop,
                    )
                except Exception as notify_error:
                    print(f"⚠️  Failed to notify clients about Soniox disconnect: {notify_error}")
            elif stop_requested:
                self.last_disconnect_payload = None
