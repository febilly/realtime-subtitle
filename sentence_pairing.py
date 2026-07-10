"""Order-based pairing of source sentences with their streamed translations.

Soniox two-way translation gives no linkage between a translation token and
the source segment it translates: no timestamps, no segment ids. The only
guarantee is ORDER — "translated tokens are generated after their spoken
tokens and follow the same sequence". The old sentence-buffer code paired
"whatever translation tokens are in the open buffer" with the open source
sentence, so one late translation shifted every subsequent (source, draft)
pair off by one, permanently (the refine LLM then had to re-translate every
line — see llm_log evidence from 2026-07-10).

This module is the single owner of pairing. Per speaker it keeps a FIFO of
sentences:

  - source tokens append to the newest entry (opening one as needed);
  - translation tokens route to the OLDEST entry still awaiting translation
    and are stamped with that entry's sentence id at arrival time;
  - an entry completes when both sides are closed, and only completed
    entries are handed to the caller (display separator + LLM refine).

Translation close signals, strongest first:

  1. resync-on-arrival: a new translation token arrives after a real gap
     (>= RESYNC_GAP_SECONDS) while the head entry's translation already ends
     with sentence-final punctuation -> the head is done, the token belongs
     to the next entry. Soniox streams one sentence's translation as a rapid
     chunk burst; the next sentence's translation cannot start until its
     source was spoken, seconds later. Within-batch tokens after an internal
     punctuation keep appending (no false split on "因为A。所以B。").
  2. batch-end punctuation: at the end of a response batch, a source-closed
     head whose translation ends with sentence-final punctuation is done.
  3. quiet close: source closed, some translation arrived, none for
     QUIET_CLOSE_SECONDS -> done (covers translations without punctuation).
  4. timeout: source closed for MAX_WAIT_SECONDS with no translation at all
     -> done empty (keeps refine/gray-reveal from stalling forever).

Entries that never get a translation (same-language speech, 准确 mode with
soniox translation suppressed) are marked expects_translation=False and
complete as soon as their source closes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import sentence_segmentation

# A translation token arriving at least this long after the previous one,
# when the pending translation already looks complete, starts the NEXT
# sentence's translation. Chunks within one sentence stream much faster.
RESYNC_GAP_SECONDS = 0.30
# Source closed + translation present + no new translation tokens for this
# long -> treat the translation as complete even without ending punctuation.
QUIET_CLOSE_SECONDS = 0.90
# Source closed this long with no translation at all -> give up waiting.
MAX_WAIT_SECONDS = 3.0


@dataclass
class PairedSentence:
    sentence_id: str
    speaker: str
    source_tokens: list = field(default_factory=list)
    translation_tokens: list = field(default_factory=list)
    source_closed: bool = False
    translation_closed: bool = False
    expects_translation: bool = True
    source_closed_at: float = 0.0
    last_translation_at: float = 0.0
    # Why the entry completed / the source closed — display separators reuse
    # the finalize reason ("punctuation" / "endpoint" / "speaker_change").
    close_reason: str = "punctuation"
    # Observability for tuning RESYNC_GAP / QUIET_CLOSE / MAX_WAIT: which
    # rule closed the translation side and the timing it saw (llm_log's
    # pairing_close event; see tools/llm_log_report.py).
    translation_close_reason: str = ""
    translation_closed_at: float = 0.0
    first_translation_at: float = 0.0
    max_chunk_gap: float = 0.0

    def source_text(self) -> str:
        return "".join(str(t.get("text") or "") for t in self.source_tokens)

    def translation_text(self) -> str:
        return "".join(str(t.get("text") or "") for t in self.translation_tokens)

    def translation_ends_sentence(self) -> bool:
        text = self.translation_text().rstrip()
        if not text:
            return False
        return sentence_segmentation.is_sentence_ender_at(text, len(text) - 1)


class SentencePairer:
    """Per-speaker FIFO pairing of source sentences and streamed translations."""

    def __init__(
        self,
        make_sentence_id: Callable[[], str],
        *,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._make_sentence_id = make_sentence_id
        self._now_override = now
        self._queues: dict[str, list[PairedSentence]] = {}

    def _now(self) -> float:
        # Resolved lazily so tests that monkeypatch time.monotonic control
        # the pairing clock too.
        if self._now_override is not None:
            return self._now_override()
        return time.monotonic()

    # ------------------------------------------------------------------ state

    def open_entry(self, speaker: str) -> Optional[PairedSentence]:
        """The newest entry whose source is still open, if any."""
        queue = self._queues.get(str(speaker)) or []
        if queue and not queue[-1].source_closed:
            return queue[-1]
        return None

    def entries(self, speaker: str) -> list[PairedSentence]:
        return list(self._queues.get(str(speaker)) or [])

    def speakers(self) -> list[str]:
        return [s for s, q in self._queues.items() if q]

    def has_open_source(self, speaker: str) -> bool:
        return self.open_entry(speaker) is not None

    def pending_count(self, speaker: str) -> int:
        return len(self._queues.get(str(speaker)) or [])

    # ---------------------------------------------------------------- routing

    def route_source_token(self, token: dict, speaker: str) -> PairedSentence:
        """Append a source token to the speaker's open sentence (opening one
        if needed) and stamp the sentence id on the token."""
        speaker = str(speaker)
        queue = self._queues.setdefault(speaker, [])
        if not queue or queue[-1].source_closed:
            queue.append(
                PairedSentence(sentence_id=self._make_sentence_id(), speaker=speaker)
            )
        entry = queue[-1]
        entry.source_tokens.append(token)
        token["llm_sentence_id"] = entry.sentence_id
        return entry

    def route_translation_token(
        self,
        token: dict,
        speaker: str,
        *,
        expected_language: Optional[str] = None,
    ) -> PairedSentence:
        """Route a translation token to the oldest entry awaiting translation
        and stamp that entry's sentence id on the token.

        `expected_language`: the token's language; entries whose expected
        target differs are skipped when a better match exists further along
        (two-way mode, where directions alternate).
        """
        speaker = str(speaker)
        now = self._now()
        queue = self._queues.setdefault(speaker, [])

        # Resync-on-arrival: if the oldest awaiting translation already looks
        # complete and this token arrives after a real gap, it starts the NEXT
        # sentence's translation — close the old one instead of appending.
        for entry in queue:
            if entry.translation_closed or not entry.expects_translation:
                continue
            if (
                entry.source_closed
                and entry.translation_tokens
                and entry.translation_ends_sentence()
                and now - entry.last_translation_at >= RESYNC_GAP_SECONDS
            ):
                entry.translation_closed = True
                entry.translation_close_reason = "resync_gap"
                entry.translation_closed_at = now
                continue
            break

        target = None
        for entry in queue:
            if entry.translation_closed or not entry.expects_translation:
                continue
            target = entry
            break

        if target is None:
            # No sentence is awaiting a translation (e.g. the source side has
            # not opened yet, or everything closed). Attach to the open entry
            # or a fresh one: dropping the token would lose displayed text.
            if not queue or queue[-1].source_closed:
                queue.append(
                    PairedSentence(
                        sentence_id=self._make_sentence_id(), speaker=speaker
                    )
                )
            target = queue[-1]

        if not target.translation_tokens:
            target.first_translation_at = now
        else:
            target.max_chunk_gap = max(
                target.max_chunk_gap, now - target.last_translation_at
            )
        target.translation_tokens.append(token)
        target.last_translation_at = now
        token["llm_sentence_id"] = target.sentence_id
        return target

    # ---------------------------------------------------------------- closing

    def close_source(
        self,
        speaker: str,
        *,
        reason: str = "punctuation",
        expects_translation: bool = True,
    ) -> Optional[PairedSentence]:
        """Close the source side of the speaker's open sentence. Returns the
        entry (or None if nothing was open)."""
        entry = self.open_entry(str(speaker))
        if entry is None:
            return None
        entry.source_closed = True
        entry.source_closed_at = self._now()
        entry.close_reason = reason
        entry.expects_translation = expects_translation and bool(
            entry.expects_translation
        )
        if not entry.expects_translation:
            entry.translation_closed = True
            entry.translation_close_reason = "no_translation_expected"
            entry.translation_closed_at = entry.source_closed_at
        return entry

    # --------------------------------------------------------------- collect

    def collect_completed(self, speaker: Optional[str] = None) -> list[PairedSentence]:
        """Apply time-based close rules, then pop and return every entry that
        is fully closed, oldest first. Call once per response batch (and the
        caller emits display separators / refine dispatches per entry)."""
        now = self._now()
        completed: list[PairedSentence] = []
        speakers = [str(speaker)] if speaker is not None else list(self._queues.keys())
        for spk in speakers:
            queue = self._queues.get(spk) or []
            while queue:
                head = queue[0]
                if not head.translation_closed and head.source_closed:
                    if head.translation_tokens:
                        if head.translation_ends_sentence():
                            # batch-end punctuation close
                            head.translation_closed = True
                            head.translation_close_reason = "punct"
                            head.translation_closed_at = now
                        elif now - head.last_translation_at >= QUIET_CLOSE_SECONDS:
                            head.translation_closed = True
                            head.translation_close_reason = "quiet"
                            head.translation_closed_at = now
                    elif now - head.source_closed_at >= MAX_WAIT_SECONDS:
                        head.translation_closed = True
                        head.translation_close_reason = "timeout_empty"
                        head.translation_closed_at = now
                if head.source_closed and head.translation_closed:
                    completed.append(queue.pop(0))
                    continue
                break
            if not queue:
                self._queues.pop(spk, None)
        return completed

    def flush_speaker(self, speaker: str, *, reason: str = "flush") -> list[PairedSentence]:
        """Force-complete every entry for the speaker (stream end, sleep,
        session stop). Open sources are closed as-is."""
        speaker = str(speaker)
        queue = self._queues.pop(speaker, [])
        for entry in queue:
            if not entry.source_closed:
                entry.source_closed = True
                entry.source_closed_at = self._now()
                entry.close_reason = reason
            if not entry.translation_closed:
                entry.translation_closed = True
                entry.translation_close_reason = "flush"
                entry.translation_closed_at = self._now()
        return queue

    def flush_all(self, *, reason: str = "flush") -> list[PairedSentence]:
        flushed: list[PairedSentence] = []
        for speaker in list(self._queues.keys()):
            flushed.extend(self.flush_speaker(speaker, reason=reason))
        return flushed

    def restore_entry(self, entry: PairedSentence) -> None:
        """Reopen a previously finalized sentence as the speaker's newest
        entry (interrupt repair: a retracted fragment merges back so the
        continuation appends to it)."""
        queue = self._queues.setdefault(str(entry.speaker), [])
        entry.source_closed = False
        entry.translation_closed = False
        queue.append(entry)
