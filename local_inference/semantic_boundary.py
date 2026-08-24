"""Conservative sentence commits for revising local-ASR hypotheses.

The detector implements a text-only LocalAgreement policy.  A sentence prefix
is committable only when the same safe boundary is present in the configured
number of consecutive hypotheses and every hypothesis already contains useful
text to the right of that boundary.  The right-context requirement prevents a
period hallucinated at the provisional end of a partial result from becoming a
hard audio cut.

This module deliberately has no recognizer or configuration dependencies.  A
caller can observe hypotheses, apply the returned commit to its own audio
timeline, and then call :meth:`LocalAgreementBoundaryDetector.reset` after it
has switched to the suffix utterance.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import unicodedata

import sentence_segmentation


@dataclass(frozen=True, slots=True)
class BoundaryCommit:
    """The earliest sentence prefix safe to commit from one hypothesis.

    ``prefix + suffix`` is always exactly the newest observed hypothesis.
    ``boundary_index`` is therefore also the prefix length in Python character
    indices; audio alignment remains the caller's responsibility.
    """

    prefix: str
    suffix: str
    boundary_index: int
    agreement_count: int


def _nonspace_length(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _sentence_boundary_indices(text: str) -> tuple[int, ...]:
    """Return safe, non-terminal boundary indices produced by the shared rules.

    ``split_text_at_sentence_boundaries`` already owns the application's rules
    for decimal points, abbreviations, ellipses, and unclosed quote pairs.  A
    split before the last piece is a confirmed textual boundary.  A boundary at
    the absolute end is intentionally omitted because it has no right context.
    """

    pieces = sentence_segmentation.split_text_at_sentence_boundaries(text)
    if len(pieces) < 2:
        return ()

    indices: list[int] = []
    offset = 0
    for piece in pieces[:-1]:
        offset += len(piece)
        indices.append(offset)
    return tuple(indices)


class LocalAgreementBoundaryDetector:
    """Detect stable sentence prefixes across consecutive ASR hypotheses.

    The default ``agreement_count=2`` is LocalAgreement-2.  Once a commit is
    returned, the detector latches and returns no further decision until
    :meth:`reset` is called.  This makes the audio-cut handoff explicit and
    prevents the same prefix from being emitted twice while a recognizer is
    being reset or replaying overlap audio.
    """

    def __init__(
        self,
        *,
        agreement_count: int = 2,
        min_prefix_nonspace_chars: int = 1,
        min_right_context_nonspace_chars: int = 1,
        require_safe_replay_evidence: bool = False,
    ) -> None:
        if agreement_count < 2:
            raise ValueError("agreement_count must be at least 2")
        if min_prefix_nonspace_chars < 1:
            raise ValueError("min_prefix_nonspace_chars must be at least 1")
        if min_right_context_nonspace_chars < 1:
            raise ValueError("min_right_context_nonspace_chars must be at least 1")

        self.agreement_count = int(agreement_count)
        self.min_prefix_nonspace_chars = int(min_prefix_nonspace_chars)
        self.min_right_context_nonspace_chars = int(min_right_context_nonspace_chars)
        self.require_safe_replay_evidence = bool(require_safe_replay_evidence)
        self._hypotheses: deque[str] = deque(maxlen=self.agreement_count)
        self._latched = False
        self._has_provisional_boundary = False

    @property
    def latched(self) -> bool:
        """Whether a commit has been returned and a caller reset is required."""

        return self._latched

    @property
    def has_provisional_boundary(self) -> bool:
        """Whether the newest hypothesis has a usable internal boundary."""

        return self._has_provisional_boundary

    def reset(self) -> None:
        """Forget prior hypotheses and allow a new utterance to commit."""

        self._hypotheses.clear()
        self._latched = False
        self._has_provisional_boundary = False

    def observe(self, hypothesis: str | None) -> BoundaryCommit | None:
        """Observe one full revising hypothesis and maybe return a stable prefix.

        Agreement is exact and deliberately conservative: suffix text may
        revise freely, but the prefix and the safe boundary character index must
        match in every hypothesis in the agreement window.
        """

        if self._latched:
            return None

        text = str(hypothesis or "")
        self._hypotheses.append(text)
        newest_boundaries = _sentence_boundary_indices(text)
        self._has_provisional_boundary = any(
            _nonspace_length(text[:index]) >= self.min_prefix_nonspace_chars
            and (
                not self.require_safe_replay_evidence
                or has_safe_replay_evidence(text[:index])
            )
            and _nonspace_length(text[index:])
            >= self.min_right_context_nonspace_chars
            for index in newest_boundaries
        )
        if len(self._hypotheses) < self.agreement_count:
            return None

        hypotheses = tuple(self._hypotheses)
        newest = hypotheses[-1]
        boundaries_by_hypothesis = {
            value: frozenset(_sentence_boundary_indices(value)) for value in hypotheses
        }

        for boundary_index in _sentence_boundary_indices(newest):
            prefix = newest[:boundary_index]
            if _nonspace_length(prefix) < self.min_prefix_nonspace_chars:
                continue
            if (
                self.require_safe_replay_evidence
                and not has_safe_replay_evidence(prefix)
            ):
                continue

            stable = True
            for value in hypotheses:
                if boundary_index not in boundaries_by_hypothesis[value]:
                    stable = False
                    break
                if value[:boundary_index] != prefix:
                    stable = False
                    break
                if (
                    _nonspace_length(value[boundary_index:])
                    < self.min_right_context_nonspace_chars
                ):
                    stable = False
                    break
            if not stable:
                continue

            self._latched = True
            self._has_provisional_boundary = False
            return BoundaryCommit(
                prefix=prefix,
                suffix=newest[boundary_index:],
                boundary_index=boundary_index,
                agreement_count=self.agreement_count,
            )

        return None


def _is_cjk_like(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
        or 0x3040 <= codepoint <= 0x30FF
        or 0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _is_spaced_word_core(char: str) -> bool:
    return (
        bool(char)
        and (char.isalnum() or char == "_")
        and not _is_cjk_like(char)
    )


def _is_combining_mark(char: str) -> bool:
    return bool(char) and unicodedata.category(char).startswith("M")


def _is_spaced_word_char(char: str) -> bool:
    return (
        _is_spaced_word_core(char)
        or _is_combining_mark(char)
        or char in "'\u2019"
    )


def _word_token_count(value: str) -> int:
    """Count Unicode words while CJK/Kana/Hangul use character evidence."""
    count = 0
    in_word = False
    for index, char in enumerate(value):
        if _is_spaced_word_core(char):
            if not in_word:
                count += 1
            in_word = True
            continue
        if _is_combining_mark(char) and in_word:
            continue
        if (
            char in "'\u2019"
            and in_word
            and index + 1 < len(value)
            and _is_spaced_word_core(value[index + 1])
        ):
            continue
        in_word = False
    return count


def _overlap_has_enough_evidence(
    overlap: str,
    *,
    min_overlap_chars: int,
    min_cjk_chars: int,
    min_word_tokens: int,
) -> bool:
    visible = "".join(char for char in overlap if not char.isspace())
    if len(visible) < min_overlap_chars:
        return False
    if not any(char.isalnum() for char in visible):
        return False

    cjk_count = sum(1 for char in overlap if _is_cjk_like(char))
    if cjk_count >= min_cjk_chars:
        return True

    return _word_token_count(overlap) >= min_word_tokens


def has_safe_replay_evidence(
    text: str | None,
    *,
    min_overlap_chars: int = 4,
    min_cjk_chars: int = 4,
    min_word_tokens: int = 2,
) -> bool:
    """Whether a committed prefix can later be deduplicated conservatively."""
    return _overlap_has_enough_evidence(
        str(text or ""),
        min_overlap_chars=min_overlap_chars,
        min_cjk_chars=min_cjk_chars,
        min_word_tokens=min_word_tokens,
    )


def deduplicate_replayed_overlap(
    committed_text: str | None,
    replayed_text: str | None,
    *,
    min_overlap_chars: int = 4,
    min_cjk_chars: int = 4,
    min_word_tokens: int = 2,
) -> str:
    """Remove a strongly evidenced replay prefix from revised ASR text.

    The longest exact suffix/prefix match wins.  ASCII matches must start and
    end on word boundaries and contain at least two words by default; unspaced
    CJK matches require at least four CJK-like characters.  Short common words,
    punctuation-only matches, case/spacing variants, and matches inside an
    ASCII word are intentionally retained to avoid deleting legitimate speech.

    Leading whitespace is stripped only when an overlap is actually removed.
    If no safe match exists, the original ``replayed_text`` is returned exactly.
    """

    if min_overlap_chars < 1:
        raise ValueError("min_overlap_chars must be at least 1")
    if min_cjk_chars < 1:
        raise ValueError("min_cjk_chars must be at least 1")
    if min_word_tokens < 1:
        raise ValueError("min_word_tokens must be at least 1")

    committed = str(committed_text or "")
    replayed = str(replayed_text or "")
    if not committed or not replayed:
        return replayed

    left = committed.rstrip()
    right = replayed.lstrip()
    if not left or not right:
        return replayed

    for overlap_length in range(min(len(left), len(right)), 0, -1):
        overlap = right[:overlap_length]
        if not left.endswith(overlap):
            continue

        left_start = len(left) - overlap_length
        if (
            left_start > 0
            and _is_spaced_word_char(left[left_start - 1])
            and _is_spaced_word_char(overlap[0])
        ):
            continue
        if (
            overlap_length < len(right)
            and _is_spaced_word_char(overlap[-1])
            and _is_spaced_word_char(right[overlap_length])
        ):
            continue
        if not _overlap_has_enough_evidence(
            overlap,
            min_overlap_chars=min_overlap_chars,
            min_cjk_chars=min_cjk_chars,
            min_word_tokens=min_word_tokens,
        ):
            continue

        return right[overlap_length:].lstrip()

    return replayed


def deduplicate_normalized_replay(
    committed_text: str | None,
    replayed_text: str | None,
    *,
    min_cjk_chars: int = 4,
    min_word_tokens: int = 2,
    max_overlap_lexical_chars: int | None = None,
) -> str:
    """Remove known replay audio despite punctuation/case revisions.

    This is intentionally separate from :func:`deduplicate_replayed_overlap`:
    callers may use it only when they know that the new audio window contains
    pre-roll from ``committed_text``.  Matching ignores whitespace,
    punctuation and case, but still requires four CJK-like characters or two
    Unicode spaced-language words and chooses the longest lexical suffix/prefix match.
    """
    committed = str(committed_text or "")
    replayed = str(replayed_text or "")
    if not committed or not replayed:
        return replayed

    def lexical_with_offsets(value: str) -> tuple[str, list[int]]:
        lexical: list[str] = []
        offsets: list[int] = []
        for index, original in enumerate(value):
            normalized = unicodedata.normalize("NFKC", original).casefold()
            for char in normalized:
                if char.isspace() or unicodedata.category(char).startswith("P"):
                    continue
                lexical.append(char)
                offsets.append(index + 1)
        return "".join(lexical), offsets

    left, left_offsets = lexical_with_offsets(committed)
    right, right_offsets = lexical_with_offsets(replayed)
    if not left or not right:
        return replayed

    maximum = min(len(left), len(right))
    if max_overlap_lexical_chars is not None:
        maximum = min(maximum, max(0, int(max_overlap_lexical_chars)))
    for length in range(maximum, 0, -1):
        overlap = right[:length]
        if not left.endswith(overlap):
            continue
        left_lexical_start = len(left) - length
        left_original_start = left_offsets[left_lexical_start] - 1
        original_end = right_offsets[length - 1]
        original_overlap = replayed[:original_end]
        # Punctuation/case normalization must not turn a substring inside an
        # spaced-language word into replay evidence (``broadcast`` -> ``cast``), nor trim
        # only the beginning of a longer word in the new hypothesis.  Replay
        # audio can start mid-word, so both raw-text word boundaries matter.
        if (
            left_original_start > 0
            and _is_spaced_word_char(committed[left_original_start - 1])
            and _is_spaced_word_char(committed[left_original_start])
        ):
            continue
        if (
            original_end < len(replayed)
            and _is_spaced_word_char(replayed[original_end - 1])
            and _is_spaced_word_char(replayed[original_end])
        ):
            continue
        cjk_count = sum(1 for char in overlap if _is_cjk_like(char))
        word_count = _word_token_count(original_overlap)
        if cjk_count < min_cjk_chars and word_count < min_word_tokens:
            continue

        # If the whole hypothesis matches the committed suffix, Qwen may have
        # suppressed pre-roll and recognized a legitimate repeated sentence.
        # Wait for lexical right context instead of deleting everything.
        if length >= len(right):
            return replayed

        while original_end < len(replayed):
            char = replayed[original_end]
            if char.isspace() or unicodedata.category(char).startswith("P"):
                original_end += 1
                continue
            break
        return replayed[original_end:]
    return replayed


__all__ = [
    "BoundaryCommit",
    "LocalAgreementBoundaryDetector",
    "deduplicate_normalized_replay",
    "deduplicate_replayed_overlap",
    "has_safe_replay_evidence",
]
