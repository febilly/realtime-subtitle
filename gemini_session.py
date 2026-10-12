"""
Gemini会话模块 - 管理与Gemini Live Translation服务的WebSocket会话
"""
import asyncio
import re
import time
from typing import Optional

import config  # noqa: F401  (tests reach the config stub through this module)
from config import (
    GEMINI_STREAM_DURATION_SECONDS,
    SLEEP_IDLE_SECONDS,
    SLEEP_PRE_ROLL_SECONDS,
    SLEEP_SPEECH_GRACE_SECONDS,
    SLEEP_SPEECH_WINDOW_SECONDS,
    SLEEP_WAKE_SPEECH_SECONDS,
    SLEEP_WAKE_SPEECH_WINDOW_SECONDS,
    SLEEP_VAD_THRESHOLD,
    SLEEP_WAKE_VAD_THRESHOLD,
    ROLLOVER_VAD_THRESHOLD,
    is_llm_refine_available,
    normalize_language_code,
    GEMINI_FULLWIDTH_PUNCT_FIX,
    TARGET_LANG_1,
    TARGET_LANG_2,
)
from audio_router import AudioSendRouter
from stream_session_runtime import (
    StreamState as _StreamState,
    STREAM_ROLLOVER_AUDIO_BUFFER_CHUNKS,
    STREAM_ROLLOVER_RECV_TIMEOUT_SECONDS,
    STREAM_ROLLOVER_SILENCE_HOLD_SECONDS,
    StreamRuntimeSettings,
    StreamSessionHooks,
    run_stream_session,
)
import gemini_client
from osc_manager import osc_manager
from osc_draft import OscDraftPublisher
from realtime_session_base import RealtimeSessionBase

# Half-width ASCII punctuation -> full-width, applied only when wedged between
# two CJK (Han) characters (see fix_fullwidth_punctuation_between_cjk).
HALFWIDTH_TO_FULLWIDTH_PUNCT = {",": "，", ".": "。", "?": "？", "!": "！"}
_CJK_JA_CHAR_CLASS = (
    r"㐀-䶿一-鿿豈-﫿" # CJK Ideographs (Han characters)
    r"\u3040-\u309F"  # Hiragana
    r"\u30A0-\u30FF"  # Katakana
    r"\u31F0-\u31FF"  # Katakana phonetic extensions
    r"\uFF66-\uFF9D"  # Halfwidth Katakana
)
FULLWIDTH_PUNCT_AFTER_CJK_JA_RE = re.compile(
    rf"(?<=[{_CJK_JA_CHAR_CLASS}])([,.?!])"
)
# Gemini streams sentence-ending punctuation as its own chunk with a leading
# space (e.g. "…ごめん" then " 。"), which renders as "…ごめん 。". A space directly
# before East Asian punctuation is always spurious, so strip it. Only ASCII /
# ideographic spaces are removed (not newlines).
EAST_ASIAN_PUNCT_NO_LEADING_SPACE = "。、，！？：；．…‥｡､」』）】〕》〉〗〙｣"
SPACE_BEFORE_EAST_ASIAN_PUNCT_RE = re.compile(
    rf"[ \t　]+(?=[{EAST_ASIAN_PUNCT_NO_LEADING_SPACE}])"
)


def fix_fullwidth_punctuation_after_cjk_ja(text: str, prev_char: Optional[str] = None) -> str:
    """Convert a half-width , . ? ! to its full-width form when it is preceded
    by a CJK (Han) or Japanese (Hiragana/Katakana) character. If prev_char is provided,
    it is treated as the character preceding text (to handle chunk boundaries)."""
    value = "" if text is None else str(text)
    if not GEMINI_FULLWIDTH_PUNCT_FIX or not value:
        return value

    if prev_char:
        combined = prev_char + value
        fixed = FULLWIDTH_PUNCT_AFTER_CJK_JA_RE.sub(
            lambda m: HALFWIDTH_TO_FULLWIDTH_PUNCT[m.group(1)], combined
        )
        return fixed[len(prev_char):]
    else:
        return FULLWIDTH_PUNCT_AFTER_CJK_JA_RE.sub(
            lambda m: HALFWIDTH_TO_FULLWIDTH_PUNCT[m.group(1)], value
        )


def strip_space_before_east_asian_punctuation(text: str) -> str:
    """Remove spaces/tabs that sit immediately before East Asian punctuation,
    e.g. "…ごめん 。" -> "…ごめん。"."""
    value = "" if text is None else str(text)
    if not value:
        return value
    return SPACE_BEFORE_EAST_ASIAN_PUNCT_RE.sub("", value)


def normalize_gemini_text(text: str, prev_char: Optional[str] = None) -> str:
    """Apply all per-token cleanups to Gemini's returned source/translation text."""
    value = strip_space_before_east_asian_punctuation(text)
    return fix_fullwidth_punctuation_after_cjk_ja(value, prev_char)


class GeminiSession(RealtimeSessionBase):
    """Gemini会话管理器"""

    PROVIDER_NAME = "Gemini"
    CONFIG_PREFIX = "GEMINI"
    CLIENT_MODULE = "gemini_client"
    # Gemini Live Translation specifies 100 ms PCM chunks at 16 kHz.
    CHUNK_SIZE = 1600

    def __init__(self, logger, broadcast_callback):
        super().__init__(logger, broadcast_callback)
        # Speakers whose translation for the current sentence already ended (with
        # sentence-ending punctuation) but whose source hasn't caught up yet. The
        # line break is deferred until the source is also sentence-complete, so
        # Gemini's slightly-late trailing source tokens don't spill onto the next
        # line. Flushed unconditionally on a turnComplete <end>.
        self._pending_translation_finalization: set[str] = set()
        self._last_input_language = ""
        self._last_char_original = None
        self._last_char_translation = None

    # --- RealtimeSessionBase hooks ---

    def _clear_segmentation_state(self) -> None:
        super()._clear_segmentation_state()
        self._pending_translation_finalization.clear()

    def _reset_run_state(self) -> None:
        self._last_input_language = ""
        self._last_char_original = None
        self._last_char_translation = None

    def _close_ws(self, ws, reason: str) -> None:
        ws.close(reason)

    def _request_stream_finalize(self, ws) -> None:
        ws.finalize()

    def _warmup_response_ends_stream(self, stream: _StreamState, res) -> bool:
        if isinstance(res, dict) and res.get("error"):
            error = res.get("error") or {}
            print(f"⚠️  Gemini warmup error {error.get('code')}: {error.get('message', '')}")
            return True
        if isinstance(res, dict) and ("goAway" in res or "go_away" in res):
            print(f"⚠️  Gemini warmup stream #{stream.index} received goAway")
            return True
        return False

    def _two_way_pair(self) -> tuple[str, str]:
        return TARGET_LANG_1, TARGET_LANG_2

    def _live_osc_preview_suppressed(self) -> bool:
        return self.get_llm_refine_mode() == "translate"

    def set_translation_target_lang(self, lang: str) -> tuple[bool, str]:
        from config import canonicalize_language_code, is_supported_language_code

        canonical = canonicalize_language_code(lang)
        if not is_supported_language_code(canonical):
            return False, f"Unsupported translation target language: {lang}"

        previous = self.translation_target_lang
        self.translation_target_lang = canonical
        if previous != canonical:
            print(f"🌐 Translation target language updated: {previous} -> {canonical}")
        return True, "ok"
    
    def _process_token_for_sentence(self, token: dict) -> None:
        """缓存 token 供后续断句/改进使用。"""
        text = token.get("text", "")
        if self._is_internal_token(text):
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

    def _flush_stale_quote_boundaries(self, token: dict, outgoing_final_tokens: list[dict]) -> None:
        for speaker in self._pending_boundaries.flush_stale_quote_boundaries_before_incompatible_token(token):
            if not speaker:
                continue
            self._pending_translation_finalization.discard(speaker)
            if self._trigger_sentence_finalization(speaker):
                outgoing_final_tokens.append(
                    self._minify_token(self._make_separator_token("punctuation"))
                )

    def _buffer_source_is_sentence_complete(self, speaker: str) -> bool:
        """Whether the speaker's buffered source text ends on sentence-ending
        punctuation (i.e. Gemini has streamed the whole source sentence)."""
        buffer = self._sentence_buffers.get(speaker)
        if not buffer:
            return False
        text = self._join_token_texts(buffer.get("original_tokens") or [])
        return bool(text) and self._is_sentence_ending_punctuation(text)

    def _trigger_sentence_finalization(self, speaker: str) -> bool:
        """触发句子完成处理"""
        buffer = self._sentence_buffers.get(speaker)
        if not buffer:
            return False

        original_tokens = buffer.get("original_tokens") or []
        translation_tokens = buffer.get("translation_tokens") or []

        if not original_tokens:
            return False

        if not translation_tokens and self._expects_translation_stream(original_tokens):
            return False

        self._sentence_buffers.pop(speaker, None)
        self._clear_pending_boundaries_for_speaker(speaker)
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

        self._broadcast_foreign_speech(source, original_tokens)

        fallback_to_source = not translation and self._can_use_source_as_translation(original_tokens)
        display_translation = translation if translation else (source if fallback_to_source else "")
        if not display_translation:
            return

        context_items = self._get_dynamic_context_items()

        utterance_target_lang = (
            self._osc_translation_language_for_comparison(original_tokens)
            or normalize_language_code(self.get_translation_target_lang())
        )

        mode = self.get_llm_refine_mode()

        # 混合: push the fast built-in draft to the chatbox immediately (before the
        # LLM), so viewers never wait on refine latency; a high/critical refine
        # replaces that same line afterwards. 准确 publishes once, after the LLM.
        osc_pub = OscDraftPublisher(
            osc_manager,
            enabled=self.get_osc_translation_enabled(),
            wants_draft=(mode != "translate"),
        )
        osc_pub.send_draft(self._select_osc_text(display_translation, source, original_tokens))

        refined_translation = display_translation
        no_change = True

        if is_llm_refine_available() and mode != "off" and translation:
            try:
                if mode == "refine":
                    refine_kwargs = self._target_lang_kwargs(self._perform_refine, utterance_target_lang)

                    result = await self._perform_refine(
                        source, translation, context_items, **refine_kwargs
                    )
                    if result.get("status") == "ok" and not result.get("no_change"):
                        refined_translation = result.get("refined_translation") or translation
                        no_change = False
                elif mode == "translate":
                    translate_kwargs = self._target_lang_kwargs(self._perform_translate, utterance_target_lang)

                    result = await self._perform_translate(
                        source, context_items, **translate_kwargs
                    )
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

        osc_pub.publish_final(
            self._select_osc_text(refined_translation, source, original_tokens),
            no_change=no_change,
        )

        self._remember_refine_context({"source": source, "translation": refined_translation})

    def set_translation_mode(self, mode: str) -> tuple[bool, str, bool]:
        """Apply the unified 翻译模式 (fast/accurate/hybrid), mirroring the soniox
        session. 混合 shows the built-in draft and refines it in place (internal
        refine); 准确 does a full LLM translate. Gemini always keeps its built-in
        translation on, so no stream restart is ever needed here."""
        normalized = str(mode or "").strip().lower()
        # "refine" is accepted as a legacy alias and normalized to 混合.
        if normalized == "refine":
            normalized = "hybrid"
        mapping = {"fast": "off", "accurate": "translate", "hybrid": "refine"}
        if normalized not in mapping:
            return False, f"Invalid translation mode: {mode}", False
        self._llm_refine_mode = mapping[normalized]
        self._suppress_soniox_translation = False
        if mapping[normalized] != "off":
            self._hosted_llm.reset()
        return True, normalized, False

    def get_translation_mode(self) -> str:
        if self._llm_refine_mode == "refine":
            return "hybrid"
        if self._llm_refine_mode == "translate":
            return "accurate"
        return "fast"

    def _stream_rollover_seconds(self) -> float | None:
        if GEMINI_STREAM_DURATION_SECONDS is None:
            return None
        try:
            value = float(GEMINI_STREAM_DURATION_SECONDS)
        except Exception:
            return None
        if value <= 0:
            return None
        return value

    def _open_stream_state(
        self,
        api_key: str,
        stream_index: int,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
        *,
        warming: bool = False,
    ) -> _StreamState:
        if audio_format not in ("pcm_s16le", "auto"):
            raise ValueError(f"Unsupported audio_format: {audio_format}")
        label = f"stream #{stream_index}"
        purpose = " warmup" if warming else ""
        print(f"Connecting to Gemini ({label}{purpose})...")
        ws = gemini_client.connect_live(
            api_key,
            translation=translation,
            translation_target_lang=translation_target_lang,
            sample_rate=self.sample_rate,
            run_id=self.relay_run_id,
        )
        state = _StreamState(
            ws=ws,
            index=stream_index,
            api_key=api_key,
            started_at=time.monotonic(),
            ready_at=time.monotonic(),
            all_final_tokens=[],
        )
        print(f"Session started ({label}{purpose}).")
        return state

    def _convert_gemini_message(self, res: dict) -> dict:
        """将Gemini Live API消息转换为内部token流格式。

        返回字段：
        - tokens: Soniox风格token列表（全部is_final=True，单一说话人"1"）
        - endpoint_detected: turnComplete（说话回合结束）
        - translation_complete: generationComplete（本回合译文输出完毕）
        - finished / go_away: 服务端即将关闭流
        - error_code / error_message
        """
        out = {
            "tokens": [],
            "endpoint_detected": False,
            "translation_complete": False,
            "finished": False,
            "go_away": False,
            "error_code": None,
            "error_message": "",
        }
        if not isinstance(res, dict):
            return out

        error = res.get("error")
        if error:
            if isinstance(error, dict):
                out["error_code"] = error.get("code", "unknown")
                out["error_message"] = str(error.get("message", ""))
            else:
                out["error_code"] = "unknown"
                out["error_message"] = str(error)
            return out

        if "goAway" in res or "go_away" in res:
            out["finished"] = True
            out["go_away"] = True

        server_content = res.get("serverContent") or res.get("server_content") or {}
        if not isinstance(server_content, dict):
            return out

        def _make_token(text: str, translation_status: str, language: str | None, source_language: str | None) -> dict:
            return {
                "text": text,
                "is_final": True,
                "speaker": "1",
                "translation_status": translation_status,
                "language": language,
                "source_language": source_language,
            }

        input_transcription = (
            server_content.get("inputTranscription")
            or server_content.get("input_transcription")
            or {}
        )
        if isinstance(input_transcription, dict) and input_transcription.get("text"):
            language = normalize_language_code(
                input_transcription.get("languageCode")
                or input_transcription.get("language_code")
                or ""
            )
            if language:
                self._last_input_language = language
            else:
                language = self._last_input_language or None
            raw_text = str(input_transcription["text"])
            fixed_text = normalize_gemini_text(raw_text, self._last_char_original)
            if fixed_text:
                self._last_char_original = fixed_text[-1]
            out["tokens"].append(
                _make_token(
                    fixed_text,
                    "original",
                    language or None,
                    None,
                )
            )

        output_transcription = (
            server_content.get("outputTranscription")
            or server_content.get("output_transcription")
            or {}
        )
        if (
            isinstance(output_transcription, dict)
            and output_transcription.get("text")
            and (self.translation or "none") != "none"
        ):
            language = normalize_language_code(
                output_transcription.get("languageCode")
                or output_transcription.get("language_code")
                or ""
            ) or normalize_language_code(self.get_translation_target_lang()) or None
            raw_text = str(output_transcription["text"])
            fixed_text = normalize_gemini_text(raw_text, self._last_char_translation)
            if fixed_text:
                self._last_char_translation = fixed_text[-1]
            out["tokens"].append(
                _make_token(
                    fixed_text,
                    "translation",
                    language,
                    self._last_input_language or None,
                )
            )

        if server_content.get("generationComplete") or server_content.get("generation_complete"):
            out["translation_complete"] = True
            self._last_char_translation = None

        if server_content.get("turnComplete") or server_content.get("turn_complete"):
            out["endpoint_detected"] = True
            # Internal marker token consumed by segmentation, never displayed.
            out["tokens"].append(_make_token("<end>", "original", None, None))
            self._last_char_original = None
            self._last_char_translation = None

        # Translated audio chunks (modelTurn parts) are intentionally ignored:
        # this app renders subtitles only.
        return out

    def _process_stream_response(
        self,
        res: dict,
        all_final_tokens: list[dict],
        sent_count: int,
        loop: asyncio.AbstractEventLoop,
    ) -> tuple[int, bool, str | None]:
        """Process one Gemini response. Returns sent count, should end stream, reason."""
        converted = self._convert_gemini_message(res)

        if converted.get("error_code") is not None:
            message = converted.get("error_message", "")
            reason = f"server error {converted['error_code']}: {message}"
            print(f"Error: {converted['error_code']} - {message}")
            return sent_count, True, reason

        # Parse tokens from current response.
        non_final_tokens: list[dict] = []

        for token in converted.get("tokens", []):
            text = token.get("text")
            if text:
                if token.get("is_final"):
                    # Final tokens累积添加
                    all_final_tokens.append(token)
                elif not self._is_internal_token(text):
                    # Non-final tokens每次重置
                    non_final_tokens.append(token)

        # 计算新增的final tokens（增量部分）
        new_final_tokens = all_final_tokens[sent_count:]
        endpoint_detected = bool(converted.get("endpoint_detected", False))

        separator_tokens: list[dict] = []
        outgoing_final_tokens: list[dict] = []
        source_as_output = not self._expects_translation_stream(new_final_tokens)
        output_sentence_done = False

        if new_final_tokens:
            for index, token in enumerate(new_final_tokens):
                if token.get("text") is None:
                    continue
                is_internal = self._is_internal_token(token)
                context_text = "" if is_internal else self._sentence_context_text_for_token(token)
                if not is_internal and self._pending_boundaries.flush_before_token(token):
                    spk = token.get("speaker")
                    speaker = str(spk) if (spk is not None and spk != "") else None
                    if speaker:
                        self._pending_translation_finalization.discard(speaker)
                        if self._trigger_sentence_finalization(speaker):
                            outgoing_final_tokens.append(
                                self._minify_token(self._make_separator_token("punctuation"))
                            )
                if not is_internal:
                    self._flush_stale_quote_boundaries(token, outgoing_final_tokens)
                if (
                    not is_internal
                    and (token.get("translation_status") == "translation" or source_as_output)
                    and self._pending_boundaries.is_token_sentence_ending(
                        new_final_tokens,
                        index,
                        context_text=context_text,
                        is_internal_token=self._is_internal_token,
                        source_as_output=source_as_output,
                    )
                ):
                    output_sentence_done = True
                self._process_token_for_sentence(token)
                if not is_internal:
                    self._pending_boundaries.mark_after_token(
                        new_final_tokens,
                        index,
                        context_text=context_text,
                        is_internal_token=self._is_internal_token,
                        source_as_output=source_as_output,
                    )

        self._maybe_send_live_osc_translation(non_final_tokens)

        if new_final_tokens or endpoint_detected:
            # Sentence segmentation: by sentence-ending punctuation (or the
            # internal <end> marker emitted on Gemini turnComplete). Normally we
            # look at translation tokens, but when the speech is already in the
            # target language there are no translation tokens, so the source text
            # is used as the output — in that case segment on punctuation in the
            # original tokens too (gated by _expects_translation_stream so we
            # never cut before a real translation arrives).
            # A turnComplete <end> is a hard turn boundary: always flush.
            hard_end = any(t.get("text") == "<end>" for t in new_final_tokens)
            speaker_value = (
                self._last_token_speaker(
                    new_final_tokens,
                    lambda t: t.get("translation_status") == "translation" and bool(t.get("text")),
                )
                or self._last_token_speaker(
                    new_final_tokens,
                    lambda t: bool(t.get("text")) and not self._is_internal_token(t),
                )
                or self._any_buffered_speaker()
            )

            if speaker_value:
                # Remember an output-side sentence completion so we can cut once
                # the SOURCE catches up.
                if output_sentence_done:
                    self._pending_translation_finalization.add(speaker_value)
                # Defer the line break until the source is also sentence-complete:
                # Gemini frequently streams the last source tokens a beat after the
                # translation, and cutting on the translation alone would spill
                # those trailing source characters onto the next line (and feed the
                # LLM a truncated source). A turnComplete <end> flushes regardless.
                pending = speaker_value in self._pending_translation_finalization
                should_finalize = hard_end or (pending and self._buffer_source_is_sentence_complete(speaker_value))
                if should_finalize:
                    self._pending_translation_finalization.discard(speaker_value)
                    if self._trigger_sentence_finalization(speaker_value):
                        separator_tokens.append(self._make_separator_token("punctuation"))

        if new_final_tokens:
            for token in new_final_tokens:
                if token.get("text") is None:
                    continue
                if not self._is_internal_token(token):
                    outgoing_final_tokens.append(self._minify_token(token, is_final=True))

        # 将新的final tokens写入日志
        if outgoing_final_tokens and not self.is_paused:
            self.logger.write_to_log([t for t in new_final_tokens if not self._is_internal_token(t)])

        # 如果有新的数据，发送给前端（暂停时也显示，只是不记录）
        if self._broadcast_token_update(loop, outgoing_final_tokens, separator_tokens, non_final_tokens):
            # 更新已发送的计数
            sent_count = len(all_final_tokens)
            self.last_sent_count = sent_count

        if new_final_tokens:
            sent_count = len(all_final_tokens)
            self.last_sent_count = sent_count

        # Session finished / server requested shutdown (goAway).
        if converted.get("finished"):
            if converted.get("go_away"):
                print("Session received goAway from server.")
                return sent_count, True, "server goAway"
            print("Session finished.")
            return sent_count, True, "session finished"

        return sent_count, False, None

    
    def _run_session(
        self,
        api_key: str,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        settings = StreamRuntimeSettings(
            provider_name="Gemini",
            key_thread_prefix="gemini-key",
            finalize_thread_prefix="gemini-finalize-",
            recv_timeout_seconds=STREAM_ROLLOVER_RECV_TIMEOUT_SECONDS,
            audio_buffer_chunks=STREAM_ROLLOVER_AUDIO_BUFFER_CHUNKS,
            silence_hold_seconds=STREAM_ROLLOVER_SILENCE_HOLD_SECONDS,
            rollover_vad_threshold=ROLLOVER_VAD_THRESHOLD,
            sleep_idle_seconds=float(SLEEP_IDLE_SECONDS),
            sleep_pre_roll_seconds=SLEEP_PRE_ROLL_SECONDS,
            sleep_speech_grace_seconds=SLEEP_SPEECH_GRACE_SECONDS,
            sleep_speech_window_seconds=SLEEP_SPEECH_WINDOW_SECONDS,
            sleep_vad_threshold=SLEEP_VAD_THRESHOLD,
            sleep_wake_speech_seconds=SLEEP_WAKE_SPEECH_SECONDS,
            sleep_wake_speech_window_seconds=SLEEP_WAKE_SPEECH_WINDOW_SECONDS,
            sleep_wake_vad_threshold=SLEEP_WAKE_VAD_THRESHOLD,
        )
        hooks = StreamSessionHooks(
            open_stream=self._open_stream_state,
            close_stream=self._close_stream_state,
            process_response=self._process_stream_response,
            adjust_receive_timeout=lambda receive_timeout: receive_timeout,
            on_receive_timeout=lambda: None,
            should_rollover_finished=lambda reason, started_at, rollover: (
                reason == "server goAway"
                or self._stream_is_near_rollover_limit(started_at, rollover)
            ),
            finished_rollover_description=lambda reason: f"({reason})",
        )
        run_stream_session(
            self,
            api_key,
            audio_format,
            translation,
            translation_target_lang,
            loop,
            settings=settings,
            hooks=hooks,
            audio_router_factory=AudioSendRouter,
        )
