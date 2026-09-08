"""Low-latency local subtitles: immediate source rows and coalesced translation.

Display boundaries belong to the text model, not to VAD or the audio locator.
Each row has an independent Hy-MT revision chain while all chains can share
one model. Only one translation runs at a time; waiting revisions of a row
replace one another without dropping the final state of any completed row.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time
from typing import Callable
from uuid import uuid4

from .subtitle_segments import StickySubtitleSegments


@dataclass
class _Row:
    id: str
    source: str
    source_language: str
    target_language: str
    order: float
    requires_translation: bool
    translation: str = ""
    revision: int = 0
    source_revision: int = 0
    applied_translation_revision: int = -1
    epoch: int = 0
    is_final: bool = False
    previous_source: str = ""

    def payload(self) -> dict:
        return {key: getattr(self, key) for key in (
            "id", "source", "translation", "source_language", "target_language",
            "order", "requires_translation", "revision", "is_final",
        )}


class LocalSubtitlePipeline:
    def __init__(
        self,
        translator_factory: Callable,
        publish: Callable[[dict], None],
        on_final: Callable[[dict], None],
        on_error: Callable[[Exception | str], None],
    ) -> None:
        self._make_translator = translator_factory
        self._publish = publish
        self._on_final = on_final
        self._on_error = on_error
        self._segments = StickySubtitleSegments()
        self._prefix = "local-" + uuid4().hex[:12] + "-"
        self._rows: dict[str, _Row] = {}
        self._pending: OrderedDict[str, None] = OrderedDict()
        self._ready: dict[str, int] = {}
        self._translators: dict[str, object] = {}
        self._history: deque[dict] = deque(maxlen=8)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-translation")
        self._working = False
        self._closed = False
        self._base_order = 0
        self._order_origin = time.time()

    def _order(self, index: int) -> float:
        # Keep newly started runs after retained captions from earlier runs.
        return self._order_origin + index / 10000.0

    def _report_error(self, error: Exception | str) -> None:
        try:
            self._on_error(error)
        except Exception:
            pass  # An error observer must never strand final translation work.

    def _emit(self, frame: dict) -> None:
        try:
            self._publish(frame)
        except Exception as exc:
            self._report_error(exc)

    def _id(self, segment_id: int) -> str:
        return self._prefix + str(segment_id)

    def update(
        self, text: str, is_final: bool, source_language: str, target_language: str,
        *, translation_enabled: bool = True, semantic_suffix: str | None = None,
    ) -> None:
        with self._lock:
            if self._closed:
                return
            old_ids = {self._id(segment.id) for segment in self._segments.segments}
            full_text = text + semantic_suffix if semantic_suffix is not None else text
            segments = self._segments.update(full_text)
            consumed = []
            if semantic_suffix is not None:
                consumed = self._segments.consume_prefix(text)
                segments = consumed + list(self._segments.segments)
            new_ids = {self._id(segment.id) for segment in segments}
            removed = sorted(old_ids - new_ids)
            for row_id in removed:
                self._rows.pop(row_id, None)
                self._pending.pop(row_id, None)
                self._ready.pop(row_id, None)

            changed: dict[str, _Row] = {}
            previous_source = self._history[-1]["source"] if self._history else ""
            # Pending finalized rows still count as context before their MT completes.
            preceding = [row for row in self._rows.values() if row.order < self._order(self._base_order)]
            if preceding:
                previous_source = max(preceding, key=lambda row: row.order).source
            for index, segment in enumerate(segments):
                row_id = self._id(segment.id)
                requires = translation_enabled and source_language != target_language
                row = self._rows.get(row_id)
                if row is None:
                    row = _Row(row_id, segment.text, source_language, target_language,
                               self._order(self._base_order + index), requires)
                    self._rows[row_id] = row
                    source_changed = True
                else:
                    source_changed = (
                        row.source != segment.text or row.source_language != source_language
                        or row.target_language != target_language or row.requires_translation != requires
                    )
                    direction_changed = (row.source_language, row.target_language, row.requires_translation) != (
                        source_language, target_language, requires,
                    )
                    # An old whole-row translation cannot land after that row
                    # has been repartitioned into different subtitle units.
                    if direction_changed or (old_ids != new_ids and row.source != segment.text):
                        row.epoch += 1
                    if direction_changed:
                        row.translation = ""
                    row.source = segment.text
                    row.source_language = source_language
                    row.target_language = target_language
                    row.requires_translation = requires
                order = self._order(self._base_order + index)
                order_changed = row.order != order
                row.order = order
                context_changed = row.previous_source != previous_source
                row.previous_source = previous_source
                previous_source = row.source
                if source_changed or context_changed:
                    row.source_revision += 1
                    self._queue(row)
                if source_changed or order_changed:
                    row.revision += 1
                    changed[row_id] = row

            if semantic_suffix is not None:
                self._base_order += len(consumed)
                for segment in consumed:
                    row = self._rows[self._id(segment.id)]
                    row.is_final = True
                    row.source_revision += 1
                    row.revision += 1
                    changed[row.id] = row
                    self._queue(row)
            elif is_final:
                for segment in segments:
                    row = self._rows[self._id(segment.id)]
                    row.is_final = True
                    row.source_revision += 1
                    row.revision += 1
                    changed[row.id] = row
                    self._queue(row)
                self._base_order += len(segments) + 1
                self._segments.reset()

            if changed or removed:
                self._emit({
                    "type": "update", "local_segments": [row.payload() for row in changed.values()],
                    "local_removed_segment_ids": removed,
                })
            self._start_worker()

    def _queue(self, row: _Row) -> None:
        self._pending[row.id] = None

    def _start_worker(self) -> None:
        if self._pending and not self._working:
            self._working = True
            self._executor.submit(self._drain)

    def _context(self, row: _Row) -> tuple[list[dict], str]:
        before = list(self._history)
        after = []
        for other in sorted(self._rows.values(), key=lambda value: value.order):
            if other.order < row.order:
                before.append({"source": other.source, "target": other.translation})
            elif other.order > row.order:
                after.append(other.source)
        return before[-8:], "".join(after)[:256]

    def _drain(self) -> None:
        while True:
            with self._lock:
                self._cleanup_translators()
                if not self._pending:
                    self._working = False
                    return
                row_id, _ = self._pending.popitem(last=False)
                row = self._rows.get(row_id)
                if row is None:
                    continue
                source_revision, epoch = row.source_revision, row.epoch
                source, source_lang, target_lang = row.source, row.source_language, row.target_language
                final, requires = row.is_final, row.requires_translation
                context, following = self._context(row)
            translated = ""
            error = None
            try:
                if requires:
                    translator = self._translators.get(row_id)
                    if translator is None:
                        translator = self._make_translator()
                        self._translators[row_id] = translator
                    translated = translator.translate(
                        source, source_language=source_lang, target_language=target_lang,
                        is_partial=not final, context_pairs=context, following_source=following,
                    ).strip()
                    if translated.startswith("[ERROR]"):
                        error, translated = translated, ""
            except Exception as exc:
                error = exc
            if error is not None:
                self._report_error(error)
            with self._lock:
                row = self._rows.get(row_id)
                if row is None or row.epoch != epoch:
                    continue
                # Do not starve the first translation during continuous speech:
                # an older source version can improve what is currently shown,
                # but can never overwrite a newer applied translation.
                if translated and source_revision > row.applied_translation_revision:
                    row.translation = translated
                    row.applied_translation_revision = source_revision
                    row.revision += 1
                    self._emit({"type": "update", "local_segments": [row.payload()]})
                if final and row.source_revision == source_revision:
                    # Final errors still release the chain and preserve its
                    # last usable display translation; report them separately.
                    self._ready[row_id] = source_revision
                    self._flush_finalized()

    def _flush_finalized(self) -> None:
        # A later row can finish while the preceding row's final is queued
        # behind an in-flight partial. Logs, IPC and OSC still commit in speech order.
        for row in sorted(self._rows.values(), key=lambda value: value.order):
            if not row.is_final or self._ready.get(row.id) != row.source_revision:
                break
            self._history.append({"source": row.source, "target": row.translation})
            try:
                self._on_final(row.payload())
            except Exception as exc:
                self._report_error(exc)
            self._rows.pop(row.id, None)
            self._ready.pop(row.id, None)

    def _cleanup_translators(self) -> None:
        for row_id in list(self._translators):
            if row_id not in self._rows:
                translator = self._translators.pop(row_id)
                try:
                    translator.close()
                except Exception as exc:
                    self._report_error(exc)

    def close(self) -> None:
        """Seal any remaining visible tail and drain every row before unload."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            changed = []
            for row in self._rows.values():
                if not row.is_final:
                    row.is_final = True
                    row.source_revision += 1
                    row.revision += 1
                    changed.append(row.payload())
                    self._queue(row)
            self._segments.reset()
            if changed:
                self._emit({"type": "update", "local_segments": changed})
            self._start_worker()
        self._executor.shutdown(wait=True)
        with self._lock:
            self._cleanup_translators()
