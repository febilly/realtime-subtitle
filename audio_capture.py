"""音频捕获模块 - 处理本机/网络音频的录制和流式传输"""
import threading
import time
from typing import Optional

import numpy as np

try:
    import soundcard as sc
except ImportError:
    sc = None

_warned_missing_soundcard = False


def _convert_float32_to_int16(channel_data: np.ndarray) -> bytes:
    """将浮点音频数据转换为int16字节流"""
    clipped = np.clip(channel_data, -1.0, 1.0)
    data_int16 = (clipped * 32767).astype(np.int16)
    return data_int16.tobytes()


class AudioStreamer:
    """音频流控制器 - 支持系统输出与麦克风之间切换"""

    def __init__(self, ws, initial_source: str = "system", sample_rate: int = 16000, chunk_size: int = 3840):
        self.ws = ws
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size

        self._stop_event = threading.Event()
        self._source_changed_event = threading.Event()
        self._source_lock = threading.Lock()

        self._current_source = initial_source
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """启动音频流线程"""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._source_changed_event.clear()

        self._thread = threading.Thread(target=self._run, name="AudioStreamer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止音频流线程"""
        self._stop_event.set()
        self._source_changed_event.set()

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)

        self._thread = None

    def set_source(self, source: str) -> bool:
        """切换音频源。返回是否发生了实际切换"""
        if source not in ("system", "microphone"):
            raise ValueError("Invalid audio source. Expect 'system' or 'microphone'.")

        with self._source_lock:
            if source == self._current_source:
                return False
            self._current_source = source

        self._source_changed_event.set()
        return True

    def get_source(self) -> str:
        """获取当前音频源"""
        with self._source_lock:
            return self._current_source

    def _run(self) -> None:
        """音频线程主循环"""
        while not self._stop_event.is_set():
            with self._source_lock:
                source = self._current_source

            recorder_ctx = self._create_recorder(source)
            if recorder_ctx is None:
                time.sleep(1.0)
                continue

            # 清除切换信号，准备开始当前音频源
            self._source_changed_event.clear()

            try:
                with recorder_ctx as recorder:
                    while not self._stop_event.is_set() and not self._source_changed_event.is_set():
                        data = recorder.record(numframes=self.chunk_size)
                        if data.size == 0:
                            continue

                        payload = _convert_float32_to_int16(data[:, 0])
                        try:
                            self.ws.send(payload)
                        except Exception as send_error:
                            print(f"Error sending audio data: {send_error}")
                            return
            except Exception as capture_error:
                print(f"Error capturing audio from {source}: {capture_error}")
                time.sleep(0.5)
                continue

    def _create_recorder(self, source: str):
        """根据音频源创建对应的recorder上下文"""
        try:
            global _warned_missing_soundcard
            if sc is None:
                if not _warned_missing_soundcard:
                    print("❌ soundcard is not installed; audio capture is unavailable in this environment")
                    print("   Install with: pip install soundcard")
                    _warned_missing_soundcard = True
                return None

            if source == "system":
                speaker = sc.default_speaker()
                if speaker is None:
                    print("⚠️  No default speaker available for system audio capture")
                    return None

                loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
                if loopback is None:
                    print("⚠️  Loopback capture is not available on this device")
                    return None

                print(f"🔊 Capturing system audio from: {speaker.name}")
                return loopback.recorder(samplerate=self.sample_rate, channels=1)

            microphone = sc.default_microphone()
            if microphone is None:
                print("⚠️  No default microphone available")
                return None

            print(f"🎤 Capturing from microphone: {microphone.name}")
            return microphone.recorder(samplerate=self.sample_rate, channels=1)

        except Exception as init_error:
            print(f"Error initializing audio source '{source}': {init_error}")
            return None
