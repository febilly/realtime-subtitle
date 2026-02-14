"""音频捕获模块 - 处理本机/网络音频的录制和流式传输"""
import threading
import time
import queue
import warnings
from collections import deque
from typing import Optional

import numpy as np

try:
    import soundcard as sc
except ImportError:
    sc = None

if sc is not None:
    warnings.filterwarnings(
        "once",
        message=r"data discontinuity in recording",
        category=Warning,
    )

_warned_missing_soundcard = False


def _convert_float32_to_int16(channel_data: np.ndarray) -> bytes:
    """将浮点音频数据转换为int16字节流"""
    clipped = np.clip(channel_data, -1.0, 1.0)
    data_int16 = (clipped * 32767).astype(np.int16)
    return data_int16.tobytes()


class AudioStreamer:
    """音频流控制器 - 支持系统输出、麦克风与混合模式"""

    def __init__(self, ws, initial_source: str = "system", sample_rate: int = 16000, chunk_size: int = 3840):
        self.ws = ws
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.mix_capture_frames = max(320, min(self.chunk_size, 960))

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
        if source not in ("system", "microphone", "mix"):
            raise ValueError("Invalid audio source. Expect 'system', 'microphone' or 'mix'.")

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

            # 清除切换信号，准备开始当前音频源
            self._source_changed_event.clear()

            if source == "mix":
                self._run_mix_mode()
            else:
                self._run_single_source_mode(source)

    def _run_single_source_mode(self, source: str) -> None:
        recorder_ctx = self._create_recorder(source)
        if recorder_ctx is None:
            time.sleep(1.0)
            return

        try:
            with recorder_ctx as recorder:
                while not self._stop_event.is_set() and not self._source_changed_event.is_set():
                    data = recorder.record(numframes=self.chunk_size)
                    channel_data = self._extract_mono_channel(data)
                    if channel_data.size == 0:
                        continue

                    normalized = self._resample_to_chunk(channel_data, self.chunk_size)
                    payload = _convert_float32_to_int16(normalized)
                    try:
                        self.ws.send(payload)
                    except Exception as send_error:
                        print(f"Error sending audio data: {send_error}")
                        self._stop_event.set()
                        return
        except Exception as capture_error:
            print(f"Error capturing audio from {source}: {capture_error}")
            time.sleep(0.5)

    def _run_mix_mode(self) -> None:
        system_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=4)
        microphone_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=4)
        local_stop_event = threading.Event()

        system_thread = threading.Thread(
            target=self._capture_worker,
            args=("system", system_queue, local_stop_event, self.mix_capture_frames),
            name="AudioCaptureSystem",
            daemon=True,
        )
        microphone_thread = threading.Thread(
            target=self._capture_worker,
            args=("microphone", microphone_queue, local_stop_event, self.mix_capture_frames),
            name="AudioCaptureMicrophone",
            daemon=True,
        )
        mixer_thread = threading.Thread(
            target=self._mix_and_send_worker,
            args=(system_queue, microphone_queue, local_stop_event),
            name="AudioMixSender",
            daemon=True,
        )

        print("🎛️  Capturing mixed audio: system + microphone")

        system_thread.start()
        microphone_thread.start()
        mixer_thread.start()

        while not self._stop_event.is_set() and not self._source_changed_event.is_set():
            if not mixer_thread.is_alive():
                break
            time.sleep(0.05)

        local_stop_event.set()

        for thread in (system_thread, microphone_thread, mixer_thread):
            if thread.is_alive():
                thread.join(timeout=1.0)

    def _capture_worker(
        self,
        source: str,
        out_queue: "queue.Queue[np.ndarray]",
        local_stop_event: threading.Event,
        capture_frames: int,
    ) -> None:
        recorder_ctx = self._create_recorder(source)
        if recorder_ctx is None:
            local_stop_event.set()
            return

        try:
            with recorder_ctx as recorder:
                while not self._stop_event.is_set() and not self._source_changed_event.is_set() and not local_stop_event.is_set():
                    data = recorder.record(numframes=max(1, int(capture_frames)))
                    channel_data = self._extract_mono_channel(data)
                    if channel_data.size == 0:
                        continue

                    payload = channel_data.astype(np.float32, copy=False)
                    try:
                        out_queue.put_nowait(payload)
                    except queue.Full:
                        try:
                            out_queue.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            out_queue.put_nowait(payload)
                        except queue.Full:
                            pass
        except Exception as capture_error:
            print(f"Error capturing audio from {source}: {capture_error}")
            local_stop_event.set()

    def _mix_and_send_worker(
        self,
        system_queue: "queue.Queue[np.ndarray]",
        microphone_queue: "queue.Queue[np.ndarray]",
        local_stop_event: threading.Event,
    ) -> None:
        system_segments: deque[np.ndarray] = deque()
        microphone_segments: deque[np.ndarray] = deque()
        system_available = 0
        microphone_available = 0
        min_mix_frames = max(1, int(self.chunk_size))
        max_buffer_frames = min_mix_frames * 3
        max_source_skew_frames = min_mix_frames

        while not self._stop_event.is_set() and not self._source_changed_event.is_set() and not local_stop_event.is_set():
            try:
                captured_system = system_queue.get(timeout=0.02)
                if captured_system is not None and captured_system.size > 0:
                    normalized = captured_system.astype(np.float32, copy=False)
                    system_segments.append(normalized)
                    system_available += int(normalized.size)
            except queue.Empty:
                pass

            try:
                captured_microphone = microphone_queue.get(timeout=0.02)
                if captured_microphone is not None and captured_microphone.size > 0:
                    normalized = captured_microphone.astype(np.float32, copy=False)
                    microphone_segments.append(normalized)
                    microphone_available += int(normalized.size)
            except queue.Empty:
                pass

            if system_available > max_buffer_frames:
                drop_len = system_available - max_buffer_frames
                system_segments, system_available = self._drop_old_frames(system_segments, system_available, drop_len)

            if microphone_available > max_buffer_frames:
                drop_len = microphone_available - max_buffer_frames
                microphone_segments, microphone_available = self._drop_old_frames(microphone_segments, microphone_available, drop_len)

            if system_available - microphone_available > max_source_skew_frames:
                drop_len = (system_available - microphone_available) - max_source_skew_frames
                system_segments, system_available = self._drop_old_frames(system_segments, system_available, drop_len)
            elif microphone_available - system_available > max_source_skew_frames:
                drop_len = (microphone_available - system_available) - max_source_skew_frames
                microphone_segments, microphone_available = self._drop_old_frames(microphone_segments, microphone_available, drop_len)

            if system_available < min_mix_frames or microphone_available < min_mix_frames:
                continue

            mix_len = min(min_mix_frames, system_available, microphone_available)
            system_data, system_segments, system_available = self._consume_segments(system_segments, system_available, mix_len)
            microphone_data, microphone_segments, microphone_available = self._consume_segments(microphone_segments, microphone_available, mix_len)

            mixed = (system_data + microphone_data) * 0.5
            peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
            if peak > 0.98:
                mixed = mixed * (0.98 / peak)

            payload = _convert_float32_to_int16(mixed)
            try:
                self.ws.send(payload)
            except Exception as send_error:
                print(f"Error sending mixed audio data: {send_error}")
                local_stop_event.set()
                self._stop_event.set()
                return

    def _consume_segments(
        self,
        segments: "deque[np.ndarray]",
        available: int,
        length: int,
    ) -> tuple[np.ndarray, "deque[np.ndarray]", int]:
        if length <= 0:
            return np.zeros(0, dtype=np.float32), segments, available

        remaining = int(length)
        parts: list[np.ndarray] = []

        while remaining > 0 and segments:
            head = segments[0]
            head_len = int(head.size)

            if head_len <= remaining:
                parts.append(head)
                segments.popleft()
                remaining -= head_len
                available -= head_len
                continue

            parts.append(head[:remaining])
            segments[0] = head[remaining:]
            available -= remaining
            remaining = 0

        if not parts:
            return np.zeros(0, dtype=np.float32), segments, available

        if len(parts) == 1:
            return parts[0], segments, available

        return np.concatenate(parts), segments, available

    def _drop_old_frames(
        self,
        segments: "deque[np.ndarray]",
        available: int,
        length: int,
    ) -> tuple["deque[np.ndarray]", int]:
        remaining = max(0, int(length))
        if remaining <= 0:
            return segments, available

        while remaining > 0 and segments:
            head = segments[0]
            head_len = int(head.size)
            if head_len <= remaining:
                segments.popleft()
                available -= head_len
                remaining -= head_len
                continue

            segments[0] = head[remaining:]
            available -= remaining
            remaining = 0

        return segments, max(0, available)

    def _extract_mono_channel(self, data: np.ndarray) -> np.ndarray:
        if data is None:
            return np.zeros(0, dtype=np.float32)

        arr = np.asarray(data, dtype=np.float32)
        if arr.size == 0:
            return np.zeros(0, dtype=np.float32)

        if arr.ndim == 1:
            return arr

        return arr[:, 0]

    def _resample_to_chunk(self, data: Optional[np.ndarray], target_length: int) -> np.ndarray:
        if data is None:
            return np.zeros(target_length, dtype=np.float32)

        arr = np.asarray(data, dtype=np.float32)
        current_length = int(arr.shape[0])

        if current_length == target_length:
            return arr

        if current_length <= 1:
            value = float(arr[0]) if current_length == 1 else 0.0
            return np.full(target_length, value, dtype=np.float32)

        source_axis = np.linspace(0.0, 1.0, num=current_length, dtype=np.float32)
        target_axis = np.linspace(0.0, 1.0, num=target_length, dtype=np.float32)
        return np.interp(target_axis, source_axis, arr).astype(np.float32)

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
