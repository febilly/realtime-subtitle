"""Optional Nemotron timestamp sidecar for conservative audio cuts.

The application's primary recognizer may revise its text without exposing word
times.  ``NemotronBoundaryScout`` can consume the same float32 PCM stream with
the sherpa-onnx Nemotron export and retain a small immutable result snapshot.
It deliberately owns a separate stream: callers may reset it with replayed
overlap audio without changing the primary recognizer.

The alignment helper in this module is text-model agnostic.  It only accepts a
confirmed prefix when its normalized lexical content matches scout tokens from
the very beginning and the scout has already observed another lexical token.
The returned cut is the timestamp of that next token, which avoids guessing a
word end from an RNN-T emission timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
import os
from pathlib import Path
import re
import threading
import unicodedata
from typing import Any

import numpy as np


SAMPLE_RATE = 16_000
_MODEL_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
)
_TOKEN_SPACE_MARKERS = ("\u2581", "\u0120")  # SentencePiece and byte-BPE spaces.
_LANGUAGE_CONTROL_TOKEN_RE = re.compile(
    r"^<\|?(?P<base>[a-z]{2,3})(?:-[a-z0-9]{2,8})?\|?>$", re.IGNORECASE
)
_LANGUAGE_CODES = frozenset(
    {
        "ar", "bg", "ca", "cs", "da", "de", "el", "en", "es", "et",
        "fa", "fi", "fr", "he", "hi", "hr", "hu", "id", "it", "ja",
        "ko", "lt", "lv", "ms", "nl", "no", "pl", "pt", "ro", "ru",
        "sk", "sl", "sr", "sv", "th", "tr", "uk", "ur", "vi", "zh",
        "yue",
    }
)


class BoundaryScoutUnavailableError(RuntimeError):
    """Raised when the optional timestamp sidecar cannot operate safely."""


@dataclass(frozen=True, slots=True)
class ScoutSnapshot:
    """One sherpa result, with timestamps relative to ``start_time``.

    ``timestamps`` and ``tokens`` are parallel.  ``start_time`` is the global
    audio time corresponding to timestamp zero in the current scout stream.
    """

    text: str
    tokens: tuple[str, ...]
    timestamps: tuple[float, ...]
    start_time: float


@dataclass(frozen=True, slots=True)
class PrefixCutAlignment:
    """An exact normalized prefix alignment with observed right context."""

    cut_seconds: float
    confidence: float
    evidence: str
    matched_lexical_tokens: int
    matched_scout_token_index: int
    next_scout_token_index: int
    next_token: str


def _resolve_model_dir(model_dir: str | os.PathLike[str]) -> Path:
    raw = os.path.expandvars(os.path.expanduser(os.fspath(model_dir)))
    path = Path(raw).resolve()
    missing = [name for name in _MODEL_FILES if not (path / name).is_file()]
    if missing:
        raise BoundaryScoutUnavailableError(
            "Nemotron boundary scout model is incomplete at "
            f"{path}. Missing: {', '.join(missing)}."
        )
    return path


def _coerce_audio(audio: Any, *, argument_name: str) -> np.ndarray:
    try:
        samples = np.asarray(audio, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{argument_name} must be float32-compatible PCM samples") from error
    if samples.ndim != 1:
        raise ValueError(f"{argument_name} must be a one-dimensional mono array")
    if samples.size and not np.isfinite(samples).all():
        raise ValueError(f"{argument_name} must contain only finite samples")
    return np.ascontiguousarray(samples)


class NemotronBoundaryScout:
    """Decode a parallel Nemotron stream and expose token timestamps.

    The sidecar has no hard dependency at module-import time.  Construction
    raises :class:`BoundaryScoutUnavailableError` with an actionable message if
    model files, sherpa-onnx, per-stream language support, or timestamp-bearing
    results are unavailable.
    """

    sample_rate = SAMPLE_RATE

    def __init__(
        self,
        model_dir: str | os.PathLike[str],
        num_threads: int = 1,
        language: str = "auto",
    ) -> None:
        try:
            threads = int(num_threads)
        except (TypeError, ValueError) as error:
            raise ValueError("num_threads must be a positive integer") from error
        if threads < 1:
            raise ValueError("num_threads must be a positive integer")

        self.model_dir = _resolve_model_dir(model_dir)
        self.num_threads = threads
        self.language = str(language or "auto").strip().lower() or "auto"
        self._lock = threading.RLock()
        self._timeline_samples = 0
        self._stream_start_sample = 0

        try:
            sherpa_onnx = importlib.import_module("sherpa_onnx")
        except (ImportError, ModuleNotFoundError) as error:
            raise BoundaryScoutUnavailableError(
                "Nemotron boundary scout requires sherpa-onnx; install a compatible "
                "sherpa-onnx build or leave the optional sidecar disabled."
            ) from error

        online_recognizer = getattr(sherpa_onnx, "OnlineRecognizer", None)
        factory = getattr(online_recognizer, "from_transducer", None)
        if factory is None:
            raise BoundaryScoutUnavailableError(
                "The installed sherpa-onnx build has no "
                "OnlineRecognizer.from_transducer API."
            )

        try:
            self._recognizer = factory(
                tokens=str(self.model_dir / "tokens.txt"),
                encoder=str(self.model_dir / "encoder.int8.onnx"),
                decoder=str(self.model_dir / "decoder.int8.onnx"),
                joiner=str(self.model_dir / "joiner.int8.onnx"),
                num_threads=self.num_threads,
                sample_rate=self.sample_rate,
                feature_dim=80,
                enable_endpoint_detection=False,
                decoding_method="greedy_search",
                provider="cpu",
            )
        except Exception as error:
            raise BoundaryScoutUnavailableError(
                f"Failed to load Nemotron boundary scout from {self.model_dir}: {error}"
            ) from error

        self._result_getter = self._find_result_getter()
        self._stream = self._new_stream()
        self._snapshot = ScoutSnapshot("", (), (), 0.0)

    @property
    def snapshot(self) -> ScoutSnapshot:
        """Return the most recently decoded immutable snapshot."""

        with self._lock:
            return self._snapshot

    @property
    def timeline_seconds(self) -> float:
        """Unique source audio consumed so far, excluding replayed overlap."""

        with self._lock:
            return self._timeline_samples / self.sample_rate

    def _find_result_getter(self):
        recognizer = self._recognizer
        for name in ("get_result_all", "get_result"):
            getter = getattr(recognizer, name, None)
            if callable(getter):
                return getter
        raise BoundaryScoutUnavailableError(
            "The installed sherpa-onnx recognizer exposes neither "
            "get_result_all() nor get_result()."
        )

    def _new_stream(self):
        creator = getattr(self._recognizer, "create_stream", None)
        if not callable(creator):
            raise BoundaryScoutUnavailableError(
                "The installed sherpa-onnx recognizer has no create_stream() API."
            )
        try:
            stream = creator()
        except Exception as error:
            raise BoundaryScoutUnavailableError(
                f"Failed to create a Nemotron boundary scout stream: {error}"
            ) from error

        accept_waveform = getattr(stream, "accept_waveform", None)
        if not callable(accept_waveform):
            raise BoundaryScoutUnavailableError(
                "The installed sherpa-onnx stream has no accept_waveform() API."
            )

        if self.language != "auto":
            setter = getattr(stream, "set_option", None) or getattr(stream, "SetOption", None)
            if not callable(setter):
                raise BoundaryScoutUnavailableError(
                    "This sherpa-onnx build cannot select Nemotron language per stream; "
                    f"requested language was '{self.language}'."
                )
            try:
                setter("language", self.language)
            except Exception as error:
                raise BoundaryScoutUnavailableError(
                    f"Failed to set Nemotron boundary scout language "
                    f"'{self.language}': {error}"
                ) from error
        return stream

    @staticmethod
    def _sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise BoundaryScoutUnavailableError(
                f"sherpa-onnx returned non-indexable {field_name}; "
                "a token-array result API is required."
            )
        try:
            return tuple(value)
        except TypeError as error:
            raise BoundaryScoutUnavailableError(
                f"sherpa-onnx returned invalid {field_name}."
            ) from error

    def _make_snapshot(self, result: Any) -> ScoutSnapshot:
        text = str(getattr(result, "text", "") or "")
        raw_tokens = getattr(result, "tokens_arr", None)
        if raw_tokens is None:
            raw_tokens = getattr(result, "tokens", None)
        tokens = tuple(
            str(token) for token in self._sequence(raw_tokens, field_name="tokens")
        )
        raw_timestamps = self._sequence(
            getattr(result, "timestamps", None), field_name="timestamps"
        )

        if not tokens and not text:
            timestamps: tuple[float, ...] = ()
        elif not tokens:
            raise BoundaryScoutUnavailableError(
                "Nemotron boundary scout produced text without token arrays; "
                "this sherpa-onnx result cannot be aligned safely."
            )
        elif len(raw_timestamps) != len(tokens):
            raise BoundaryScoutUnavailableError(
                "Nemotron boundary scout did not provide one timestamp per token "
                f"({len(tokens)} tokens, {len(raw_timestamps)} timestamps)."
            )
        else:
            try:
                timestamps = tuple(float(value) for value in raw_timestamps)
            except (TypeError, ValueError) as error:
                raise BoundaryScoutUnavailableError(
                    "Nemotron boundary scout returned non-numeric token timestamps."
                ) from error
            if any(not math.isfinite(value) or value < 0.0 for value in timestamps):
                raise BoundaryScoutUnavailableError(
                    "Nemotron boundary scout returned invalid token timestamps."
                )
            if any(right < left for left, right in zip(timestamps, timestamps[1:])):
                raise BoundaryScoutUnavailableError(
                    "Nemotron boundary scout token timestamps are not monotonic."
                )

        return ScoutSnapshot(
            text=text,
            tokens=tokens,
            timestamps=timestamps,
            start_time=self._stream_start_sample / self.sample_rate,
        )

    def _decode_snapshot(self) -> ScoutSnapshot:
        is_ready = getattr(self._recognizer, "is_ready", None)
        decode_stream = getattr(self._recognizer, "decode_stream", None)
        if not callable(is_ready) or not callable(decode_stream):
            raise BoundaryScoutUnavailableError(
                "The installed sherpa-onnx recognizer lacks streaming decode APIs."
            )
        try:
            while is_ready(self._stream):
                decode_stream(self._stream)
            result = self._result_getter(self._stream)
        except BoundaryScoutUnavailableError:
            raise
        except Exception as error:
            raise BoundaryScoutUnavailableError(
                f"Nemotron boundary scout decoding failed: {error}"
            ) from error
        self._snapshot = self._make_snapshot(result)
        return self._snapshot

    def _accept(self, samples: np.ndarray, *, count_as_new_audio: bool) -> ScoutSnapshot:
        if samples.size:
            try:
                self._stream.accept_waveform(self.sample_rate, samples)
            except Exception as error:
                raise BoundaryScoutUnavailableError(
                    f"Nemotron boundary scout rejected audio: {error}"
                ) from error
            if count_as_new_audio:
                self._timeline_samples += int(samples.size)
        return self._decode_snapshot()

    def feed(self, audio: Any) -> ScoutSnapshot:
        """Feed new mono float32 PCM and return the updated scout snapshot."""

        samples = _coerce_audio(audio, argument_name="audio")
        with self._lock:
            return self._accept(samples, count_as_new_audio=True)

    def reset(self, replay_audio: Any | None = None) -> ScoutSnapshot:
        """Start a new stream, optionally replaying already-counted overlap.

        ``replay_audio`` must be a suffix of audio already passed to
        :meth:`feed`; it does not advance :attr:`timeline_seconds` a second
        time.  Its length determines the new stream's global ``start_time``.
        """

        replay = (
            np.empty(0, dtype=np.float32)
            if replay_audio is None
            else _coerce_audio(replay_audio, argument_name="replay_audio")
        )
        with self._lock:
            if replay.size > self._timeline_samples:
                raise ValueError(
                    "replay_audio is longer than the source audio already fed to the scout"
                )
            new_start_sample = self._timeline_samples - int(replay.size)
            stream = self._new_stream()
            self._stream = stream
            self._stream_start_sample = new_start_sample
            self._snapshot = ScoutSnapshot(
                "", (), (), self._stream_start_sample / self.sample_rate
            )
            return self._accept(replay, count_as_new_audio=False)


def _is_language_control_token(value: str) -> bool:
    """Recognize only explicit language tags, not arbitrary unknown tokens.

    Treating every ``<...>`` token as ignorable could align through ``<unk>``
    and create a false cut.  Nemotron's emitted leading language tags use the
    narrow forms accepted here, such as ``<en>`` and ``<|zh|>``.
    """

    match = _LANGUAGE_CONTROL_TOKEN_RE.fullmatch(value.strip())
    return bool(match and match.group("base").lower() in _LANGUAGE_CODES)


def _normalize_lexical(value: str, *, token: bool = False) -> str:
    if token and _is_language_control_token(value):
        return ""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    if token:
        for marker in _TOKEN_SPACE_MARKERS:
            normalized = normalized.replace(marker, " ")
    return "".join(
        char
        for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def align_confirmed_prefix_to_scout(
    confirmed_prefix: str | None,
    snapshot: ScoutSnapshot,
) -> PrefixCutAlignment | None:
    """Map a confirmed text prefix to the next scout token timestamp.

    Punctuation, whitespace, and case are ignored.  Matching never scans for a
    later substring: the first lexical scout token must begin the confirmed
    prefix, and the prefix must end exactly at a scout lexical-token boundary.
    A following lexical token and a complete parallel timestamp array are both
    required; all other cases return ``None``.
    """

    target = _normalize_lexical(str(confirmed_prefix or ""))
    if not target or len(snapshot.tokens) != len(snapshot.timestamps):
        return None

    lexical: list[tuple[int, str, str]] = []
    for token_index, token_value in enumerate(snapshot.tokens):
        normalized = _normalize_lexical(token_value, token=True)
        if normalized:
            lexical.append((token_index, token_value, normalized))
    if len(lexical) < 2:
        return None

    accumulated = ""
    for lexical_index, (token_index, _token_value, normalized) in enumerate(lexical):
        candidate = accumulated + normalized
        if not target.startswith(candidate):
            return None
        accumulated = candidate
        if accumulated != target:
            continue
        if lexical_index + 1 >= len(lexical):
            return None

        next_token_index, next_token, _ = lexical[lexical_index + 1]
        try:
            relative_cut = float(snapshot.timestamps[next_token_index])
            absolute_cut = float(snapshot.start_time) + relative_cut
        except (TypeError, ValueError, IndexError):
            return None
        if not math.isfinite(relative_cut) or relative_cut < 0.0:
            return None
        if not math.isfinite(absolute_cut) or absolute_cut < 0.0:
            return None

        return PrefixCutAlignment(
            cut_seconds=absolute_cut,
            confidence=1.0,
            evidence="normalized_exact_prefix_with_next_lexical_token",
            matched_lexical_tokens=lexical_index + 1,
            matched_scout_token_index=token_index,
            next_scout_token_index=next_token_index,
            next_token=next_token,
        )

    return None


__all__ = [
    "BoundaryScoutUnavailableError",
    "NemotronBoundaryScout",
    "PrefixCutAlignment",
    "ScoutSnapshot",
    "align_confirmed_prefix_to_scout",
]
