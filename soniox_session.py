"""
Soniox会话模块 - 管理与Soniox服务的WebSocket会话
"""
import json
import threading
import asyncio
from typing import Optional, Tuple

from websockets import ConnectionClosedOK
from websockets.sync.client import connect as sync_connect

from config import (
    SONIOX_WEBSOCKET_URL,
    USE_TWITCH_AUDIO_STREAM,
    TWITCH_CHANNEL,
    TWITCH_STREAM_QUALITY,
    FFMPEG_PATH,
    EXTERNAL_WS_NON_FINAL_SEND_INTERVAL,
)
from soniox_client import get_config
from audio_capture import AudioStreamer
from osc_manager import osc_manager


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
        self.input_device_id: Optional[str] = None  # 入力デバイスID
        self.output_device_id: Optional[str] = None  # 出力デバイスID
        self.osc_translation_enabled = False
        self._osc_buffer_lock = threading.Lock()
        self._osc_translation_tokens: list[dict] = []
        # External WebSocket text buffer
        self._external_ws_buffer_lock = threading.Lock()
        self._external_ws_tokens: list[dict] = []  # Final tokens
        self._external_ws_non_final_tokens: list[dict] = []  # Non-final tokens (青文字)
        self._external_ws_word_count = 0
        self._external_ws_non_final_token_count = 0
        self.external_ws_non_final_send_interval = EXTERNAL_WS_NON_FINAL_SEND_INTERVAL
        # Track the last flush state to detect new additions
        self._external_ws_last_flush_final_text = ""  # Last flushed final tokens text
        self._external_ws_last_flush_non_final_text = ""  # Last flushed non-final tokens text

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

        if translation_target_lang is not None:
            self.set_translation_target_lang(translation_target_lang)
        self._reset_osc_buffer()
        self._reset_external_ws_buffer()
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
        with self._osc_buffer_lock:
            self.osc_translation_enabled = value
            if not value:
                self._osc_translation_tokens.clear()
                osc_manager.clear_history()

    def get_osc_translation_enabled(self) -> bool:
        with self._osc_buffer_lock:
            return self.osc_translation_enabled

    def _reset_osc_buffer(self):
        with self._osc_buffer_lock:
            self._osc_translation_tokens.clear()
    
    def _reset_external_ws_buffer(self):
        with self._external_ws_buffer_lock:
            self._external_ws_tokens.clear()
            self._external_ws_word_count = 0
            self._external_ws_non_final_token_count = 0
            self._external_ws_last_flush_final_text = ""
            self._external_ws_last_flush_non_final_text = ""
    
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
            self._reset_osc_buffer()
            self._reset_external_ws_buffer()
            osc_manager.clear_history()

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

    def set_input_device(self, device_id: Optional[str]) -> Tuple[bool, str]:
        """设置输入设备ID（麦克风）"""
        with self.audio_lock:
            self.input_device_id = device_id
            streamer = self.audio_streamer

        if streamer:
            try:
                streamer.set_input_device(device_id)
                device_name = device_id if device_id else "default"
                print(f"🎤 Input device set to: {device_name}")
                return True, f"Input device set to '{device_name}'."
            except Exception as error:
                return False, str(error)

        device_name = device_id if device_id else "default"
        print(f"🎤 Input device set to: {device_name} (will apply on next session)")
        return True, f"Input device saved as '{device_name}'. The change will apply when a session is active."

    def set_output_device(self, device_id: Optional[str]) -> Tuple[bool, str]:
        """设置输出设备ID（扬声器，用于系统音频捕获）"""
        with self.audio_lock:
            self.output_device_id = device_id
            streamer = self.audio_streamer

        if streamer:
            try:
                streamer.set_output_device(device_id)
                device_name = device_id if device_id else "default"
                print(f"🔊 Output device set to: {device_name}")
                return True, f"Output device set to '{device_name}'."
            except Exception as error:
                return False, str(error)

        device_name = device_id if device_id else "default"
        print(f"🔊 Output device set to: {device_name} (will apply on next session)")
        return True, f"Output device saved as '{device_name}'. The change will apply when a session is active."

    def get_input_device(self) -> Optional[str]:
        """获取当前输入设备ID"""
        with self.audio_lock:
            return self.input_device_id

    def get_output_device(self) -> Optional[str]:
        """获取当前输出设备ID"""
        with self.audio_lock:
            return self.output_device_id

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
                input_device_id=self.input_device_id,
                output_device_id=self.output_device_id
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

    def _flush_osc_translation_segment(self):
        """将缓存的译文片段通过 OSC 发送（遵循历史拼接规则）"""
        with self._osc_buffer_lock:
            if not self.osc_translation_enabled:
                self._osc_translation_tokens.clear()
                return

            tokens = list(self._osc_translation_tokens)
            self._osc_translation_tokens.clear()

        speaker_value = "?"
        for tok in reversed(tokens):
            spk = tok.get("speaker")
            if spk is not None and spk != "":
                speaker_value = str(spk)
                break

        text = "".join([tok.get("text", "") for tok in tokens]).strip()

        if text:
            osc_manager.add_message_and_send(text, ongoing=False, speaker=speaker_value)

    def _handle_osc_final_tokens(self, final_tokens: list[dict]):
        """处理新增的 final tokens，用 <end> 断句并缓存译文"""
        if not self.get_osc_translation_enabled():
            return

        for token in final_tokens:
            if not token.get("is_final"):
                continue

            text = token.get("text") or ""
            if text == "<end>":
                self._flush_osc_translation_segment()
                continue

            if token.get("translation_status") == "translation" and text:
                with self._osc_buffer_lock:
                    self._osc_translation_tokens.append(token)
    
    def _count_words(self, text: str) -> int:
        """Count words in text (simple whitespace-based)"""
        if not text:
            return 0
        return len(text.split())
    
    def _should_flush_external_ws(self, token: dict) -> bool:
        """Check if external WebSocket buffer should be flushed based on conditions"""
        text = token.get("text") or ""
        
        # Condition 1: Line end (<end> token)
        if text == "<end>":
            return True
        
        with self._external_ws_buffer_lock:
            # Count words in current token
            word_count = self._count_words(text)
            total_words = self._external_ws_word_count + word_count
            
            # Condition 2: 20 words or more
            if total_words >= 20:
                return True
            
            # Condition 3: 10 words or more and comma appears
            if total_words >= 10 and ',' in text:
                return True
            
            # Condition 4: 2 words or more and dot appears
            if total_words >= 2 and '.' in text:
                return True
        
        return False
    
    def _flush_external_ws_segment(self):
        """Flush external WebSocket buffer and send text (final + non-final tokens)"""
        with self._external_ws_buffer_lock:
            # Combine final and non-final tokens
            all_tokens = list(self._external_ws_tokens) + list(self._external_ws_non_final_tokens)
            if not all_tokens:
                return
            
            # Save current state before clearing (for detecting new additions)
            final_text = "".join([tok.get("text", "") for tok in self._external_ws_tokens])
            non_final_text = "".join([tok.get("text", "") for tok in self._external_ws_non_final_tokens])
            
            # Clear final tokens (non-final tokens are kept for next update)
            self._external_ws_tokens.clear()
            self._external_ws_word_count = 0
            # Note: non-final tokens are kept, they will be replaced on next update
            
            # Update last flush state
            self._external_ws_last_flush_final_text = final_text
            self._external_ws_last_flush_non_final_text = non_final_text
        
        # Convert tokens to text using the same method as OSC
        text = "".join([tok.get("text", "") for tok in all_tokens]).strip()
        
        if text and self.loop:
            # Get web_server from broadcast_callback closure or pass it differently
            # For now, we'll need to access web_server through a different mechanism
            # Let's add a callback for external WS sending
            if hasattr(self, 'external_ws_send_callback') and self.external_ws_send_callback:
                asyncio.run_coroutine_threadsafe(
                    self.external_ws_send_callback(text),
                    self.loop
                )
    
    def _handle_external_ws_final_tokens(self, final_tokens: list[dict]):
        """Handle final tokens for external WebSocket sending"""
        # Check if external WS is enabled (via callback existence)
        if not hasattr(self, 'external_ws_send_callback') or not self.external_ws_send_callback:
            return
        
        has_end_token = False
        
        with self._external_ws_buffer_lock:
            self._external_ws_non_final_tokens.clear()
            self._external_ws_non_final_token_count = 0
        
        for token in final_tokens:
            if not token.get("is_final"):
                continue
            
            text = token.get("text") or ""
            
            # Check for <end> token (triggers immediate flush)
            if text == "<end>":
                has_end_token = True
                continue
            
            # Process original transcription tokens (not translations)
            # translation_status can be "original", "none", or missing
            # We want to send the original transcript, not the translation
            translation_status = token.get("translation_status")
            if translation_status != "translation" and text:
                with self._external_ws_buffer_lock:
                    # Add the final token to buffer
                    word_count = self._count_words(text)
                    self._external_ws_tokens.append(token)
                    self._external_ws_word_count += word_count
        
        # Check if we have any tokens to send or <end> token
        with self._external_ws_buffer_lock:
            if not self._external_ws_tokens and not has_end_token:
                # No tokens to send
                return
            
            # Check conditions based on word count (used to determine whether to reset word_count)
            should_reset_word_count = False
            if self._external_ws_tokens:
                if self._external_ws_word_count >= 20:
                    should_reset_word_count = True
                elif self._external_ws_word_count >= 10:
                    # Check if comma is included
                    combined_text = "".join([tok.get("text", "") for tok in self._external_ws_tokens])
                    if ',' in combined_text:
                        should_reset_word_count = True
                elif self._external_ws_word_count >= 2:
                    # Check if dot is included
                    combined_text = "".join([tok.get("text", "") for tok in self._external_ws_tokens])
                    if '.' in combined_text:
                        should_reset_word_count = True
                
                if should_reset_word_count:
                    self._external_ws_word_count = 0
        
        # Always deliver when final is confirmed (regardless of rate control)
        # If <end> token exists or final token exists, send all at once
        self._flush_external_ws_segment()
    
    def _handle_external_ws_non_final_tokens(self, non_final_tokens: list[dict]):
        """Handle non-final tokens for external WebSocket sending"""
        # Check if external WS is enabled (via callback existence)
        if not hasattr(self, 'external_ws_send_callback') or not self.external_ws_send_callback:
            return
        
        # Process original transcription tokens (not translations)
        filtered_tokens = []
        for token in non_final_tokens:
            text = token.get("text") or ""
            translation_status = token.get("translation_status")
            if translation_status != "translation" and text:
                filtered_tokens.append(token)
        
        if not filtered_tokens:
            return
        
        # Update non-final tokens buffer and increment counter
        with self._external_ws_buffer_lock:
            # Get previous non-final text for comparison
            previous_non_final_text = "".join([tok.get("text", "") for tok in self._external_ws_non_final_tokens])
            
            # Update non-final tokens buffer
            self._external_ws_non_final_tokens = filtered_tokens
            self._external_ws_non_final_token_count += 1
            
            # Get current state
            current_final_text = "".join([tok.get("text", "") for tok in self._external_ws_tokens])
            current_non_final_text = "".join([tok.get("text", "") for tok in self._external_ws_non_final_tokens])
            
            # Calculate newly added content since last flush
            # Final tokens: current - last flushed (only newly added final tokens)
            new_final_text = current_final_text[len(self._external_ws_last_flush_final_text):]
            # Non-final tokens: check if current is different from last flushed
            # If different, the new part is current - last flushed (but non-final is replaced entirely)
            # So we check if current non-final contains new punctuation compared to last flushed
            if current_non_final_text != self._external_ws_last_flush_non_final_text:
                # Non-final was updated, check if it has new punctuation
                # Compare with previous non-final to see if punctuation is new
                new_non_final_text = current_non_final_text[len(previous_non_final_text):] if previous_non_final_text else current_non_final_text
            else:
                new_non_final_text = ""
            
            # Combine final and non-final tokens for condition checking
            all_tokens = self._external_ws_tokens + self._external_ws_non_final_tokens
            combined_text = "".join([tok.get("text", "") for tok in all_tokens])
            combined_word_count = sum([self._count_words(tok.get("text", "")) for tok in all_tokens])
        
        # Check conditions
        should_flush = False
        reset_counter = False
        
        if combined_word_count >= 20:
            should_flush = True
            reset_counter = True
        elif combined_word_count >= 10:
            new_combined_text = new_final_text + new_non_final_text
            if ',' in new_combined_text:
                should_flush = True
                reset_counter = True
        elif combined_word_count >= 2:
            new_combined_text = new_final_text + new_non_final_text
            if '.' in new_combined_text:
                should_flush = True
                reset_counter = True
        if not should_flush and self._external_ws_non_final_token_count >= self.external_ws_non_final_send_interval:
            should_flush = True
            reset_counter = True
        
        # Flush if condition met
        if should_flush:
            if reset_counter:
                with self._external_ws_buffer_lock:
                    self._external_ws_non_final_token_count = 0
            self._flush_external_ws_segment()
    
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
                        has_translation = False  # 标记本次响应是否包含翻译token
                        
                        for token in res.get("tokens", []):
                            if token.get("text"):
                                if token.get("is_final"):
                                    # Final tokens累积添加
                                    all_final_tokens.append(token)
                                    # 检查是否是翻译token
                                    if token.get("translation_status") == "translation":
                                        has_translation = True
                                else:
                                    # Non-final tokens每次重置
                                    non_final_tokens.append(token)

                        # 计算新增的final tokens（增量部分）
                        new_final_tokens = all_final_tokens[self.last_sent_count:]

                        if new_final_tokens:
                            self._handle_osc_final_tokens(new_final_tokens)
                            self._handle_external_ws_final_tokens(new_final_tokens)
                        
                        # Handle non-final tokens for external WebSocket sending
                        if non_final_tokens:
                            self._handle_external_ws_non_final_tokens(non_final_tokens)
                        
                        # 将新的final tokens写入日志
                        if new_final_tokens and not self.is_paused:
                            self.logger.write_to_log(new_final_tokens)
                        
                        # 如果有新的数据，发送给前端（暂停时也显示，只是不记录）
                        if new_final_tokens or non_final_tokens:
                            asyncio.run_coroutine_threadsafe(
                                self.broadcast_callback({
                                    "type": "update",
                                    "final_tokens": new_final_tokens,  # 只发送新增的final tokens
                                    "non_final_tokens": non_final_tokens,  # 当前所有non-final tokens
                                    "has_translation": has_translation,  # 本次响应是否包含翻译
                                    "endpoint_detected": res.get("endpoint_detected", False)  # 是否检测到endpoint
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
