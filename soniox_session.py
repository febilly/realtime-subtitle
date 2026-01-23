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
)
from soniox_client import get_config
from audio_capture import AudioStreamer
from osc_manager import osc_manager
from llm_client import LlmConfig, chat_completion, extract_answer_tag, LlmError


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
        self._llm_refine_enabled = bool(LLM_REFINE_DEFAULT_ENABLED)

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

    def _process_token_for_sentence(self, token: dict) -> list[dict]:
        """处理 token 并根据断句模式检测句子结束。"""
        separators: list[dict] = []
        text = token.get("text", "")
        speaker = str(token.get("speaker", "?"))
        translation_status = token.get("translation_status", "original")

        # <end> 标记处理
        if text == "<end>":
            if self._segment_mode == "translation" and speaker in self._sentence_buffers:
                if self._trigger_sentence_finalization(speaker):
                    separators.append(self._make_separator_token("translation"))
            return separators

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

        if self._segment_mode == "punctuation" and translation_status == "translation":
            if self._is_sentence_ending_punctuation(text):
                if self._trigger_sentence_finalization(speaker):
                    separators.append(self._make_separator_token("punctuation"))

        return separators

    def _is_sentence_ending_punctuation(self, text: str) -> bool:
        """检测是否为句末标点"""
        value = (text or "").strip()
        if not value:
            return False
        ending_chars = ("。", "！", "？", ".", "!", "?", "︒", "︕", "︖", "…")
        return value.endswith(ending_chars)

    def _trigger_sentence_finalization(self, speaker: str) -> bool:
        """触发句子完成处理"""
        buffer = self._sentence_buffers.pop(speaker, None)
        if not buffer:
            return False

        original_tokens = buffer.get("original_tokens") or []
        translation_tokens = buffer.get("translation_tokens") or []

        if not original_tokens or not translation_tokens:
            return False

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

        if is_llm_refine_available() and self._llm_refine_enabled:
            try:
                result = await self._perform_refine(source, translation, context_items)
                if result.get("status") == "ok" and not result.get("no_change"):
                    refined_translation = result.get("refined_translation") or translation
                    no_change = False
            except Exception as error:
                print(f"LLM refine error: {error}")

        await self.broadcast_callback({
            "type": "refine_result",
            "sentence_id": sentence_id,
            "source": source,
            "original_translation": translation,
            "refined_translation": refined_translation if not no_change else None,
            "no_change": no_change,
            "speaker": speaker,
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
            "You are a strict QA system for real-time translation. Your task is to verify a draft translation against the source text.\n"
            "\n\n"
            "Rules:\n"
            "1. Readability vs Accuracy: This is real-time subtitles. Large rewrites disrupt the user's reading flow. Balance minimal changes with correctness: only rewrite when needed to fix a major error or severe sentence-structure issue.\n"
            "2. Preserve Question Form: If the draft translation is a question, keep it a question in the output (preserve question intent and punctuation such as '?' where appropriate).\n"
            "3. Major Errors Only: Fix only mistranslations, hallucinations, opposite meanings, missing key entities, or sentence structure issues that make the translation hard to understand.\n"
            "   - Do NOT fix minor word-order awkwardness or small grammar issues if the meaning is already correct.\n"
            "4. Minimal Edits: Keep the draft as-is unless a major error requires change.\n\n"
            "If NO major error exists (even if wording/style is poor): output exactly:\n"
            f"<answer>{NO_CHANGE_MARKER}</answer>\n\n"
            "If a major error exists: output ONLY the corrected translation wrapped exactly as:\n"
            f"<answer>{PLACEHOLDER_ANSWER}</answer>\n\n"
            "Do NOT add explanations.\n\n"
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

            return {"status": "ok", "no_change": False, "refined_translation": refined}

        return {"status": "ok", "no_change": True}

    def set_segment_mode(self, mode: str) -> tuple[bool, str]:
        """设置断句模式并广播给所有前端"""
        if mode not in ("translation", "endpoint", "punctuation"):
            return False, f"Invalid segment mode: {mode}"

        self._segment_mode = mode
        self._sentence_buffers.clear()
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

    def set_llm_refine_enabled(self, enabled: bool):
        self._llm_refine_enabled = bool(enabled)

    def get_llm_refine_enabled(self) -> bool:
        return self._llm_refine_enabled
    
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

                        separator_tokens: list[dict] = []
                        outgoing_final_tokens: list[dict] = []

                        if new_final_tokens:
                            for token in new_final_tokens:
                                if token.get("text") is None:
                                    continue
                                separator_tokens.extend(self._process_token_for_sentence(token))
                                if token.get("text") != "<end>":
                                    outgoing_final_tokens.append(token)

                        if endpoint_detected and self._segment_mode == "endpoint":
                            speaker_value = None
                            for token in reversed(new_final_tokens):
                                spk = token.get("speaker")
                                if spk is not None and spk != "":
                                    speaker_value = str(spk)
                                    break
                            if speaker_value is None and self._sentence_buffers:
                                speaker_value = next(iter(self._sentence_buffers.keys()))
                            if speaker_value and self._trigger_sentence_finalization(speaker_value):
                                separator_tokens.append(self._make_separator_token("endpoint"))

                        # 将新的final tokens写入日志
                        if outgoing_final_tokens and not self.is_paused:
                            self.logger.write_to_log([t for t in outgoing_final_tokens if not t.get("is_separator")])

                        # 如果有新的数据，发送给前端（暂停时也显示，只是不记录）
                        if outgoing_final_tokens or separator_tokens or non_final_tokens:
                            asyncio.run_coroutine_threadsafe(
                                self.broadcast_callback({
                                    "type": "update",
                                    "final_tokens": outgoing_final_tokens + separator_tokens,
                                    "non_final_tokens": [t for t in non_final_tokens if t.get("text") != "<end>"],
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
