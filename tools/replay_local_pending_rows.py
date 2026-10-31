"""Replay a recording through the local Qwen + Hy-MT subtitle path and measure pending-row churn.

The same audio is fed at real-time pace through ``LocalQwenRecognizer`` and ``LocalSubtitlePipeline``,
and every subtitle frame is recorded, so two settings can be compared on identical speech:

    python tools/replay_local_pending_rows.py run talk.wav --start 600 --duration 240 --mt --out new.json
    python tools/replay_local_pending_rows.py run talk.wav --start 600 --duration 240 --mt --legacy --out old.json
    python tools/replay_local_pending_rows.py reference talk.wav --start 600 --duration 240 --out ref.txt
    python tools/replay_local_pending_rows.py compare old.json new.json --reference ref.txt

The WAV must be 16 kHz mono PCM16 (``ffmpeg -i in.opus -ac 1 -ar 16000 -c:a pcm_s16le out.wav``).
``--legacy`` restores the decoding and commit settings that preceded text-level commits.
``reference`` transcribes ~20 s chunks offline; the error rates against it show dropped or duplicated
speech, not absolute accuracy. Nothing else on the GPU should run during a measurement.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
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

import config  # noqa: E402

LEGACY_SETTINGS = {
    "LOCAL_QWEN_ASR_TEMPERATURE": 0.4,
    "LOCAL_QWEN_ASR_DRAFT_BIAS": 0.0,
    "LOCAL_SEMANTIC_BOUNDARY_LEXICAL_AGREEMENT": False,
    "LOCAL_SEMANTIC_COMMIT_MODE": "audio",
}
CHUNK_SAMPLES = 3840  # AudioStreamer's default block


def load_wav(path: Path, start: float, duration: float) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected 16 kHz mono PCM16")
        wav.setpos(int(start * 16000))
        frames = wav.readframes(int(duration * 16000))
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _coerce(value: str):
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    for kind in (int, float):
        try:
            return kind(value)
        except ValueError:
            pass
    return value


def run(args: argparse.Namespace) -> None:
    overrides = dict(LEGACY_SETTINGS) if args.legacy else {}
    for item in args.set:
        key, _, value = item.partition("=")
        overrides[key] = _coerce(value)
    for key, value in overrides.items():
        setattr(config, key, value)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        handlers=[logging.FileHandler(args.out.with_suffix(".log"), "w", "utf-8")],
    )
    from local_inference.recognizer import LocalQwenRecognizer
    from local_inference.subtitle_pipeline import LocalSubtitlePipeline

    audio = load_wav(args.wav, args.start, args.duration)
    lock = threading.Lock()
    t0: list[float] = []
    frames: list[dict] = []
    finals: list[dict] = []
    requests: list[dict] = []
    errors: list[str] = []

    def now() -> float:
        return time.monotonic() - t0[0] if t0 else 0.0

    def publish(frame: dict) -> None:
        with lock:
            frames.append({
                "t": round(now(), 3),
                "segments": [
                    {key: row[key] for key in ("id", "source", "translation", "is_final", "order")}
                    for row in frame.get("local_segments", [])
                ],
                "removed": list(frame.get("local_removed_segment_ids", [])),
            })

    def on_final(row: dict) -> None:
        with lock:
            finals.append({"t": round(now(), 3), "source": row["source"], "translation": row["translation"]})

    translator_factory = None
    warm = None
    if args.mt:
        from streaming_translation import HyMT2API

        def translator_factory():
            translator = HyMT2API(backend="local", local_device=config.LOCAL_TRANSLATION_DEVICE)
            translator.load_local_engine()
            return translator

        warm = translator_factory()  # keep the shared model resident between rows
    pipeline = LocalSubtitlePipeline(translator_factory, publish, on_final, lambda e: errors.append(str(e)))

    def on_result(text: str, is_final: bool, raw: dict | None) -> None:
        raw = raw or {}
        source_language = config.normalize_language_code(raw.get("language")) or "en"
        pipeline.update(
            text, is_final, source_language, args.target_language, translation_enabled=args.mt,
            semantic_suffix=raw.get("semantic_suffix_text") if is_final else None,
        )

    recognizer = LocalQwenRecognizer(on_result, lambda e: errors.append(str(e)), source_language="auto")
    recognizer.start()
    engine = recognizer._engine
    transcribe = engine.transcribe

    def traced(chunk, **kwargs):
        started = time.monotonic()
        result = transcribe(chunk, **kwargs)
        with lock:
            requests.append({
                "t": round(now(), 3), "audio": round(len(chunk) / 16000, 2),
                "ms": round((time.monotonic() - started) * 1000),
                "final": bool(kwargs.get("update_context", True)),
                "text": (result or {}).get("text", ""),
            })
        return result

    engine.transcribe = traced
    transcribe(np.zeros(16000, dtype=np.float32), update_context=False)  # warm the GPU path
    engine.reset_draft()

    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    t0.append(time.monotonic())
    for offset in range(0, len(pcm), CHUNK_SAMPLES):
        recognizer.send(pcm[offset:offset + CHUNK_SAMPLES].tobytes())
        delay = t0[0] + (offset + CHUNK_SAMPLES) / 16000 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    fed = now()
    recognizer.stop()
    pipeline.close()
    if warm is not None:
        warm.close()
    args.out.write_text(json.dumps({
        "overrides": overrides, "audio_seconds": len(audio) / 16000, "fed_seconds": fed,
        "drained_seconds": now(), "semantic_stats": dict(recognizer._semantic_stats),
        "errors": errors, "requests": requests, "frames": frames, "finals": finals,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {args.out}: {len(finals)} final rows, {len(errors)} errors")


def reference(args: argparse.Namespace) -> None:
    from local_inference.asr_qwen3 import Qwen3ASREngine

    audio = load_wav(args.wav, args.start, args.duration)
    frame = 1600
    energy = np.array([np.sqrt(np.mean(audio[i:i + frame] ** 2)) for i in range(0, len(audio) - frame, frame)])
    cuts = [0]
    while cuts[-1] + 24 * 16000 < len(audio):  # 16-24 s chunks, cut at the quietest 100 ms
        low, high = (cuts[-1] + 16 * 16000) // frame, (cuts[-1] + 24 * 16000) // frame
        cuts.append(int(low + np.argmin(energy[low:high])) * frame + frame // 2)
    cuts.append(len(audio))
    engine = Qwen3ASREngine()
    engine.set_language("auto")
    try:
        texts = [str((engine.probe_transcribe(audio[a:b]) or {}).get("text", "")) for a, b in zip(cuts, cuts[1:])]
    finally:
        engine.unload()
    args.out.write_text("\n".join(texts), encoding="utf-8")
    print(f"wrote {args.out}: {len(texts)} chunks")


def _lexical(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith("P"))


def _edit_distance(a, b) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y))
        previous = current
    return previous[-1]


def _percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else 0


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def summarize(run_data: dict, reference_text: str | None) -> dict:
    """Pending rows over time, how often already shown rows are rewritten, and row lifetimes."""
    rows: dict[str, dict] = {}
    pending_at_update, rewritten, older_rewritten = [], [], []
    punctuation_only = merges = splits = 0
    area = last_t = 0.0
    end_t = float(run_data["fed_seconds"])

    def live():
        return [row for row in rows.values() if not row["removed"]]

    for frame in run_data["frames"]:
        t = frame["t"]
        area += sum(1 for row in live() if not row["final"]) * max(0.0, min(t, end_t) - last_t)
        last_t = min(t, end_t)
        source_update = bool(frame["removed"])
        changed, created = [], []
        for segment in frame["segments"]:
            row = rows.get(segment["id"])
            if row is None:
                rows[segment["id"]] = {
                    "source": segment["source"], "final": segment["is_final"], "first": t,
                    "final_t": t if segment["is_final"] else None, "order": segment["order"], "removed": False,
                }
                created.append(segment["id"])
                source_update = True
                continue
            if segment["source"] != row["source"]:
                source_update = True
                changed.append(segment["id"])
                punctuation_only += _lexical(segment["source"]) == _lexical(row["source"])
                row["source"] = segment["source"]
            if segment["is_final"] and not row["final"]:
                row["final"], row["final_t"] = True, t
            row["order"] = segment["order"]
        for row_id in frame["removed"]:
            if row_id in rows:
                rows[row_id]["removed"] = True
            merges += 1
        if created:
            tail_order = max(row["order"] for row in live())
            splits += sum(1 for row_id in created if rows[row_id]["order"] < tail_order)
        if source_update:
            ordered = sorted(live(), key=lambda row: row["order"])
            tail = ordered[-1] if ordered else None
            pending_at_update.append(sum(1 for row in ordered if not row["final"]))
            rewritten.append(len(changed))
            older_rewritten.append(sum(1 for row_id in changed if rows[row_id] is not tail))
    area += sum(1 for row in live() if not row["final"]) * max(0.0, end_t - last_t)

    lifetimes = [row["final_t"] - row["first"] for row in live() if row["final"] and row["final_t"] is not None]
    finals = run_data["finals"]
    transcript = " ".join(final["source"].strip() for final in finals)
    summary = {
        "pending rows, time average": round(area / end_t, 2),
        "pending rows, p90 at updates": _percentile(pending_at_update, 0.9),
        "pending rows, max": max(pending_at_update or [0]),
        "rows rewritten per update": round(_mean(rewritten), 2),
        "  of them older than the tail": round(_mean(older_rewritten), 2),
        "punctuation-only rewrites": punctuation_only,
        "row merges": merges,
        "row splits inside shown text": splits,
        "seconds until a row is final, mean": round(_mean(lifetimes), 2),
        "seconds until a row is final, p90": round(_percentile(lifetimes, 0.9), 2),
        "final rows": len(finals),
        "final rows not ending a sentence": sum(
            1 for final in finals if not re.search(r"[.!?。！？…]['\"”’)」』]*\s*$", final["source"].strip())
        ),
        "ASR requests": len(run_data["requests"]),
        "ASR busy fraction": round(sum(r["ms"] for r in run_data["requests"]) / 1000 / end_t, 2),
        "largest ASR window, s": max((r["audio"] for r in run_data["requests"]), default=0),
        "errors": len(run_data["errors"]),
    }
    if reference_text is not None:
        ref, hyp = list(_lexical(reference_text)), list(_lexical(transcript))
        summary["character error vs reference"] = round(_edit_distance(hyp, ref) / max(1, len(ref)), 4)
        summary["characters, final / reference"] = f"{len(hyp)} / {len(ref)}"
    return summary


def compare(args: argparse.Namespace) -> None:
    reference_text = args.reference.read_text(encoding="utf-8") if args.reference else None
    results = {path.stem: summarize(json.loads(path.read_text(encoding="utf-8")), reference_text)
               for path in args.runs}
    keys = list(next(iter(results.values())))
    width = max(map(len, keys))
    print(" " * width, *(f"{name[:16]:>16}" for name in results))
    for key in keys:
        print(f"{key:<{width}}", *(f"{str(result[key])[:16]:>16}" for result in results.values()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "reference"):
        command = commands.add_parser(name)
        command.add_argument("wav", type=Path)
        command.add_argument("--start", type=float, default=0.0)
        command.add_argument("--duration", type=float, default=240.0)
        command.add_argument("--out", type=Path, required=True)
    run_parser = commands.choices["run"]
    run_parser.add_argument("--mt", action="store_true", help="also run Hy-MT2 translation, as in the app")
    run_parser.add_argument("--legacy", action="store_true", help="the settings before text-level commits")
    run_parser.add_argument("--set", action="append", default=[], metavar="CONFIG_NAME=VALUE")
    run_parser.add_argument("--target-language", default="zh")
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("runs", type=Path, nargs="+")
    compare_parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    {"run": run, "reference": reference, "compare": compare}[args.command](args)


if __name__ == "__main__":
    main()
