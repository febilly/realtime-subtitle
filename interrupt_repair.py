"""Short-interrupt repair: rejoin a sentence split by another speaker's
brief filler.

    A1 (speaker A) -> B (speaker B, short filler like "うん" / "嗯") -> A2 (A resumes)

When A2 starts shortly after B, A1 is retracted and reopened so A2 continues
it as one sentence. This module holds the pure part: the history of recently
finalized sentences, the A1/B match rule and token cloning. SonioxSession
applies the merge (display buffer, pairer, retract frame).

Settings are read from the `config` object passed in at call time, so a
runtime settings change (or a test stub) takes effect immediately.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

HISTORY_MAX = 8
# Small timestamp jitter/overlap allowed at both handoffs. Large overlap is
# ordinary cross-talk, not a brief interruption to repair.
OVERLAP_TOLERANCE_MS = 120.0


@dataclass
class FinalizedSentenceSnapshot:
    speaker: str
    sentence_id: str
    original_tokens: list[dict]
    translation_tokens: list[dict]
    source_start_ms: float
    source_end_ms: float
    retracted: bool = False

    def duration_ms(self) -> float | None:
        if self.source_start_ms is None or self.source_end_ms is None:
            return None
        return max(0.0, float(self.source_end_ms) - float(self.source_start_ms))


def source_time_bounds_ms(
    tokens: list[dict],
    is_internal_token: Callable[[object], bool],
) -> tuple[float | None, float | None]:
    """(first start, last end) of the source tokens; (None, None) when any
    source token lacks timestamps."""
    starts: list[float] = []
    ends: list[float] = []
    for token in tokens or []:
        if not isinstance(token, dict):
            continue
        if token.get("translation_status") == "translation":
            continue
        if is_internal_token(token):
            continue
        try:
            start = float(token["start_ms"])
            end = float(token["end_ms"])
        except (KeyError, TypeError, ValueError):
            return (None, None)
        starts.append(start)
        ends.append(end)
    if not starts or not ends:
        return (None, None)
    return (min(starts), max(ends))


def normalize_filler_text(text: str) -> str:
    """NFKC + casefold, with punctuation, symbols and spaces removed."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    chars: list[str] = []
    for ch in value:
        category = unicodedata.category(ch)
        if category.startswith("P") or category.startswith("S") or category.startswith("Z"):
            continue
        chars.append(ch)
    return "".join(chars).strip()


def filler_whitelist(config) -> set[str]:
    raw = str(getattr(config, "SONIOX_INTERRUPT_FILLER_WHITELIST", "") or "")
    values: set[str] = set()
    for item in re.split(r"[,，\r\n\t]+", raw):
        normalized = normalize_filler_text(item)
        if normalized:
            values.add(normalized)
    return values


def matches_filler_whitelist(text: str, config) -> bool:
    if not bool(getattr(config, "SONIOX_INTERRUPT_FILLER_WHITELIST_ENABLED", True)):
        return True
    normalized = normalize_filler_text(text)
    if not normalized:
        return False
    return normalized in filler_whitelist(config)


def clone_tokens_for_merge(tokens: list[dict], new_sentence_id: str) -> list[dict]:
    """Copies of A1's tokens for the merged sentence, with A1's final
    sentence-ending punctuation dropped so A2 can continue it."""
    cloned = [dict(t) for t in tokens or [] if isinstance(t, dict)]
    for token in cloned:
        token["llm_sentence_id"] = new_sentence_id
    for token in reversed(cloned):
        if token.get("translation_status") == "translation":
            continue
        text = str(token.get("text") or "")
        stripped = text.rstrip()
        if not stripped:
            continue
        cleaned = re.sub(r"[。．.!！？?…︒︕︖]+$", "", stripped)
        if cleaned != stripped:
            token["text"] = text[:len(text) - len(stripped)] + cleaned
        break
    return cloned


class InterruptRepairHistory:
    """Recently finalized sentences and the ids retracted by a repair."""

    def __init__(self) -> None:
        self.snapshots: list[FinalizedSentenceSnapshot] = []
        self.retracted_ids: set[str] = set()

    def clear(self) -> None:
        self.snapshots.clear()
        self.retracted_ids.clear()

    def is_retracted(self, sentence_id) -> bool:
        return bool(sentence_id) and str(sentence_id) in self.retracted_ids

    def retract(self, snapshot: FinalizedSentenceSnapshot) -> None:
        snapshot.retracted = True
        self.retracted_ids.add(snapshot.sentence_id)

    def record(
        self,
        speaker: str,
        sentence_id: str,
        original_tokens: list[dict],
        translation_tokens: list[dict],
        *,
        is_internal_token: Callable[[object], bool],
    ) -> None:
        start_ms, end_ms = source_time_bounds_ms(original_tokens, is_internal_token)
        if start_ms is None or end_ms is None:
            return
        snapshot = FinalizedSentenceSnapshot(
            speaker=str(speaker),
            sentence_id=str(sentence_id),
            original_tokens=[dict(t) for t in original_tokens if isinstance(t, dict)],
            translation_tokens=[dict(t) for t in translation_tokens if isinstance(t, dict)],
            source_start_ms=start_ms,
            source_end_ms=end_ms,
        )
        # A revived sentence (timeout_empty then late translation) dispatches
        # twice: replace the stale empty-translation snapshot instead of
        # keeping same-id duplicates for the interrupt-repair walk.
        self.snapshots = [s for s in self.snapshots if s.sentence_id != snapshot.sentence_id]
        self.snapshots.append(snapshot)
        if len(self.snapshots) > HISTORY_MAX:
            self.snapshots = self.snapshots[-HISTORY_MAX:]

    def find_merge_pair(
        self,
        current_speaker: str,
        current_start_ms: float,
        *,
        config,
        join_text: Callable[[list[dict]], str],
    ) -> tuple[FinalizedSentenceSnapshot, FinalizedSentenceSnapshot] | None:
        """(A1, B) when a token of `current_speaker` starting at
        `current_start_ms` resumes A1 right after B's short filler."""
        if not bool(getattr(config, "SONIOX_INTERRUPT_REPAIR_ENABLED", True)):
            return None
        max_duration_ms = float(getattr(config, "SONIOX_INTERRUPT_MAX_DURATION_MS", 300))
        resume_gap_ms = float(getattr(config, "SONIOX_INTERRUPT_RESUME_GAP_MS", 1000))
        if max_duration_ms <= 0 or resume_gap_ms <= 0:
            return None

        # Only the two latest active sentences may form A1 -> B. Searching
        # farther back can incorrectly revive an old A1/B pair after another
        # sentence has already occurred.
        active = [snapshot for snapshot in self.snapshots if not snapshot.retracted]
        if len(active) < 2:
            return None
        previous, interrupt = active[-2:]
        if previous.speaker != str(current_speaker):
            return None
        if interrupt.speaker == str(current_speaker):
            return None
        duration = interrupt.duration_ms()
        if duration is None or duration > max_duration_ms:
            return None
        if not matches_filler_whitelist(join_text(interrupt.original_tokens), config):
            return None

        previous_to_interrupt_ms = interrupt.source_start_ms - previous.source_end_ms
        interrupt_to_current_ms = current_start_ms - interrupt.source_end_ms
        if not -OVERLAP_TOLERANCE_MS <= previous_to_interrupt_ms <= resume_gap_ms:
            return None
        if not -OVERLAP_TOLERANCE_MS <= interrupt_to_current_ms <= resume_gap_ms:
            return None
        return previous, interrupt
