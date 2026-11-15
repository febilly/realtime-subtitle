from __future__ import annotations

import collections
import logging
import math
import numpy as np

from .model_manager import firered_vad_cmvn_path, firered_vad_onnx_path

logger = logging.getLogger(__name__)

# Official FireRedVAD streaming ONNX export (Stream-VAD).
_FIRERED_SAMPLE_RATE = 16000
_FIRERED_FEATURE_DIM = 80
_FIRERED_NUM_CACHES = 8
_FIRERED_CACHE_DIM = 128
_FIRERED_CACHE_LENGTH = 19


def _load_kaldi_cmvn(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Read the Kaldi binary CMVN matrix shipped with FireRedVAD."""
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 15 or data[:2] != b"\x00B" or data[2:5] not in (b"DM ", b"FM "):
        raise ValueError(f"Unsupported Kaldi CMVN file: {path}")

    dtype = np.dtype("<f8") if data[2:5] == b"DM " else np.dtype("<f4")
    offset = 5
    dimensions = []
    for _ in range(2):
        if data[offset] in (0x20, 0x04):
            offset += 1
        dimensions.append(int.from_bytes(data[offset : offset + 4], "little", signed=True))
        offset += 4
    rows, columns = dimensions
    stats = np.frombuffer(data, dtype=dtype, count=rows * columns, offset=offset)
    stats = stats.reshape(rows, columns).astype(np.float64, copy=False)

    count = max(1.0, float(stats[0, -1]))
    means = stats[0, :-1] / count
    variance = stats[1, :-1] / count - means * means
    inverse_std = 1.0 / np.sqrt(np.maximum(variance, 1e-20))
    return means.astype(np.float32), inverse_std.astype(np.float32)


class FireRedStreamVAD:
    """FireRedVAD streaming VAD via the official ONNX model (no PyTorch).

    The model consumes 10 ms Kaldi fbank frames (25 ms window, 80 mel bins)
    and keeps eight DFSMN cache tensors between calls.  Audio is accepted as
    normalized float32 PCM in [-1, 1]; Kaldi fbank expects int16-scale values,
    so the samples are scaled before feature extraction.
    """

    def __init__(self, model_path: str, cmvn_path: str) -> None:
        import kaldi_native_fbank as knf
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )
        self._means, self._inverse_std = _load_kaldi_cmvn(cmvn_path)
        self._knf = knf
        options = knf.FbankOptions()
        options.frame_opts.samp_freq = _FIRERED_SAMPLE_RATE
        options.frame_opts.frame_length_ms = 25
        options.frame_opts.frame_shift_ms = 10
        options.frame_opts.dither = 0
        options.frame_opts.snip_edges = True
        options.mel_opts.num_bins = _FIRERED_FEATURE_DIM
        options.mel_opts.debug_mel = False
        self._fbank_options = options
        self.reset_states()

    def reset_states(self) -> None:
        self._fbank = self._knf.OnlineFbank(self._fbank_options)
        self._frames_read = 0
        self._caches = np.zeros(
            (
                _FIRERED_NUM_CACHES,
                1,
                _FIRERED_CACHE_DIM,
                _FIRERED_CACHE_LENGTH,
            ),
            dtype=np.float32,
        )
        self.last_probability = 0.0
        self.last_frame_probabilities = np.zeros(0, dtype=np.float32)

    def probability(self, audio_chunk: np.ndarray, sample_rate: int) -> float:
        if int(sample_rate) != _FIRERED_SAMPLE_RATE:
            raise ValueError(
                f"FireRed VAD expects 16 kHz audio, got {sample_rate}"
            )

        audio = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return self.last_probability

        pcm = np.clip(audio, -1.0, 1.0) * 32768.0
        self._fbank.accept_waveform(int(sample_rate), pcm)

        features = []
        while self._frames_read < self._fbank.num_frames_ready:
            feature = np.asarray(self._fbank.get_frame(self._frames_read), dtype=np.float32)
            self._frames_read += 1
            features.append((feature - self._means) * self._inverse_std)
        if not features:
            return self.last_probability

        batch = np.stack(features, axis=0)[None, ...].astype(np.float32, copy=False)
        probs, caches = self._session.run(
            None,
            {"feat": batch, "caches_in": self._caches},
        )
        self._caches = np.ascontiguousarray(caches, dtype=np.float32)
        values = np.asarray(probs, dtype=np.float32).reshape(-1)
        self.last_frame_probabilities = values
        self.last_probability = float(values.mean())
        return self.last_probability


class VADProcessor:
    """Voice activity detection with FireRed VAD (ONNX), energy, or disabled mode."""

    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.50,
        min_speech_duration: float = 1.0,
        chunk_duration: float = 0.032,
        pre_speech_duration: float = 0.5,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.energy_threshold = 0.02
        self.min_speech_samples = int(min_speech_duration * sample_rate)
        self._chunk_duration = chunk_duration
        self.mode = "firered"

        self._firered: FireRedStreamVAD | None = None

        self._speech_buffer: list[np.ndarray] = []
        self._confidence_history: list[float] = []
        self._speech_samples = 0
        self._is_speaking = False
        self._silence_counter = 0

        self._pre_speech_duration = max(0.0, float(pre_speech_duration))
        self._pre_speech_chunks = 0
        self._pre_buffer: collections.deque[np.ndarray] = collections.deque(maxlen=1)
        self._update_pre_speech_chunks(self._pre_speech_duration)

        self._silence_limit = self._seconds_to_chunks(0.8)
        self.last_confidence = 0.0

    def _ensure_firered(self) -> FireRedStreamVAD:
        if self._firered is not None:
            return self._firered
        model_path = firered_vad_onnx_path()
        cmvn_path = firered_vad_cmvn_path()
        if not model_path.is_file() or not cmvn_path.is_file():
            raise FileNotFoundError(
                f"FireRed VAD 模型未找到: {model_path}（随程序附带，安装可能不完整）"
            )
        self._firered = FireRedStreamVAD(str(model_path), str(cmvn_path))
        return self._firered

    def _seconds_to_chunks(self, seconds: float) -> int:
        return max(1, round(seconds / self._chunk_duration))

    def _update_pre_speech_chunks(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        chunks = int(math.ceil(seconds / self._chunk_duration)) if self._chunk_duration > 0 else 0
        if seconds > 0 and chunks <= 0:
            chunks = 1

        self._pre_speech_duration = seconds
        self._pre_speech_chunks = max(0, chunks)

        if self._pre_speech_chunks <= 0:
            self._pre_buffer = collections.deque(maxlen=1)
            self._pre_buffer.clear()
            return

        old = list(self._pre_buffer)
        self._pre_buffer = collections.deque(maxlen=self._pre_speech_chunks)
        if old:
            self._pre_buffer.extend(old[-self._pre_speech_chunks :])

    def update_settings(self, settings: dict) -> None:
        if "vad_mode" in settings:
            mode = str(settings["vad_mode"] or "").strip().lower()
            if mode not in ("firered", "energy", "disabled"):
                if mode:
                    logger.warning("Unsupported VAD mode %r, using firered", mode)
                mode = "firered"
            self.mode = mode
        if "vad_threshold" in settings:
            self.threshold = settings["vad_threshold"]
        if "energy_threshold" in settings:
            self.energy_threshold = settings["energy_threshold"]
        if "min_speech_duration" in settings:
            self.min_speech_samples = int(settings["min_speech_duration"] * self.sample_rate)
        if "silence_duration" in settings:
            self._silence_limit = self._seconds_to_chunks(float(settings["silence_duration"]))
        if "pre_speech_duration" in settings:
            self._update_pre_speech_chunks(float(settings["pre_speech_duration"]))

    def _firered_confidence(self, audio_chunk: np.ndarray) -> float:
        chunk = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        return self._ensure_firered().probability(chunk, self.sample_rate)

    def _energy_confidence(self, audio_chunk: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(audio_chunk**2)))
        return min(1.0, rms / (self.energy_threshold * 2))

    def _get_confidence(self, audio_chunk: np.ndarray) -> float:
        if self.mode == "firered":
            return self._firered_confidence(audio_chunk)
        if self.mode == "energy":
            return self._energy_confidence(audio_chunk)
        return 1.0

    def process_chunk(self, audio_chunk: np.ndarray) -> np.ndarray | None:
        confidence = self._get_confidence(audio_chunk)
        self.last_confidence = confidence

        effective_threshold = self.threshold if self.mode == "firered" else 0.5

        if confidence >= effective_threshold:
            if not self._is_speaking:
                for pre_chunk in self._pre_buffer:
                    self._speech_buffer.append(pre_chunk)
                    self._confidence_history.append(effective_threshold)
                    self._speech_samples += len(pre_chunk)
                self._pre_buffer.clear()

            self._is_speaking = True
            self._silence_counter = 0
            self._speech_buffer.append(audio_chunk)
            self._confidence_history.append(confidence)
            self._speech_samples += len(audio_chunk)
        elif self._is_speaking:
            self._silence_counter += 1
            self._speech_buffer.append(audio_chunk)
            self._confidence_history.append(confidence)
            self._speech_samples += len(audio_chunk)
        else:
            if self._pre_speech_chunks > 0:
                self._pre_buffer.append(audio_chunk)

        if self._is_speaking and self._silence_counter >= self._silence_limit:
            if self._speech_samples >= self.min_speech_samples:
                return self._flush_segment()
            logger.debug(
                "Short segment %.1fs < min %.1fs, keeping for merge",
                self._speech_samples / self.sample_rate,
                self.min_speech_samples / self.sample_rate,
            )
            self._is_speaking = False
            self._silence_counter = 0
            return None

        return None

    @property
    def is_speaking(self) -> bool:
        """当前是否正在说话（供外部 gating 逻辑读取）。"""
        return self._is_speaking

    @property
    def current_silence_duration(self) -> float:
        """当前语音段内已连续静音的时长（秒）；非说话状态返回 0.0。

        供增量识别的短停顿触发等逻辑判断"说话中出现了多长的停顿"。
        """
        if not self._is_speaking:
            return 0.0
        return self._silence_counter * self._chunk_duration

    @property
    def speech_samples(self) -> int:
        """Number of samples in the current uncommitted speech window."""
        return self._speech_samples

    def trim_prefix(self, samples_to_remove: int) -> int:
        """Drop a confirmed prefix while keeping the current VAD segment open.

        Semantic sentence commits happen without silence, so flushing the VAD
        would throw away the already-buffered beginning of the next sentence.
        This method removes only the confirmed PCM prefix and leaves the replay
        tail, confidence history and speaking state intact.

        Returns the exact number of samples removed.
        """
        requested = max(0, int(samples_to_remove))
        if requested <= 0 or not self._speech_buffer:
            return 0

        # A semantic cut always retains pre-roll.  Still keep one sample if a
        # bad locator asks to remove the whole window; losing the live segment
        # is a much worse failure mode than doing a little redundant work.
        remaining_to_remove = min(requested, max(0, self._speech_samples - 1))
        removed = 0
        new_buffer: list[np.ndarray] = []
        new_confidences: list[float] = []
        for chunk, confidence in zip(self._speech_buffer, self._confidence_history):
            if remaining_to_remove >= len(chunk):
                remaining_to_remove -= len(chunk)
                removed += len(chunk)
                continue
            if remaining_to_remove > 0:
                removed += remaining_to_remove
                chunk = chunk[remaining_to_remove:]
                remaining_to_remove = 0
            new_buffer.append(chunk)
            new_confidences.append(confidence)

        self._speech_buffer = new_buffer
        self._confidence_history = new_confidences
        self._speech_samples = sum(len(chunk) for chunk in new_buffer)
        self._is_speaking = bool(new_buffer)
        if self._is_speaking:
            effective_threshold = self.threshold if self.mode == "firered" else 0.5
            trailing_silence = 0
            for confidence in reversed(new_confidences):
                if confidence >= effective_threshold:
                    break
                trailing_silence += 1
            self._silence_counter = trailing_silence
        else:
            self._silence_counter = 0
        return removed

    def quietest_offset(self, start_sample: int, end_sample: int) -> int:
        """Chunk boundary in [start, end] that follows the least speech-like chunk.

        Used to place a trim cut in a gap between words.  Returns ``end``
        when the range holds no complete chunk.
        """
        best_offset = int(end_sample)
        best_confidence = float("inf")
        offset = 0
        for chunk, confidence in zip(self._speech_buffer, self._confidence_history):
            offset += len(chunk)
            if offset > end_sample:
                break
            if offset >= start_sample and confidence < best_confidence:
                best_confidence = confidence
                best_offset = offset
        return best_offset

    def _flush_segment(self) -> np.ndarray | None:
        if not self._speech_buffer:
            return None
        if len(self._confidence_history) >= 4:
            effective_threshold = self.threshold if self.mode == "firered" else 0.5
            voiced = sum(1 for confidence in self._confidence_history if confidence >= effective_threshold)
            density = voiced / len(self._confidence_history)
            if density < 0.25:
                logger.debug(
                    "Low speech density %.0f%%, discarding %.1fs",
                    density * 100,
                    self._speech_samples / self.sample_rate,
                )
                self._reset()
                return None
        segment = np.concatenate(self._speech_buffer)
        self._reset()
        return segment

    def _reset(self) -> None:
        self._speech_buffer = []
        self._confidence_history = []
        self._speech_samples = 0
        self._is_speaking = False
        self._silence_counter = 0

    def reset(self) -> None:
        self._reset()
        self.last_confidence = 0.0
        if self._firered is not None:
            try:
                self._firered.reset_states()
            except Exception:
                pass
        self._pre_buffer.clear()

    def peek_buffer(self) -> tuple[np.ndarray, float] | None:
        if not self._speech_buffer or not self._is_speaking:
            return None
        audio = np.concatenate(self._speech_buffer)
        return audio, self._speech_samples / self.sample_rate

    def force_flush(self) -> np.ndarray | None:
        if not self._speech_buffer:
            return None
        segment = np.concatenate(self._speech_buffer)
        self._reset()
        return segment

    def flush(self) -> np.ndarray | None:
        if self._speech_samples >= self.min_speech_samples:
            return self._flush_segment()
        self._reset()
        return None
