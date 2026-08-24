"""Real-model smoke test for continuous-speech semantic audio cuts.

The input WAV is silence-trimmed, duplicated, and joined with a 20 ms
crossfade.  The known join is then used to inspect the recognizer's semantic
commit, cut point, replay handling, and final/partial ordering.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config
from local_inference.recognizer import LocalQwenRecognizer


def load_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        raise ValueError(f"Expected PCM16 WAV, got {sample_width * 8}-bit")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if sample_rate != 16000:
        source_positions = np.arange(audio.size, dtype=np.float64)
        target_positions = (
            np.arange(round(audio.size * 16000 / sample_rate)) * sample_rate / 16000
        )
        audio = np.interp(target_positions, source_positions, audio).astype(np.float32)
    return audio


def build_no_pause_pair(audio: np.ndarray) -> tuple[np.ndarray, float]:
    active = np.flatnonzero(np.abs(audio) >= 0.006)
    if active.size:
        padding = int(0.04 * 16000)
        start = max(0, int(active[0]) - padding)
        end = min(audio.size, int(active[-1]) + padding + 1)
        audio = audio[start:end]
    crossfade = min(int(0.02 * 16000), audio.size // 4)
    if crossfade <= 0:
        return np.concatenate([audio, audio]), audio.size / 16000
    fade_out = np.linspace(1.0, 0.0, crossfade, dtype=np.float32)
    seam = audio[-crossfade:] * fade_out + audio[:crossfade] * (1.0 - fade_out)
    paired = np.concatenate([audio[:-crossfade], seam, audio[crossfade:]])
    return paired, (audio.size - crossfade) / 16000


def lexical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace()
        and not unicodedata.category(char).startswith("P")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--language", default="auto")
    parser.add_argument(
        "--pace",
        type=float,
        default=0.24,
        help="Wall delay per 240 ms audio chunk; 0.24 approximates realtime feed",
    )
    parser.add_argument("--scan-interval", type=float, default=1.0)
    parser.add_argument("--confirm-interval", type=float, default=0.35)
    parser.add_argument(
        "--max-join-error-ms",
        type=float,
        default=350.0,
        help=(
            "Fail when the first located boundary is farther from the known "
            "single-sentence join than this tolerance"
        ),
    )
    parser.add_argument("--min-trimmed-fraction", type=float, default=0.30)
    parser.add_argument("--sidecar", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config.VAD_ENABLED = False
    config.LOCAL_VAD_MODE = "disabled"
    config.LOCAL_SEMANTIC_BOUNDARY_ENABLED = True
    config.LOCAL_SEMANTIC_BOUNDARY_SCAN_INTERVAL = max(0.5, args.scan_interval)
    config.LOCAL_SEMANTIC_BOUNDARY_CONFIRM_INTERVAL = max(
        0.2, args.confirm_interval
    )
    config.LOCAL_SEMANTIC_BOUNDARY_SCOUT_ENABLED = bool(args.sidecar)

    audio, seam_seconds = build_no_pause_pair(load_wav_16k(args.wav))
    started = time.perf_counter()
    events: list[dict] = []
    errors: list[str] = []
    semantic_commit = threading.Event()
    progress = {"samples": 0}

    def on_result(text: str, is_final: bool, raw: dict | None) -> None:
        event = {
            "wall_seconds": round(time.perf_counter() - started, 3),
            "fed_audio_seconds": round(progress["samples"] / 16000, 3),
            "is_final": is_final,
            "text": text,
            "semantic_boundary": (raw or {}).get("semantic_boundary"),
        }
        events.append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)
        if event["semantic_boundary"]:
            semantic_commit.set()

    recognizer = LocalQwenRecognizer(
        on_result,
        lambda error: errors.append(repr(error)),
        source_language=args.language,
    )
    recognizer.start()
    try:
        for offset in range(0, audio.size, 3840):
            chunk = audio[offset : offset + 3840]
            pcm = np.clip(chunk * 32768.0, -32768, 32767).astype(np.int16)
            progress["samples"] = min(audio.size, offset + chunk.size)
            recognizer.send(pcm.tobytes())
            time.sleep(max(0.0, args.pace))
        semantic_commit.wait(timeout=5.0)
    finally:
        recognizer.stop()

    stats = dict(recognizer._semantic_stats)
    failures: list[str] = []
    if errors:
        failures.append("recognizer reported errors")
    if stats["commits"] != 1:
        failures.append("single-sentence pair must produce exactly one semantic commit")
    if stats["trimmed_samples"] <= 0:
        failures.append("no confirmed PCM prefix was trimmed")
    if stats["locator_failures"] != 0:
        failures.append("semantic locator reported failures")
    trimmed_fraction = stats["trimmed_samples"] / max(1, audio.size)
    if trimmed_fraction < max(0.0, args.min_trimmed_fraction):
        failures.append("trimmed fraction was below the configured minimum")
    if args.sidecar and stats["scout_cuts"] != 1:
        failures.append("--sidecar was requested but did not authorize the cut")

    semantic_indices = [
        index for index, event in enumerate(events) if event["semantic_boundary"]
    ]
    boundary_error_ms: float | None = None
    if len(semantic_indices) != 1:
        failures.append("expected exactly one event with semantic boundary metadata")
    if not semantic_indices:
        semantic_index = None
    else:
        semantic_index = semantic_indices[0]
        semantic_event = events[semantic_index]
        if not semantic_event["is_final"]:
            failures.append("semantic commit was not emitted as a final prefix")
        boundary_seconds = float(
            semantic_event["semantic_boundary"]["boundary_seconds"]
        )
        boundary_error_ms = round((boundary_seconds - seam_seconds) * 1000.0, 1)
        if abs(boundary_error_ms) > max(0.0, args.max_join_error_ms):
            failures.append(
                "located boundary exceeded the configured known-join tolerance"
            )
        trimmed_seconds = float(
            semantic_event["semantic_boundary"]["trimmed_seconds"]
        )
        if trimmed_seconds > seam_seconds + 0.150:
            failures.append("physical trim crossed the gold join by more than 150 ms")
        later_final_indices = [
            index
            for index in range(semantic_index + 1, len(events))
            if events[index]["is_final"]
        ]
        if not later_final_indices:
            failures.append("no suffix final was emitted after the semantic prefix")
        else:
            suffix_final_index = later_final_indices[-1]
            if not any(
                not events[index]["is_final"]
                for index in range(semantic_index + 1, suffix_final_index)
            ):
                failures.append("no suffix partial appeared between the two finals")
            if lexical_text(semantic_event["text"]) != lexical_text(
                events[suffix_final_index]["text"]
            ):
                failures.append("duplicated single sentence changed across the two finals")

    final_count = sum(1 for event in events if event["is_final"])
    if final_count != 2:
        failures.append("expected exactly two finals for the duplicated single sentence")

    passed = not failures
    print(
        json.dumps(
            {
                "audio_seconds": round(audio.size / 16000, 3),
                "known_join_seconds": round(seam_seconds, 3),
                "semantic_stats": stats,
                "trimmed_fraction": round(trimmed_fraction, 4),
                "boundary_error_ms": boundary_error_ms,
                "event_count": len(events),
                "final_count": final_count,
                "errors": errors,
                "passed": passed,
                "failures": failures,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
