"""
Soniox会话模块 - 管理与Soniox服务的WebSocket会话
"""
import json
import threading
import asyncio
import time
import re
from typing import Optional, Tuple

from websockets import ConnectionClosedOK
from websockets.sync.client import connect as sync_connect

from config import (
    SONIOX_WEBSOCKET_URL,
    USE_TWITCH_AUDIO_STREAM,
    TWITCH_CHANNEL,
    TWITCH_STREAM_QUALITY,
    FFMPEG_PATH,
    DEFAULT_SEGMENT_MODE,
    is_llm_refine_available,
    LLM_REFINE_CONTEXT_COUNT,
    LLM_PROMPT_SUFFIX,
    LLM_REFINE_MAX_TOKENS,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
    get_llm_api_key,
    LLM_REFINE_DEFAULT_ENABLED,
    LLM_REFINE_DEFAULT_MODE,
)
from soniox_client import get_config
from audio_capture import AudioStreamer
from osc_manager import osc_manager
from llm_client import LlmConfig, chat_completion, extract_answer_tag, LlmError


LLM_REFINE_MODES = ("off", "refine", "translate")


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
        self.sample_rate = 16000
        self.chunk_size = 3840
        self.audio_source = "twitch" if USE_TWITCH_AUDIO_STREAM else "system"
        self.audio_streamer: Optional[object] = None
        self.audio_lock = threading.Lock()
        self.osc_translation_enabled = False
        self._segment_mode = DEFAULT_SEGMENT_MODE if DEFAULT_SEGMENT_MODE in ("translation", "endpoint", "punctuation") else "punctuation"
        self._sentence_buffers: dict[str, dict] = {}
        self._refine_context_history: list[dict] = []
        default_mode = str(LLM_REFINE_DEFAULT_MODE or "").strip().lower()
        if default_mode not in LLM_REFINE_MODES:
            default_mode = "refine" if bool(LLM_REFINE_DEFAULT_ENABLED) else "off"
        self._llm_refine_mode = default_mode
        self._llm_translate_state: dict[str, dict] = {}

        try:
            from config import TRANSLATION_TARGET_LANG
            self.translation_target_lang = str(TRANSLATION_TARGET_LANG)
        except Exception:
            self.translation_target_lang = "en"
    
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
        self.api_key = api_key
        self.audio_format = audio_format
        self.translation = translation
        self.loop = loop
        self._sentence_buffers.clear()
        self._refine_context_history.clear()
        self._llm_translate_state.clear()

        if translation_target_lang is not None:
            self.set_translation_target_lang(translation_target_lang)
        osc_manager.clear_history()
        
        # 初始化日志文件（如果还没有创建）
        if self.logger.log_file is None:
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
            self._llm_translate_state.clear()

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

        if source not in ("system", "microphone"):
            return False, "Invalid audio source (expected 'system' or 'microphone')."

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
                chunk_size=self.chunk_size
            )

        with self.audio_lock:
            self.audio_streamer = streamer

        streamer.start()

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
        if is_final is None:
            value["is_final"] = bool(token.get("is_final", False))
        else:
            value["is_final"] = bool(is_final)
        return value

    def _process_token_for_sentence(self, token: dict) -> None:
        """缓存 token 供后续断句/改进使用。"""
        text = token.get("text", "")
        if text == "<end>":
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

    def _is_sentence_ending_punctuation(self, text: str) -> bool:
        """检测是否为句末标点"""
        value = (text or "").strip()
        if not value:
            return False
        ending_chars = ("。", "！", "？", ".", "!", "?", "︒", "︕", "︖", "…")
        return value.endswith(ending_chars)

    def _trigger_sentence_finalization(self, speaker: str) -> bool:
        """触发句子完成处理"""
        buffer = self._sentence_buffers.get(speaker)
        if not buffer:
            return False

        original_tokens = buffer.get("original_tokens") or []
        translation_tokens = buffer.get("translation_tokens") or []

        if not original_tokens or not translation_tokens:
            return False

        self._sentence_buffers.pop(speaker, None)

        if not self.loop:
            return False

        asyncio.run_coroutine_threadsafe(
            self._finalize_sentence_async(speaker, original_tokens, translation_tokens),
            self.loop,
        )
        return True

    async def _finalize_sentence_async(self, speaker: str, original_tokens: list, translation_tokens: list):
        """异步执行 LLM 改进与 OSC 发送"""
        source = "".join([t.get("text", "") for t in original_tokens]).strip()
        translation = "".join([t.get("text", "") for t in translation_tokens]).strip()

        if not source or not translation:
            return

        sentence_id = f"backend-{int(time.time() * 1000)}-{speaker}"
        context_items = list(self._refine_context_history[-LLM_REFINE_CONTEXT_COUNT:])

        refined_translation = translation
        no_change = True

        mode = self.get_llm_refine_mode()
        if is_llm_refine_available() and mode != "off":
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
            "source": source,
            "original_translation": translation,
            "refined_translation": refined_translation if not no_change else None,
            "no_change": no_change,
        })

        if self.get_osc_translation_enabled():
            try:
                osc_manager.add_message_and_send(refined_translation, ongoing=False, speaker=speaker)
            except Exception as error:
                print(f"OSC send failed: {error}")

        self._refine_context_history.append({"source": source, "translation": refined_translation})
        if len(self._refine_context_history) > 20:
            self._refine_context_history = self._refine_context_history[-20:]

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
        if isinstance(context_items, list) and LLM_REFINE_CONTEXT_COUNT > 0:
            max_items = min(int(LLM_REFINE_CONTEXT_COUNT), 20)
            for item in context_items[:max_items]:
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
        if isinstance(context_items, list) and LLM_REFINE_CONTEXT_COUNT > 0:
            max_items = min(int(LLM_REFINE_CONTEXT_COUNT), 20)
            for item in context_items[:max_items]:
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

    def _get_llm_translate_state(self, speaker: str) -> dict:
        if speaker not in self._llm_translate_state:
            self._llm_translate_state[speaker] = {
                "final_original_tokens": [],
                "final_translation_tokens": [],
                "non_final_original_tokens": [],
                "non_final_translation_tokens": [],
                "last_trigger_key": None,
                "request_id": 0,
            }
        return self._llm_translate_state[speaker]

    def _reset_llm_translate_state(self, speaker: Optional[str] = None) -> None:
        if speaker is None:
            self._llm_translate_state.clear()
            return
        self._llm_translate_state.pop(speaker, None)

    def _update_llm_translate_state(self, new_final_tokens: list, non_final_tokens: list) -> None:
        if self.get_llm_refine_mode() != "translate":
            return

        for token in new_final_tokens:
            text = token.get("text")
            if text is None or text == "<end>":
                continue
            speaker = str(token.get("speaker", "?"))
            translation_status = token.get("translation_status", "original")
            state = self._get_llm_translate_state(speaker)
            if translation_status == "translation":
                state["final_translation_tokens"].append(token)
            else:
                state["final_original_tokens"].append(token)

        non_final_by_speaker: dict[str, dict[str, list]] = {}
        for token in non_final_tokens:
            text = token.get("text")
            if text is None or text == "<end>":
                continue
            speaker = str(token.get("speaker", "?"))
            translation_status = token.get("translation_status", "original")
            bucket = non_final_by_speaker.setdefault(
                speaker,
                {"original": [], "translation": []},
            )
            if translation_status == "translation":
                bucket["translation"].append(token)
            else:
                bucket["original"].append(token)

        all_speakers = set(self._llm_translate_state.keys()) | set(non_final_by_speaker.keys())
        for speaker in all_speakers:
            state = self._get_llm_translate_state(speaker)
            bucket = non_final_by_speaker.get(speaker) or {"original": [], "translation": []}
            state["non_final_original_tokens"] = bucket.get("original", [])
            state["non_final_translation_tokens"] = bucket.get("translation", [])

    def _has_sentence_ending_punctuation_tokens(self, tokens: list) -> bool:
        for token in tokens or []:
            text = token.get("text", "")
            if text and self._is_sentence_ending_punctuation(str(text)):
                return True
        return False

    def _maybe_trigger_llm_translate(self) -> None:
        if self.get_llm_refine_mode() != "translate":
            return
        if not is_llm_refine_available():
            return
        if not self.loop:
            return

        context_items = list(self._refine_context_history[-LLM_REFINE_CONTEXT_COUNT:])

        for speaker, state in list(self._llm_translate_state.items()):
            translation_tokens = (state.get("final_translation_tokens") or []) + (state.get("non_final_translation_tokens") or [])
            if not translation_tokens:
                continue
            if not self._has_sentence_ending_punctuation_tokens(translation_tokens):
                continue

            original_tokens = (state.get("final_original_tokens") or []) + (state.get("non_final_original_tokens") or [])
            source_text = "".join([t.get("text", "") for t in original_tokens]).strip()
            translation_text = "".join([t.get("text", "") for t in translation_tokens]).strip()

            if not source_text or not translation_text:
                continue

            trigger_key = f"{source_text}||{translation_text}"
            if trigger_key == state.get("last_trigger_key"):
                continue

            state["last_trigger_key"] = trigger_key
            state["request_id"] = int(state.get("request_id", 0)) + 1
            request_id = state["request_id"]

            asyncio.run_coroutine_threadsafe(
                self._perform_translate_and_broadcast(
                    speaker,
                    source_text,
                    translation_text,
                    context_items,
                    request_id,
                ),
                self.loop,
            )

    async def _perform_translate_and_broadcast(
        self,
        speaker: str,
        source_text: str,
        translation_text: str,
        context_items: list,
        request_id: int,
    ) -> None:
        state = self._llm_translate_state.get(speaker)
        if not state or state.get("request_id") != request_id:
            return

        refined_translation = translation_text
        try:
            result = await self._perform_translate(source_text, context_items)
            if result.get("status") == "ok":
                translated = (result.get("translation") or "").strip()
                if translated:
                    refined_translation = translated
        except Exception as error:
            print(f"LLM translate error: {error}")

        state = self._llm_translate_state.get(speaker)
        if not state or state.get("request_id") != request_id:
            return

        await self.broadcast_callback({
            "type": "refine_result",
            "source": source_text,
            "original_translation": translation_text,
            "refined_translation": refined_translation,
            "no_change": False,
        })

        if self.get_osc_translation_enabled():
            try:
                osc_manager.add_message_and_send(refined_translation, ongoing=False, speaker=speaker)
            except Exception as error:
                print(f"OSC send failed: {error}")

        self._refine_context_history.append({"source": source_text, "translation": refined_translation})
        if len(self._refine_context_history) > 20:
            self._refine_context_history = self._refine_context_history[-20:]

    def set_segment_mode(self, mode: str) -> tuple[bool, str]:
        """设置断句模式并广播给所有前端"""
        if mode not in ("translation", "endpoint", "punctuation"):
            return False, f"Invalid segment mode: {mode}"

        if mode == "translation" and self.get_llm_refine_mode() == "translate":
            return False, "Segment mode 'translation' is disabled when LLM translate mode is enabled"

        self._segment_mode = mode
        self._sentence_buffers.clear()
        self._llm_translate_state.clear()
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
        self._llm_translate_state.clear()

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
                    "message": "Soniox API key is missing. Please set it in .env file."
                }),
                loop
            )
            return

        config = get_config(api_key, audio_format, translation, translation_target_lang=translation_target_lang)

        print("Connecting to Soniox...")
        self.stop_event = threading.Event()
        try:
            with sync_connect(SONIOX_WEBSOCKET_URL) as ws:
                self.ws = ws
                # Send first request with config.
                ws.send(json.dumps(config))

                # Start streaming audio in the background
                self._start_audio_streamer(ws)

                print("Session started.")

                # 累积所有的final tokens
                all_final_tokens: list[dict] = []
                
                try:
                    while True:
                        message = ws.recv()
                        res = json.loads(message)

                        # Error from server.
                        if res.get("error_code") is not None:
                            print(f"Error: {res['error_code']} - {res['error_message']}")
                            break

                        # Parse tokens from current response.
                        non_final_tokens: list[dict] = []

                        for token in res.get("tokens", []):
                            if token.get("text"):
                                if token.get("is_final"):
                                    # Final tokens累积添加
                                    all_final_tokens.append(token)
                                else:
                                    # Non-final tokens每次重置
                                    non_final_tokens.append(token)

                        # 计算新增的final tokens（增量部分）
                        new_final_tokens = all_final_tokens[self.last_sent_count:]
                        endpoint_detected = bool(res.get("endpoint_detected", False))

                        self._update_llm_translate_state(new_final_tokens, non_final_tokens)
                        if self._segment_mode == "punctuation":
                            self._maybe_trigger_llm_translate()

                        separator_tokens: list[dict] = []
                        outgoing_final_tokens: list[dict] = []

                        if new_final_tokens:
                            for token in new_final_tokens:
                                if token.get("text") is None:
                                    continue
                                self._process_token_for_sentence(token)
                                if token.get("text") != "<end>":
                                    outgoing_final_tokens.append(self._minify_token(token, is_final=True))

                        if new_final_tokens or endpoint_detected:
                            if self._segment_mode == "translation":
                                translation_hit = any(
                                    t.get("text") not in (None, "<end>")
                                    and t.get("translation_status") == "translation"
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
                                        self._reset_llm_translate_state(speaker_value)
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
                                        self._reset_llm_translate_state(speaker_value)
                                    separator_tokens.append(self._make_separator_token("endpoint"))

                            elif self._segment_mode == "punctuation":
                                punctuation_hit = any(
                                    t.get("text") == "<end>"
                                    for t in new_final_tokens
                                ) or any(
                                    t.get("text")
                                    and t.get("translation_status") == "translation"
                                    and self._is_sentence_ending_punctuation(t.get("text", ""))
                                    for t in new_final_tokens
                                )
                                if punctuation_hit:
                                    speaker_value = None
                                    for token in reversed(new_final_tokens):
                                        if token.get("translation_status") == "translation" and token.get("text"):
                                            spk = token.get("speaker")
                                            if spk is not None and spk != "":
                                                speaker_value = str(spk)
                                                break
                                    if speaker_value is None and self._sentence_buffers:
                                        speaker_value = next(iter(self._sentence_buffers.keys()))
                                    if speaker_value and self._trigger_sentence_finalization(speaker_value):
                                        self._reset_llm_translate_state(speaker_value)
                                        separator_tokens.append(self._make_separator_token("punctuation"))

                        # 将新的final tokens写入日志
                        if outgoing_final_tokens and not self.is_paused:
                            self.logger.write_to_log([t for t in new_final_tokens if t.get("text") != "<end>"])

                        # 如果有新的数据，发送给前端（暂停时也显示，只是不记录）
                        if outgoing_final_tokens or separator_tokens or non_final_tokens:
                            asyncio.run_coroutine_threadsafe(
                                self.broadcast_callback({
                                    "type": "update",
                                    "final_tokens": outgoing_final_tokens + [self._minify_token(t) for t in separator_tokens],
                                    "non_final_tokens": [self._minify_token(t, is_final=False) for t in non_final_tokens if t.get("text") != "<end>"]
                                }),
                                loop
                            )

                            # 更新已发送的计数
                            self.last_sent_count = len(all_final_tokens)

                        # Session finished.
                        if res.get("finished"):
                            print("Session finished.")
                            break

                except ConnectionClosedOK:
                    pass
                except KeyboardInterrupt:
                    print("\n⏹️ Interrupted by user.")
                    if self.stop_event:
                        self.stop_event.set()
                except Exception as e:
                    print(f"Error: {e}")
        finally:
            if self.stop_event:
                self.stop_event.set()
            self.stop_event = None
            self.ws = None
            self._stop_audio_streamer()
            self.thread = None
