"""Replay recorded Soniox responses through the backend segmentation, and
build anonymized regression fixtures from real recordings.

Recordings come from ``SONIOX_RESPONSE_LOG=1`` (``logs/soniox-responses/``).
They contain transcripts of real speech (videos, streams, other people in
voice chat): never commit them. Commit only fixtures written by
``make-fixture``, which replaces every letter and digit with a random one of
the same script while keeping punctuation, whitespace, token boundaries,
timing and metadata. Segmentation only looks at those, so the fixture
exercises the same code paths without carrying any spoken content.

Subcommands::

    python tools/segmentation_replay.py audit <responses.jsonl>... [--frames-dir DIR]
        Replay recordings with the current backend, check backend invariants
        and (optionally) write the frames for scripts/check-segmentation-frames.js.

    python tools/segmentation_replay.py make-fixture <responses.jsonl> --run N
            --start SEC --duration SEC --name NAME
        Cut one run of a recording into an anonymized fixture plus its
        expected backend output under tests/fixtures/segmentation-replay/.

    python tools/segmentation_replay.py update-golden [NAME...]
        Regenerate expected outputs after an INTENDED behaviour change.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import random
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "segmentation-replay")
REPLAY_SENTENCE_SESSION_ID = "replay"

# Token / response fields the backend actually reads. Everything else
# (confidence, audio processing counters, ...) is dropped from fixtures.
TOKEN_FIELDS = (
    "text", "is_final", "speaker", "language", "source_language",
    "translation_status", "start_ms", "end_ms",
)
RESPONSE_FIELDS = ("endpoint_detected", "finished")

# Interrupt repair only merges across a short filler interruption from this
# whitelist (config.SONIOX_INTERRUPT_FILLER_WHITELIST default). Keeping these
# tokens verbatim preserves that code path; they carry no content.
FILLER_TOKENS = {"uh", "um", "嗯", "啊", "うん", "はいはい", "そうそうそう", "어"}


# --------------------------------------------------------------------------
# Recordings
# --------------------------------------------------------------------------

def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a crash can truncate the last line
    return rows


def _ts(record: dict) -> float:
    if "t" in record:
        return float(record["t"])
    return datetime.fromisoformat(record["ts"]).timestamp()


def _tokens(record: dict) -> list[dict]:
    return (record.get("response") or {}).get("tokens") or []


def _final_count(records: Iterable[dict]) -> int:
    return sum(1 for r in records for t in _tokens(r) if t.get("is_final"))


def infer_stream_config(records: list[dict]) -> dict:
    """Session translation config for ONE stream.

    Newer recordings log it directly. Older ones only log the UI mode, so
    infer it from the translation tokens: none -> translation off (or
    same-language speech, which segments identically); one target language
    -> one_way; two -> two_way. A restart opens a new stream, so the config
    is constant within a stream.
    """
    for record in records:
        if record.get("translation"):
            langs = record.get("target_langs") or ["en", "zh"]
            return {
                "translation": str(record["translation"]),
                "target": str(record.get("translation_target_lang") or "zh"),
                "langs": [str(langs[0]), str(langs[1])],
            }
    if any(r.get("suppress_soniox_translation") for r in records):
        # 准确 mode: source drives segmentation; the target is irrelevant.
        return {"translation": "one_way", "target": "zh", "langs": ["en", "zh"]}
    counts: dict[str, int] = {}
    for record in records:
        for token in _tokens(record):
            if token.get("is_final") and token.get("translation_status") == "translation" and token.get("language"):
                counts[token["language"]] = counts.get(token["language"], 0) + 1
    if not counts:
        return {"translation": "none", "target": "zh", "langs": ["en", "zh"]}
    ordered = sorted(counts, key=lambda lang: -counts[lang])
    if len(ordered) >= 2:
        return {"translation": "two_way", "target": ordered[0], "langs": [ordered[0], ordered[1]]}
    return {"translation": "one_way", "target": ordered[0], "langs": ["en", "zh"]}


@dataclass
class Run:
    """Consecutive streams replayable on one session object."""
    config: dict
    records: list[dict] = field(default_factory=list)


def split_runs(records: list[dict]) -> tuple[list[Run], list[tuple[str, str, float]]]:
    """Group a recording into runs and detect double sessions.

    Rollover and silence sleep open new streams on the SAME session (state
    carries over); a restart opens a fresh session, recognisable as a config
    change. Two streams overlapping for more than a few seconds are two live
    sessions at once — such a recording is not a valid single-session replay.
    """
    by_stream: dict[str, list[dict]] = {}
    for record in records:
        by_stream.setdefault(record.get("stream_key") or record.get("stream") or "?", []).append(record)
    spans = sorted(
        (_ts(rows[0]), _ts(rows[-1]), key)
        for key, rows in by_stream.items()
        if len(rows) > 3
    )
    double_sessions = []
    for current, following in zip(spans, spans[1:]):
        overlap = min(current[1], following[1]) - following[0]
        if overlap > 5.0:
            double_sessions.append((current[2], following[2], round(overlap, 1)))

    runs: list[Run] = []
    config_of = {key: infer_stream_config(rows) for key, rows in by_stream.items()}
    current_keys: list[str] = []
    current_config: dict | None = None
    for key in by_stream:
        config = config_of[key]
        if _final_count(by_stream[key]) == 0 and current_config is not None:
            config = current_config  # no evidence: keep the running config
        if current_config is not None and config != current_config:
            runs.append(Run(current_config, []))
            runs[-1].records = [r for r in records if (r.get("stream_key") or r.get("stream")) in set(current_keys)]
            current_keys = []
        current_config = config
        current_keys.append(key)
    if current_keys and current_config is not None:
        runs.append(Run(current_config, [r for r in records if (r.get("stream_key") or r.get("stream")) in set(current_keys)]))
    return runs, double_sessions


# --------------------------------------------------------------------------
# Anonymization
# --------------------------------------------------------------------------

_LATIN_LOWER = "abcdefghijklmnopqrstuvwxyz"
_LATIN_UPPER = _LATIN_LOWER.upper()
_SCRIPT_RANGES = (
    (0x3041, 0x3096),   # hiragana
    (0x30A1, 0x30FA),   # katakana
    (0x4E00, 0x9FA5),   # CJK unified ideographs
    (0xAC00, 0xD7A3),   # hangul syllables
    (0x0430, 0x044F),   # cyrillic lower
    (0x0410, 0x042F),   # cyrillic upper
)


def _random_like(ch: str, rng: random.Random) -> str:
    if ch.isdigit():
        return str(rng.randrange(10))
    code = ord(ch)
    for low, high in _SCRIPT_RANGES:
        if low <= code <= high:
            return chr(rng.randint(low, high))
    if ch.isalpha():
        return rng.choice(_LATIN_UPPER if ch.isupper() else _LATIN_LOWER)
    return ch  # punctuation, whitespace, symbols: segmentation signal


def anonymize_text(text: str, rng: random.Random) -> str:
    """Replace letters/digits with random ones of the same script.

    Fresh randomness per occurrence (no memo): a consistent mapping would be a
    substitution cipher that frequency analysis could undo.
    """
    if not text or (text.startswith("<") and text.endswith(">")):
        return text  # internal Soniox markers (<end>, <fin>)
    core = text.strip().strip("。.,，、!！?？…~").lower()
    if core in FILLER_TOKENS:
        return text
    return "".join(_random_like(ch, rng) for ch in text)


def build_fixture_records(records: list[dict], rng: random.Random) -> list[dict]:
    """Compact, anonymized, time-rebased copies of recording records."""
    if not records:
        return []
    t0 = _ts(records[0])
    stream_ids: dict[str, str] = {}
    out = []
    for record in records:
        key = record.get("stream_key") or record.get("stream") or "?"
        stream = stream_ids.setdefault(key, f"s{len(stream_ids) + 1}")
        response: dict[str, Any] = {}
        tokens = []
        for token in _tokens(record):
            compact = {name: token[name] for name in TOKEN_FIELDS if name in token}
            if "text" in compact:
                compact["text"] = anonymize_text(str(compact["text"]), rng)
            tokens.append(compact)
        response["tokens"] = tokens
        raw_response = record.get("response") or {}
        for name in RESPONSE_FIELDS:
            if raw_response.get(name):
                response[name] = raw_response[name]
        out.append({
            "t": round(_ts(record) - t0, 3),
            "stream": stream,
            "segment_mode": record.get("segment_mode") or "punctuation",
            "translation_mode": record.get("translation_mode"),
            "llm_refine_mode": record.get("llm_refine_mode") or "off",
            "suppress_soniox_translation": bool(record.get("suppress_soniox_translation")),
            "response": response,
        })
    return out


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------

def replay(module, records: list[dict], config: dict) -> tuple[list[dict], list[str]]:
    """Feed records to a fresh SonioxSession from ``module`` (soniox_session,
    imported with stubbed I/O dependencies). Returns broadcast frames and any
    exceptions raised while processing.

    The clock follows the recording timestamps, and pairing deadlines that
    would have fired between two responses fire at their deadline, as the
    live receive-timeout path does. LLM finalize coroutines are dropped; they
    never influence segmentation.
    """
    frames: list[dict] = []

    async def broadcast(data):
        frames.append(data)

    clock = {"t": _ts(records[0]) if records else 0.0}
    real_monotonic = module.time.monotonic
    real_run_threadsafe = module.asyncio.run_coroutine_threadsafe

    def done_future():
        future = concurrent.futures.Future()
        future.set_result(None)
        return future

    def run_threadsafe(coro, _loop):
        if getattr(getattr(coro, "cr_code", None), "co_name", "") != "broadcast":
            coro.close()
            return done_future()
        asyncio.run(coro)
        return done_future()

    from unittest.mock import MagicMock

    module.time.monotonic = lambda: clock["t"]
    module.asyncio.run_coroutine_threadsafe = run_threadsafe
    errors: list[str] = []
    try:
        session = module.SonioxSession(MagicMock(), broadcast)
        session.translation = config["translation"]
        session.translation_target_lang = config["target"]
        session.target_lang_1, session.target_lang_2 = config["langs"]
        session._llm_sentence_session_id = REPLAY_SENTENCE_SESSION_ID
        session.loop = object()
        streams: dict[str, dict] = {}
        for record in records:
            now = _ts(record)
            while True:
                wait = session._pairer.seconds_until_next_deadline()
                if wait is None or clock["t"] + wait > now:
                    break
                clock["t"] += max(wait, 0.001)
                session._dispatch_paired_sentences()
            clock["t"] = now
            session._segment_mode = record.get("segment_mode") or "punctuation"
            session._llm_refine_mode = record.get("llm_refine_mode") or "off"
            session._suppress_soniox_translation = bool(record.get("suppress_soniox_translation"))
            key = record.get("stream_key") or record.get("stream") or "?"
            state = streams.setdefault(key, {"finals": [], "sent": 0})
            try:
                state["sent"], _end, _reason = session._process_soniox_response(
                    record.get("response") or {}, state["finals"], state["sent"], object()
                )
            except Exception as error:  # report, keep replaying
                errors.append(f"t={now:.3f}: {type(error).__name__}: {error}")
    finally:
        module.time.monotonic = real_monotonic
        module.asyncio.run_coroutine_threadsafe = real_run_threadsafe
    return frames, errors


def golden_frames(frames: list[dict]) -> list[dict]:
    """The segmentation-relevant part of the backend output: final tokens
    (with separators) and retractions. Non-final tokens are a passthrough of
    the input and would only bloat the expected files."""
    out = []
    for frame in frames:
        kind = frame.get("type")
        if kind == "update" and frame.get("final_tokens"):
            out.append({"type": "update", "final_tokens": frame["final_tokens"]})
        elif kind == "subtitle_retract":
            out.append({key: frame[key] for key in ("type", "sentence_id", "replacement_sentence_id") if key in frame})
    return out


def check_text_conservation(records: list[dict], frames: list[dict]) -> str | None:
    """Every finalized Soniox character reaches the frontend exactly once, in
    order. Interrupt repair re-sends a retracted sentence right after its
    separator; those clones (as many tokens as the retracted sentence had)
    are intentional duplicates. Repairs can chain (A1 merged into A2, then A2
    into A3), so the count includes earlier clones."""
    source = "".join(
        str(token.get("text") or "")
        for record in records
        for token in _tokens(record)
        if token.get("is_final") and token.get("text")
        and not (str(token["text"]).startswith("<") and str(token["text"]).endswith(">"))
    )
    delivered: list[dict] = []  # everything the frontend received, clones included
    kept: list[dict] = []       # minus interrupt-repair clones
    pending_skips: list[int] = []
    for frame in frames:
        if frame.get("type") == "subtitle_retract":
            sentence_id = frame.get("sentence_id")
            pending_skips.append(sum(1 for t in delivered if t.get("llm_sentence_id") == sentence_id))
            continue
        if frame.get("type") != "update":
            continue
        skip = 0
        for token in frame.get("final_tokens") or []:
            if token.get("is_separator"):
                if token.get("separator_type") == "interrupt_repair" and pending_skips:
                    skip = pending_skips.pop(0)
                continue
            delivered.append(token)
            if skip:
                skip -= 1
                continue
            kept.append(token)
    output = "".join(str(t.get("text") or "") for t in kept)
    if output == source:
        return None
    index = next((i for i, (a, b) in enumerate(zip(source, output)) if a != b), min(len(source), len(output)))
    return (f"final text diverges at char {index}: "
            f"soniox={source[max(0, index - 30):index + 30]!r} "
            f"frontend={output[max(0, index - 30):index + 30]!r}")


# --------------------------------------------------------------------------
# Fixture files
# --------------------------------------------------------------------------

def fixture_paths(name: str) -> tuple[str, str]:
    return (
        os.path.join(FIXTURE_DIR, f"{name}.responses.jsonl"),
        os.path.join(FIXTURE_DIR, f"{name}.expected.jsonl"),
    )


def list_fixtures() -> list[str]:
    if not os.path.isdir(FIXTURE_DIR):
        return []
    return sorted(
        name[: -len(".responses.jsonl")]
        for name in os.listdir(FIXTURE_DIR)
        if name.endswith(".responses.jsonl")
    )


def load_fixture(name: str) -> tuple[dict, list[dict]]:
    responses_path, _ = fixture_paths(name)
    rows = load_jsonl(responses_path)
    meta = rows[0]
    if meta.get("type") != "fixture_meta":
        raise ValueError(f"{responses_path}: first line must be fixture_meta")
    return meta, rows[1:]


def load_expected(name: str) -> list[dict]:
    return load_jsonl(fixture_paths(name)[1])


def write_jsonl(path: str, rows: Iterable[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_expected(module, name: str) -> list[dict]:
    meta, records = load_fixture(name)
    frames, errors = replay(module, records, meta["config"])
    if errors:
        raise RuntimeError(f"{name}: replay raised {errors[:3]}")
    problem = check_text_conservation(records, frames)
    if problem:
        raise RuntimeError(f"{name}: {problem}")
    expected = golden_frames(frames)
    write_jsonl(fixture_paths(name)[1], expected)
    return expected


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def load_backend_module():
    """Import soniox_session with its network/audio/LLM dependencies stubbed,
    using the same stubs as the backend unit tests."""
    sys.path.insert(0, REPO_ROOT)
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
    import test_soniox_session_response as stubs

    class _Patch:
        @staticmethod
        def delitem(mapping, key, raising=True):
            mapping.pop(key, None)

        @staticmethod
        def setitem(mapping, key, value):
            mapping[key] = value

    stubs._install_soniox_session_import_mocks(_Patch())
    import soniox_session
    return soniox_session


def _cmd_audit(args) -> int:
    module = load_backend_module()
    failures = 0
    for path in args.recordings:
        records = load_jsonl(path)
        runs, double_sessions = split_runs(records)
        label = os.path.basename(path)
        if double_sessions:
            print(f"{label}: DOUBLE SESSION {double_sessions} (two live streams; replay is not faithful)")
        for index, run in enumerate(runs):
            finals = _final_count(run.records)
            if finals < args.min_finals:
                continue
            frames, errors = replay(module, run.records, run.config)
            problem = check_text_conservation(run.records, frames)
            status = "ok" if not errors and not problem else "FAIL"
            failures += status != "ok"
            cfg = f"{run.config['translation']}:{run.config['target']}"
            print(f"{label} run{index} {cfg} finals={finals} frames={len(frames)} {status}")
            for error in errors[:3]:
                print(f"    error: {error}")
            if problem:
                print(f"    {problem}")
            if args.frames_dir:
                stem = os.path.splitext(label)[0]
                out = os.path.join(args.frames_dir, f"{stem}.run{index}.frames.jsonl")
                write_jsonl(out, [{"type": "replay_meta", "source": label, "run": index,
                                   "config": run.config, "double_session": bool(double_sessions)}]
                            + golden_frames(frames))
    return 1 if failures else 0


def _cmd_make_fixture(args) -> int:
    module = load_backend_module()
    records = load_jsonl(args.recording)
    runs, double_sessions = split_runs(records)
    if double_sessions:
        print(f"refusing: recording has a double session {double_sessions}", file=sys.stderr)
        return 2
    run = runs[args.run]
    start = _ts(run.records[0]) + args.start
    clip = [r for r in run.records if start <= _ts(r) < start + args.duration]
    if not clip:
        print("empty clip", file=sys.stderr)
        return 2
    rng = random.Random(secrets.randbits(64))  # seed discarded: irreversible
    meta = {
        "type": "fixture_meta",
        "name": args.name,
        "config": run.config,
        "description": args.description or "",
        "anonymized": "letters/digits randomized per occurrence; punctuation, whitespace, "
                      "token boundaries, timing and metadata kept",
    }
    responses_path, _ = fixture_paths(args.name)
    write_jsonl(responses_path, [meta] + build_fixture_records(clip, rng))
    expected = write_expected(module, args.name)
    print(f"wrote {responses_path} ({len(clip)} responses, {len(expected)} expected frames)")
    return 0


def _cmd_update_golden(args) -> int:
    module = load_backend_module()
    for name in args.names or list_fixtures():
        expected = write_expected(module, name)
        print(f"{name}: {len(expected)} expected frames")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="replay recordings and check backend invariants")
    audit.add_argument("recordings", nargs="+")
    audit.add_argument("--frames-dir", help="write replayed frames here for the JS frontend check")
    audit.add_argument("--min-finals", type=int, default=20)
    audit.set_defaults(func=_cmd_audit)

    make = sub.add_parser("make-fixture", help="cut an anonymized fixture from a recording")
    make.add_argument("recording")
    make.add_argument("--run", type=int, default=0)
    make.add_argument("--start", type=float, default=0.0, help="seconds from run start")
    make.add_argument("--duration", type=float, default=90.0)
    make.add_argument("--name", required=True)
    make.add_argument("--description")
    make.set_defaults(func=_cmd_make_fixture)

    update = sub.add_parser("update-golden", help="regenerate expected outputs")
    update.add_argument("names", nargs="*")
    update.set_defaults(func=_cmd_update_golden)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
