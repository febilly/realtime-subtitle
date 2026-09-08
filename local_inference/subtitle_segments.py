"""Sticky presentation rows for a revising full ASR hypothesis.

This is deliberately only a display model.  It makes no claim that a row is
safe to cut in the audio stream; callers that need that decision should use
``semantic_boundary.LocalAgreementBoundaryDetector`` separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

import sentence_segmentation


@dataclass(frozen=True, slots=True)
class SubtitleSegment:
    """One exact, independently renderable span of the current hypothesis."""

    id: int
    text: str


def _is_cjk_like(char: str) -> bool:
    point = ord(char)
    return (
        0x3400 <= point <= 0x4DBF
        or 0x4E00 <= point <= 0x9FFF
        or 0xF900 <= point <= 0xFAFF
        or 0x20000 <= point <= 0x2FA1F
        or 0x3040 <= point <= 0x30FF
        or 0x1100 <= point <= 0x11FF
        or 0x3130 <= point <= 0x318F
        or 0xA960 <= point <= 0xA97F
        or 0xAC00 <= point <= 0xD7AF
        or 0xD7B0 <= point <= 0xD7FF
    )


def _is_spacing_boundary(text: str, index: int) -> bool:
    """Whether splitting at ``index`` cannot break a spaced-language word."""

    if not 0 < index < len(text):
        return False
    return text[index - 1].isspace() or text[index].isspace()


class StickySubtitleSegments:
    """Maintain stable subtitle rows while an ASR full hypothesis changes.

    Safe sentence boundaries found in an update are displayed immediately.
    Existing displayed boundaries are projected through a ``SequenceMatcher``
    edit map and remain rows when an ASR revision changes the punctuation.
    This favours prompt presentation and low reflow; the returned rows always
    concatenate exactly to the newest full hypothesis.

    ``max_cjk_chars`` and ``max_spaced_chars`` bound otherwise unpunctuated
    rows.  They are presentation limits, measured in Python characters.  A
    natural comma or whitespace is preferred; words are never cut merely to
    satisfy the spaced-language limit.
    """

    def __init__(
        self,
        *,
        max_cjk_chars: int = 64,
        max_spaced_chars: int = 180,
        min_fallback_chars: int | None = None,
    ) -> None:
        if max_cjk_chars < 2:
            raise ValueError("max_cjk_chars must be at least 2")
        if max_spaced_chars < 2:
            raise ValueError("max_spaced_chars must be at least 2")
        if min_fallback_chars is not None and min_fallback_chars < 1:
            raise ValueError("min_fallback_chars must be positive")
        self.max_cjk_chars = int(max_cjk_chars)
        self.max_spaced_chars = int(max_spaced_chars)
        self.min_fallback_chars = (
            None if min_fallback_chars is None else int(min_fallback_chars)
        )
        self._segments: list[SubtitleSegment] = []
        self._next_id = 1

    @property
    def segments(self) -> tuple[SubtitleSegment, ...]:
        """The active rows, in source order, as immutable segment objects."""

        return tuple(self._segments)

    def reset(self) -> None:
        """Forget the active utterance while retaining session-unique IDs."""

        self._segments.clear()

    def update(self, text: str | None) -> list[SubtitleSegment]:
        """Apply a newest *full* ASR hypothesis and return its display rows."""

        newest = str(text or "")
        if not newest:
            self._segments.clear()
            return []

        previous_text = "".join(segment.text for segment in self._segments)
        sticky = self._project_old_boundaries(previous_text, newest)
        cuts = self._sentence_cuts(newest)
        cuts.update(sticky)
        cuts = self._add_fallback_cuts(newest, cuts)
        ranges = self._ranges_from_cuts(len(newest), cuts)
        ranges = self._merge_whitespace_ranges(newest, ranges)
        self._segments = self._reuse_ids(previous_text, newest, ranges)
        # A display-model bug must never silently lose a character in a live
        # caption.  The construction above is slice based, so this is also a
        # cheap executable invariant during future changes.
        assert "".join(segment.text for segment in self._segments) == newest
        assert all(segment.text for segment in self._segments)
        return list(self._segments)

    def consume_prefix(self, prefix: str | None) -> list[SubtitleSegment]:
        """Remove an exact displayed prefix for an external semantic handoff.

        ``prefix`` must match the current complete display text exactly.  At a
        row boundary the finalized rows retain their IDs.  If it ends inside a
        row, the finalized prefix keeps the original row ID and the remaining
        active suffix receives a new ID.  This keeps the DOM identity attached
        to the earlier, already displayed content.
        """

        value = str(prefix or "")
        current = "".join(segment.text for segment in self._segments)
        if not current.startswith(value):
            raise ValueError("prefix must exactly match the current display text")
        if not value:
            return []

        remaining = len(value)
        consumed: list[SubtitleSegment] = []
        active: list[SubtitleSegment] = []
        for index, segment in enumerate(self._segments):
            if remaining <= 0:
                active.extend(self._segments[index:])
                break
            if remaining >= len(segment.text):
                consumed.append(segment)
                remaining -= len(segment.text)
                continue

            # The original displayed row belongs to the earlier content.  The
            # suffix is a newly created live row, so it gets a fresh identity.
            consumed.append(SubtitleSegment(segment.id, segment.text[:remaining]))
            active.append(SubtitleSegment(self._new_id(), segment.text[remaining:]))
            active.extend(self._segments[index + 1 :])
            remaining = 0
            break

        self._segments = active
        assert "".join(segment.text for segment in consumed) == value
        assert all(segment.text for segment in consumed)
        assert all(segment.text for segment in self._segments)
        return consumed

    def _new_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    @staticmethod
    def _sentence_cuts(text: str) -> set[int]:
        pieces = sentence_segmentation.split_text_at_sentence_boundaries(text)
        offset = 0
        cuts: set[int] = set()
        for piece in pieces[:-1]:
            offset += len(piece)
            if 0 < offset < len(text):
                cuts.add(offset)
        return cuts

    def _project_old_boundaries(self, old: str, new: str) -> set[int]:
        if not old or not new or len(self._segments) < 2:
            return set()
        mapper = _OffsetMapper(old, new)
        boundaries: set[int] = set()
        offset = 0
        for segment in self._segments[:-1]:
            offset += len(segment.text)
            projected = mapper.position(offset)
            if 0 < projected < len(new):
                boundaries.add(projected)
        return boundaries

    @staticmethod
    def _ranges_from_cuts(length: int, cuts: set[int]) -> list[tuple[int, int]]:
        result: list[tuple[int, int]] = []
        start = 0
        for end in sorted(cut for cut in cuts if 0 < cut < length):
            if end > start:
                result.append((start, end))
            start = end
        if start < length:
            result.append((start, length))
        return result

    @staticmethod
    def _merge_whitespace_ranges(
        text: str, ranges: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        """Attach separator-only spans to neighboring display rows.

        The shared sentence splitter preserves exact source spacing.  It can
        therefore return ``["Hello.", " "]``; the trailing space belongs to
        the first visible row rather than a blank subtitle row.
        """

        merged: list[tuple[int, int]] = []
        leading_start: int | None = None
        for start, end in ranges:
            if text[start:end].strip():
                if leading_start is not None:
                    start = leading_start
                    leading_start = None
                merged.append((start, end))
            elif merged:
                previous_start, _previous_end = merged[-1]
                merged[-1] = (previous_start, end)
            else:
                leading_start = start
        # An all-whitespace input has no neighboring row.  Preserve it exactly
        # instead of producing a zero-length synthetic segment.
        return merged or ranges

    def _add_fallback_cuts(self, text: str, existing: set[int]) -> set[int]:
        cuts = set(existing)
        boundaries = [0, *sorted(existing), len(text)]
        for start, end in zip(boundaries, boundaries[1:]):
            cursor = start
            while end - cursor > self._limit_for(text[cursor:end]):
                cut = self._natural_fallback_cut(text, cursor, end)
                if cut is None or cut <= cursor:
                    break
                cuts.add(cut)
                cursor = cut
        return cuts

    def _limit_for(self, text: str) -> int:
        visible = [char for char in text if not char.isspace()]
        if visible and sum(_is_cjk_like(char) for char in visible) * 4 >= len(visible):
            return self.max_cjk_chars
        return self.max_spaced_chars

    def _natural_fallback_cut(self, text: str, start: int, end: int) -> int | None:
        limit = self._limit_for(text[start:end])
        target = min(end - 1, start + limit)
        minimum = start + (
            self.min_fallback_chars
            if self.min_fallback_chars is not None
            else max(2, limit // 2)
        )
        # Prefer natural punctuation before whitespace.  Commas stay with the
        # preceding phrase; whitespace also stays left so exact text
        # reconstruction does not need normalization.  The target is soft:
        # natural separators shortly after it are better than a short row.
        soft_end = min(end - 1, target + max(8, limit // 3))
        for index in range(target, minimum - 1, -1):
            previous = text[index - 1]
            if previous in ",，、;；:：":
                return index
        for index in range(target + 1, soft_end + 1):
            if text[index - 1] in ",，、;；:：":
                return index
        for index in range(target, minimum - 1, -1):
            if _is_spacing_boundary(text, index):
                return index
        for index in range(target + 1, soft_end + 1):
            if _is_spacing_boundary(text, index):
                return index

        # For spaced text, a word beginning just before the limit is allowed
        # to finish.  This is better than breaking a word or making a tiny row.
        if not any(_is_cjk_like(char) for char in text[start:end]):
            for index in range(target + 1, end):
                if _is_spacing_boundary(text, index):
                    return index
            return None

        # CJK has no inter-word whitespace, so the configured bound is useful
        # even when no punctuation has arrived yet.
        return target

    def _reuse_ids(
        self,
        old_text: str,
        new_text: str,
        ranges: list[tuple[int, int]],
    ) -> list[SubtitleSegment]:
        if not self._segments:
            return [SubtitleSegment(self._new_id(), new_text[start:end]) for start, end in ranges]

        mapper = _OffsetMapper(old_text, new_text)
        old_ranges: list[tuple[int, int, SubtitleSegment]] = []
        old_start = 0
        for segment in self._segments:
            old_end = old_start + len(segment.text)
            left = mapper.position(old_start)
            right = mapper.position(old_end)
            old_ranges.append((min(left, right), max(left, right), segment))
            old_start = old_end

        available = set(range(len(old_ranges)))
        result: list[SubtitleSegment] = []
        for start, end in ranges:
            best_index = self._best_old_row(start, end, old_ranges, available)
            if best_index is None:
                identifier = self._new_id()
            else:
                identifier = old_ranges[best_index][2].id
                available.remove(best_index)
            result.append(SubtitleSegment(identifier, new_text[start:end]))
        return result

    @staticmethod
    def _best_old_row(
        start: int,
        end: int,
        old_ranges: list[tuple[int, int, SubtitleSegment]],
        available: set[int],
    ) -> int | None:
        best: tuple[int, int] | None = None
        for index in available:
            old_start, old_end, _segment = old_ranges[index]
            overlap = max(0, min(end, old_end) - max(start, old_start))
            # A zero-width projection can still identify a row when a local
            # correction deleted all of its old characters; prefer it only if
            # its mapped anchor belongs to this newly created range.
            anchored = int(start <= old_start < end or start < old_end <= end)
            score = (overlap, anchored)
            if best is None or score > best:
                best = score
                best_index = index
        if best is None or best == (0, 0):
            return None
        return best_index


class _OffsetMapper:
    """Map character offsets through one bounded, full-hypothesis diff."""

    def __init__(self, old: str, new: str) -> None:
        self._opcodes = SequenceMatcher(None, old, new, autojunk=False).get_opcodes()

    def position(self, offset: int) -> int:
        for tag, old_start, old_end, new_start, new_end in self._opcodes:
            if old_start <= offset <= old_end:
                if tag == "equal":
                    return new_start + (offset - old_start)
                if old_end == old_start:
                    return new_end
                # Replacements retain relative positions.  This maps a row
                # boundary after '.' to one after ',' without needing special
                # punctuation cases, while deletions naturally collapse.
                return new_start + round((offset - old_start) * (new_end - new_start) / (old_end - old_start))
        return self._opcodes[-1][4] if self._opcodes else 0


__all__ = ["StickySubtitleSegments", "SubtitleSegment"]
