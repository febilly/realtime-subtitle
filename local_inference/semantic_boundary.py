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
from difflib import SequenceMatcher
import unicodedata

import sentence_segmentation


@dataclass(frozen=True, slots=True)
class BoundaryCommit:
    """A sentence prefix safe to commit from one hypothesis.

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


def _lexical(text: str) -> str:
    """Words only: NFKC, case-folded, without whitespace or punctuation."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


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

    ``prefer_latest`` selects the furthest stable sentence boundary so an audio
    window can discard several completed sentences in one location operation.

    ``lexical_agreement`` compares the prefixes by their words only, so a
    comma/period or capitalization revision *inside* an otherwise stable
    prefix no longer resets agreement.  The boundary itself must still be a
    sentence end in every hypothesis, and the committed text is always the
    newest hypothesis' exact prefix.
    """

    def __init__(
        self,
        *,
        agreement_count: int = 2,
        min_prefix_nonspace_chars: int = 1,
        min_right_context_nonspace_chars: int = 1,
        require_safe_replay_evidence: bool = False,
        prefer_latest: bool = False,
        lexical_agreement: bool = False,
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
        self.prefer_latest = bool(prefer_latest)
        self.lexical_agreement = bool(lexical_agreement)
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
        # Lexical prefix at each sentence boundary -> that boundary's index.
        lexical_boundaries = (
            {
                value: {
                    _lexical(value[:index]): index
                    for index in boundaries_by_hypothesis[value]
                }
                for value in hypotheses
            }
            if self.lexical_agreement
            else {}
        )

        candidates = _sentence_boundary_indices(newest)
        if self.prefer_latest:
            candidates = tuple(reversed(candidates))
        for boundary_index in candidates:
            prefix = newest[:boundary_index]
            if _nonspace_length(prefix) < self.min_prefix_nonspace_chars:
                continue
            if (
                self.require_safe_replay_evidence
                and not has_safe_replay_evidence(prefix)
            ):
                continue

            stable = True
            lexical_prefix = _lexical(prefix) if self.lexical_agreement else ""
            for value in hypotheses:
                if self.lexical_agreement:
                    index = lexical_boundaries[value].get(lexical_prefix)
                    if index is None:
                        stable = False
                        break
                else:
                    index = boundary_index
                    if index not in boundaries_by_hypothesis[value]:
                        stable = False
                        break
                    if value[:index] != prefix:
                        stable = False
                        break
                if (
                    _nonspace_length(value[index:])
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


def _lexical_with_offsets(value: str) -> tuple[str, list[int]]:
    """Lexical characters plus, for each, the original end offset (exclusive)."""
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


def strip_committed_prefix(
    committed_text: str | None,
    hypothesis: str | None,
    *,
    min_match_ratio: float = 0.6,
    min_block_chars: int = 3,
    sentence_end_fallback: bool = False,
) -> str | None:
    """Return what ``hypothesis`` says after an already committed prefix.

    A text-level commit finalizes sentences while their audio is still in the
    recognizer window, so every later hypothesis re-recognizes them first,
    sometimes with revisions, and after an audio trim it may also lose the
    first words that the cut ran through.  Matching is lexical (words only).
    Only solid runs of ``min_block_chars`` or more count, and only near the
    start of the hypothesis, so scattered single letters of a long new
    sentence can never pose as the committed text.

    The committed *end* must be found: the last solid run has to reach within
    a few characters of it.  Its head may be missing or revised, as long as
    half of the committed text, and ``min_match_ratio`` of it from the first
    run onwards, is matched.  A cut that lands inside a spaced-language word moves to that
    word's end, and separators after the cut are dropped.

    ``sentence_end_fallback`` covers a committed sentence the recognizer has
    since rewritten (homophones, a respelled name): committed text always ends
    at a sentence end, so a sentence end in the hypothesis at nearly the same
    lexical length is taken as its counterpart.

    Returns ``None`` when the hypothesis does not plausibly contain the end of
    the committed text, and ``""`` when it has said nothing beyond it yet.
    """
    committed = str(committed_text or "")
    text = str(hypothesis or "")
    left, _left_offsets = _lexical_with_offsets(committed)
    if not left:
        return text
    right, right_offsets = _lexical_with_offsets(text)
    if not right:
        return ""

    if right.startswith(left):
        end = len(left)
    elif left.startswith(right):
        return ""
    else:
        region = right[: len(left) + max(12, len(left) // 2)]
        minimum = min(min_block_chars, len(left))
        blocks = [
            block
            for block in SequenceMatcher(
                None, left, region, autojunk=False
            ).get_matching_blocks()
            if block.size >= minimum
        ]
        end = _aligned_committed_end(left, region, right, blocks, min_match_ratio)
        if end is None and sentence_end_fallback:
            end = _matching_sentence_end(text, right_offsets, len(left))
        if end is None:
            return None

    if end >= len(right):
        return ""
    cut = right_offsets[end - 1]
    while (
        cut < len(text)
        and _is_spaced_word_char(text[cut - 1])
        and _is_spaced_word_char(text[cut])
    ):
        cut += 1
    while cut < len(text) and (
        text[cut].isspace() or unicodedata.category(text[cut]).startswith("P")
    ):
        cut += 1
    return text[cut:]


def _aligned_committed_end(left, region, right, blocks, min_match_ratio) -> int | None:
    if not blocks:
        return None
    last = blocks[-1]
    tail_gap = len(left) - (last.a + last.size)
    if last.b + last.size >= len(right) and tail_gap > 0:
        # The hypothesis stops inside the committed text: nothing new yet.
        return len(right)
    # The committed end needs a solid anchor of its own (three CJK characters
    # carry about as much evidence as a five-letter run)...
    anchor = 3 if any(_is_cjk_like(char) for char in left[last.a:last.a + last.size]) else 5
    if tail_gap > max(3, len(left) // 5) or last.size < min(anchor, len(left)):
        return None
    # ...and enough of the committed text must be there overall.
    covered = len(left) - blocks[0].a
    matched = sum(block.size for block in blocks)
    if matched < max(min(len(left), 6), 0.5 * len(left), min_match_ratio * covered):
        return None
    # Unmatched committed tail characters were revised in place.
    return min(len(right), last.b + last.size + tail_gap)


def _matching_sentence_end(text: str, offsets: list[int], length: int) -> int | None:
    """Lexical index of the hypothesis sentence end closest to ``length``."""
    tolerance = max(2, round(length * 0.3))
    best: tuple[int, int] | None = None
    position = 0
    for piece in sentence_segmentation.split_text_at_sentence_boundaries(text)[:-1]:
        position += len(piece)
        index = sum(1 for offset in offsets if offset <= position)
        distance = abs(index - length)
        if 0 < index and distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, index)
    return None if best is None else best[1]


def deduplicate_normalized_replay(
    committed_text: str | None,
    replayed_text: str | None,
    *,
    expected_suffix: str | None = "",
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
    ``expected_suffix`` permits a shorter replay overlap only when its
    following text strongly anchors to the suffix already known at the
    semantic cut.
    """
    committed = str(committed_text or "")
    replayed = str(replayed_text or "")
    if not committed or not replayed:
        return replayed

    lexical_with_offsets = _lexical_with_offsets
    left, left_offsets = lexical_with_offsets(committed)
    right, right_offsets = lexical_with_offsets(replayed)
    if not left or not right:
        return replayed

    expected, _expected_offsets = lexical_with_offsets(str(expected_suffix or ""))
    expected_anchor_length = min(8, len(expected))
    # Beginning with the known suffix is a legitimate continuation, not proof
    # of replay.  Keeping it also prevents a repeated semantic tail vanishing.
    if expected_anchor_length >= 6 and right.startswith(expected[:expected_anchor_length]):
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
        enough_standard_evidence = (
            cjk_count >= min_cjk_chars or word_count >= min_word_tokens
        )
        if not enough_standard_evidence:
            # Two CJK characters or one complete spaced word are too weak on
            # their own.  They become safe only when the remainder starts with
            # 6-8 lexical characters of the suffix known at the cut.
            if not (cjk_count >= 2 or word_count >= 1) or expected_anchor_length < 6:
                continue
            remainder, _remainder_offsets = lexical_with_offsets(replayed[original_end:])
            if not remainder.startswith(expected[:expected_anchor_length]):
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
    "strip_committed_prefix",
]
