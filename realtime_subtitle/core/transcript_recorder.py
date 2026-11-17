"""In-memory transcript buffer, SRT/TXT formatting, and auto-save support."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

VALID_TRANSCRIPT_FORMATS = ("srt", "txt")


@dataclass
class TranscriptEntry:
    sentence_id: str
    start_ms: int
    end_ms: int
    original: str
    source_language: str = ""
    translation: str = ""


def get_default_transcript_dir() -> str:
    """Return `<Documents>/Realtime Subtitle/Transcripts` across platforms."""
    documents_dir = ""
    if os.name == "nt":
        try:
            import ctypes

            CSIDL_PERSONAL = 5
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, 0, buf) == 0:
                documents_dir = str(buf.value or "").strip()
        except Exception:
            documents_dir = ""
    if not documents_dir:
        documents_dir = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(documents_dir, "Realtime Subtitle", "Transcripts")


def format_srt_timestamp(ms: int) -> str:
    total_ms = max(0, int(ms))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_txt_timestamp(ms: int) -> str:
    total_seconds = max(0, int(ms) // 1_000)
    hours, rem = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_srt(entries: Iterable[TranscriptEntry]) -> str:
    """Render entries as SubRip (.srt) with strictly non-overlapping timestamps."""
    cues: list[str] = []
    prev_end_ms = 0
    cue_index = 1
    for entry in entries:
        orig = str(entry.original or "").strip()
        if not orig:
            continue
        start_ms = max(0, prev_end_ms, int(entry.start_ms))
        raw_end_ms = max(0, int(entry.end_ms))
        end_ms = raw_end_ms if raw_end_ms > start_ms else start_ms + 100
        prev_end_ms = end_ms

        lines = [
            str(cue_index),
            f"{format_srt_timestamp(start_ms)} --> {format_srt_timestamp(end_ms)}",
            orig,
        ]
        trans = str(entry.translation or "").strip()
        if trans:
            lines.append(trans)
        cues.append("\n".join(lines))
        cue_index += 1
    if not cues:
        return ""
    return "\n\n".join(cues) + "\n\n"


def format_txt(entries: Iterable[TranscriptEntry]) -> str:
    """Render entries as plain text with `[HH:MM:SS]` timestamps."""
    blocks: list[str] = []
    for entry in entries:
        orig = str(entry.original or "").strip()
        if not orig:
            continue
        ts = format_txt_timestamp(max(0, int(entry.start_ms)))
        block_lines = [f"[{ts}] {orig}"]
        trans = str(entry.translation or "").strip()
        if trans:
            block_lines.append(f"            {trans}")
        blocks.append("\n".join(block_lines))
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n\n"


def render_transcript(entries: Iterable[TranscriptEntry], fmt: str) -> str:
    normalized = str(fmt or "srt").strip().lower()
    if normalized == "txt":
        return format_txt(entries)
    return format_srt(entries)


def infer_format_from_path(path: str | Path, fallback: str = "srt") -> str:
    suffix = Path(path).suffix.strip().lower()
    if suffix == ".txt":
        return "txt"
    if suffix == ".srt":
        return "srt"
    normalized = str(fallback or "srt").strip().lower()
    return normalized if normalized in VALID_TRANSCRIPT_FORMATS else "srt"


def _atomic_write_utf8_bom(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".transcript-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _parse_stored_pack_enabled(stored_settings: dict | None) -> bool:
    if not isinstance(stored_settings, dict):
        return False
    raw = stored_settings.get("featurePacks.v1")
    if not isinstance(raw, str):
        return False
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            enabled = parsed.get("enabled")
            if isinstance(enabled, dict) and isinstance(enabled.get("transcript"), bool):
                return bool(enabled["transcript"])
    except Exception:
        pass
    return False


def _parse_stored_transcript_settings(stored_settings: dict | None) -> tuple[bool, str, str]:
    auto_save = False
    fmt = "srt"
    directory = ""
    if isinstance(stored_settings, dict):
        raw = stored_settings.get("transcriptSettings.v1")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("autoSave"), bool):
                        auto_save = bool(parsed["autoSave"])
                    elif isinstance(parsed.get("auto_save"), bool):
                        auto_save = bool(parsed["auto_save"])
                    raw_fmt = str(parsed.get("format") or "").strip().lower()
                    if raw_fmt in VALID_TRANSCRIPT_FORMATS:
                        fmt = raw_fmt
                    raw_dir = str(parsed.get("directory") or "").strip()
                    if raw_dir:
                        directory = raw_dir
            except Exception:
                pass
    if not directory:
        directory = get_default_transcript_dir()
    return auto_save, fmt, directory


class TranscriptRecorder:
    """Thread-safe recorder for the current recognition run's transcript."""

    def __init__(
        self,
        stored_settings: dict | None = None,
        on_status_changed: Callable[[dict], None] | None = None,
        on_auto_saved: Callable[[], None] | None = None,
        on_exported: Callable[[], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._on_status_changed = on_status_changed
        self._on_auto_saved = on_auto_saved
        self._on_exported = on_exported
        self._pack_enabled = _parse_stored_pack_enabled(stored_settings)
        auto_save, fmt, directory = _parse_stored_transcript_settings(stored_settings)
        self._auto_save = auto_save
        self._format = fmt
        self._directory = directory
        self._entries: dict[str, TranscriptEntry] = {}
        self._run_active = False
        self._run_started_wall: datetime | None = None
        self._auto_save_path: Path | None = None

    def set_status_callback(self, callback: Callable[[dict], None] | None) -> None:
        with self._lock:
            self._on_status_changed = callback

    def set_usage_callbacks(
        self,
        *,
        on_auto_saved: Callable[[], None] | None = None,
        on_exported: Callable[[], None] | None = None,
    ) -> None:
        with self._lock:
            self._on_auto_saved = on_auto_saved
            self._on_exported = on_exported

    def _emit_status(self, status: dict) -> None:
        callback = self._on_status_changed
        if callback is None:
            return
        try:
            callback(status)
        except Exception:
            logger.debug("Transcript status callback failed", exc_info=True)

    def _status_locked(self) -> dict:
        count = len(self._entries)
        return {
            "has_entries": count > 0,
            "count": count,
            "pack_enabled": self._pack_enabled,
            "run_active": self._run_active,
        }

    def get_status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def has_entries(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_entries(self) -> list[TranscriptEntry]:
        with self._lock:
            return [
                TranscriptEntry(
                    sentence_id=e.sentence_id,
                    start_ms=e.start_ms,
                    end_ms=e.end_ms,
                    original=e.original,
                    source_language=e.source_language,
                    translation=e.translation,
                )
                for e in self._entries.values()
            ]

    def get_settings(self) -> dict:
        with self._lock:
            return {
                "auto_save": self._auto_save,
                "format": self._format,
                "directory": self._directory,
            }

    def suggest_export_filename(self, fmt: str | None = None) -> str:
        with self._lock:
            ext = str(fmt or self._format or "srt").strip().lower()
            if ext not in VALID_TRANSCRIPT_FORMATS:
                ext = "srt"
            ts = (self._run_started_wall or datetime.now()).strftime("%Y%m%d-%H%M%S")
            return f"transcript-{ts}.{ext}"

    def set_pack_enabled(self, enabled: bool) -> dict:
        value = bool(enabled)
        notify = None
        with self._lock:
            changed = self._pack_enabled != value
            self._pack_enabled = value
            if changed:
                notify = self._status_locked()
        if notify is not None:
            self._emit_status(notify)
        return self.get_status()

    def is_pack_enabled(self) -> bool:
        with self._lock:
            return self._pack_enabled

    def update_settings(
        self,
        *,
        auto_save: bool | None = None,
        format: str | None = None,  # noqa: A002
        directory: str | None = None,
    ) -> dict:
        with self._lock:
            if format is not None:
                normalized_fmt = str(format).strip().lower()
                if normalized_fmt not in VALID_TRANSCRIPT_FORMATS:
                    raise ValueError(f"Unsupported transcript format: {format}")
                if normalized_fmt != self._format:
                    self._format = normalized_fmt
                    self._auto_save_path = None
            if directory is not None:
                cleaned_dir = str(directory).strip()
                resolved_dir = cleaned_dir if cleaned_dir else get_default_transcript_dir()
                if resolved_dir != self._directory:
                    self._directory = resolved_dir
                    self._auto_save_path = None
            if auto_save is not None:
                self._auto_save = bool(auto_save)
            if self._pack_enabled and self._auto_save and self._entries:
                self._write_auto_save_locked()
            return self.get_settings()

    def begin_run(self) -> None:
        notify = None
        with self._lock:
            had_entries = bool(self._entries)
            was_active = self._run_active
            self._entries.clear()
            self._run_started_wall = datetime.now()
            self._auto_save_path = None
            self._run_active = True
            if had_entries or not was_active:
                notify = self._status_locked()
        if notify is not None:
            self._emit_status(notify)

    def continue_run(self) -> None:
        notify = None
        with self._lock:
            was_active = self._run_active
            if self._run_started_wall is None:
                self._run_started_wall = datetime.now()
            self._run_active = True
            if not was_active:
                notify = self._status_locked()
        if notify is not None:
            self._emit_status(notify)

    def end_run(self) -> None:
        notify = None
        with self._lock:
            was_active = self._run_active
            self._run_active = False
            if self._pack_enabled and self._auto_save and self._entries:
                self._write_auto_save_locked()
            if was_active:
                notify = self._status_locked()
        if notify is not None:
            self._emit_status(notify)

    def record_sentence(
        self,
        sentence_id: str,
        start_ms: int,
        end_ms: int,
        original: str,
        source_language: str = "",
        translation: str = "",
    ) -> bool:
        sid = str(sentence_id or "").strip()
        orig = str(original or "").strip()
        if not sid or not orig:
            return False
        trans = str(translation or "").strip()
        src_lang = str(source_language or "").strip()
        start = max(0, int(start_ms))
        end = max(start, int(end_ms))

        notify = None
        with self._lock:
            if not self._pack_enabled:
                return False
            if self._run_started_wall is None:
                self._run_started_wall = datetime.now()
            existing = self._entries.get(sid)
            if existing is not None:
                existing.start_ms = min(existing.start_ms, start)
                existing.end_ms = max(existing.end_ms, end)
                existing.original = orig
                if src_lang:
                    existing.source_language = src_lang
                if trans:
                    existing.translation = trans
            else:
                self._entries[sid] = TranscriptEntry(
                    sentence_id=sid,
                    start_ms=start,
                    end_ms=end,
                    original=orig,
                    source_language=src_lang,
                    translation=trans,
                )
            if self._auto_save:
                self._write_auto_save_locked()
            notify = self._status_locked()
        if notify is not None:
            self._emit_status(notify)
        return True

    def update_translation(self, sentence_id: str, translation: str) -> bool:
        sid = str(sentence_id or "").strip()
        if not sid:
            return False
        trans = str(translation or "").strip()
        with self._lock:
            if not self._pack_enabled:
                return False
            entry = self._entries.get(sid)
            if entry is None:
                return False
            if entry.translation == trans:
                return True
            entry.translation = trans
            if self._auto_save and self._entries:
                self._write_auto_save_locked()
            return True

    def retract_sentence(self, sentence_id: str) -> bool:
        sid = str(sentence_id or "").strip()
        if not sid:
            return False
        notify = None
        with self._lock:
            if sid not in self._entries:
                return False
            del self._entries[sid]
            if self._pack_enabled and self._auto_save and self._auto_save_path is not None:
                self._write_auto_save_locked(allow_empty=True)
            notify = self._status_locked()
        if notify is not None:
            self._emit_status(notify)
        return True

    def _write_auto_save_locked(self, *, allow_empty: bool = False) -> Path | None:
        if not self._entries and not allow_empty:
            return None
        if self._auto_save_path is None:
            base_dir = Path(self._directory or get_default_transcript_dir())
            ts = (self._run_started_wall or datetime.now()).strftime("%Y%m%d-%H%M%S")
            candidate = base_dir / f"transcript-{ts}.{self._format}"
            suffix_idx = 2
            while candidate.exists():
                candidate = base_dir / f"transcript-{ts}-{suffix_idx}.{self._format}"
                suffix_idx += 1
            self._auto_save_path = candidate
        content = render_transcript(self._entries.values(), self._format)
        try:
            _atomic_write_utf8_bom(self._auto_save_path, content)
            has_entries = bool(self._entries)
            callback = self._on_auto_saved
        except Exception as exc:
            logger.warning("Failed to auto-save transcript to %s: %s", self._auto_save_path, exc)
            return None
        if has_entries and callable(callback):
            try:
                callback()
            except Exception:
                logger.debug("Transcript auto-save usage callback failed", exc_info=True)
        return self._auto_save_path

    def export_to_file(self, path: str | Path, fmt: str | None = None) -> dict:
        target = Path(path)
        with self._lock:
            entries = list(self._entries.values())
            fallback_fmt = fmt or self._format
        if not entries:
            raise ValueError("No transcript entries to export")
        effective_fmt = infer_format_from_path(target, fallback=fallback_fmt)
        content = render_transcript(entries, effective_fmt)
        _atomic_write_utf8_bom(target, content)
        callback = self._on_exported
        if callable(callback):
            try:
                callback()
            except Exception:
                logger.debug("Transcript export usage callback failed", exc_info=True)
        return {
            "path": str(target),
            "count": len(entries),
            "format": effective_fmt,
        }
