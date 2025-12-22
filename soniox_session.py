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
)
from soniox_client import get_config
from audio_capture import AudioStreamer, TwitchAudioStreamer
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
        self.sample_rate = 16000
        self.chunk_size = 3840
        self.audio_source = "twitch" if USE_TWITCH_AUDIO_STREAM else "system"
        self.audio_streamer: Optional[object] = None
        self.audio_lock = threading.Lock()
        self.osc_translation_enabled = False
        self._osc_buffer_lock = threading.Lock()
        self._osc_translation_tokens: list[dict] = []
    
    def start(self, api_key: Optional[str], audio_format: str, translation: str, loop: asyncio.AbstractEventLoop):
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
        self._reset_osc_buffer()
        osc_manager.clear_history()
        
        # 初始化日志文件（如果还没有创建）
        if self.logger.log_file is None:
            self.logger.init_log_file()
        
        self.thread = threading.Thread(
            target=self._run_session,
            args=(api_key, audio_format, translation, loop),
            daemon=True
        )
        self.thread.start()
        return True
    
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
    
    def resume(self, api_key: Optional[str] = None, audio_format: Optional[str] = None,
               translation: Optional[str] = None, loop: Optional[asyncio.AbstractEventLoop] = None):
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

        if not all([self.api_key, self.audio_format, self.translation, self.loop]):
            print("❌ Cannot resume: missing session configuration")
            return False

        started = self.start(self.api_key, self.audio_format, self.translation, self.loop)
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

    def _start_audio_streamer(self, ws) -> None:
        with self.audio_lock:
            existing_streamer = self.audio_streamer
            self.audio_streamer = None

        if existing_streamer:
            existing_streamer.stop()

        if USE_TWITCH_AUDIO_STREAM:
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
    
    def _run_session(self, api_key: str, audio_format: str, translation: str, loop: asyncio.AbstractEventLoop):
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

        config = get_config(api_key, audio_format, translation)

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
