"""
Soniox会话模块 - 管理与Soniox服务的WebSocket会话
"""
import json
import asyncio
import time
from dataclasses import dataclass, field

from websockets.sync.client import connect as sync_connect

import config
from config import (
    SONIOX_STREAM_DURATION_SECONDS,
    DEFAULT_SEGMENT_MODE,
    is_llm_refine_available,
    normalize_language_code,
)
from audio_router import AudioSendRouter
from stream_session_runtime import StreamState as _StreamState
from soniox_client import get_config
from soniox_audio import SonioxOpusWebSocket
from opus_audio import FRAME_SAMPLES
from osc_manager import osc_manager
from osc_draft import OscDraftPublisher
from realtime_session_base import RealtimeSessionBase
from interrupt_repair import InterruptRepairHistory, clone_tokens_for_merge
import sentence_segmentation
import llm_log
import soniox_response_log
from sentence_pairing import SentencePairer, PairedSentence

# 准确 (accurate) mode speculative translation: a sentence that is already
# complete in the not-yet-final text is sent to the LLM ahead of finalization,
# so the translation is ready (and shown as a preview) seconds earlier.
# Interim text rarely changes, so the first candidate fires IMMEDIATELY (zero
# added latency); the cooldown applies AFTER a fire — a revised reading that
# appears during the cooldown fires only once it expires and the reading is
# still present, so flip-flopping interim results can't spam requests. Results
# are cached so finalization reuses them without a second billed call.
SPEC_TRANSLATE_COOLDOWN_SECONDS = 1.0
SPEC_TRANSLATE_CACHE_MAX = 64
SPEC_TRANSLATE_MAX_INFLIGHT = 3


def _is_translation_token(token: dict) -> bool:
    return token.get("translation_status") == "translation"


@dataclass
class _InterleavedBatch:
    """Per-response state of the interleaved punctuation pass."""

    tokens: list[dict]
    # No translation is coming, so the source drives line breaks.
    source_as_output: bool
    # The response's non-final tokens: a closing quote already showing there
    # holds a batch-final sentence end.
    non_final: list[dict] = field(default_factory=list)
    outgoing: list[dict] = field(default_factory=list)
    last_real_speaker: str | None = None
    # Speakers whose display sentence was closed in this batch.
    finalized_speakers: set[str] = field(default_factory=set)

    @property
    def boundary_lookahead(self) -> list[dict]:
        """Non-final tail for PendingBoundaryState. With a translation stream
        coming, source punctuation only defers the break and the pending
        endpoint holds for the quote instead (_source_awaits_closing_quote)."""
        return self.non_final if self.source_as_output else []


class SonioxSession(RealtimeSessionBase):
    """Soniox会话管理器"""

    PROVIDER_NAME = "Soniox"
    CONFIG_PREFIX = "SONIOX"
    CLIENT_MODULE = "soniox_client"
    CHUNK_SIZE = FRAME_SAMPLES  # Capture one 40 ms Opus packet at a time.
    OSC_PREVIEW_BY_SPEAKER = True

    def __init__(self, logger, broadcast_callback):
        super().__init__(logger, broadcast_callback)
        self._segment_mode = DEFAULT_SEGMENT_MODE if DEFAULT_SEGMENT_MODE in ("translation", "endpoint", "punctuation") else "punctuation"
        # Speakers whose <end> fired before the sentence's translation was ready.
        # The line break is deferred until the translation arrives (or the next
        # sentence starts) so the late translation stays attached to this
        # sentence instead of merging into the next one. See the interleaved
        # punctuation pass in _process_stream_response.
        self._pending_endpoint_speakers: set[str] = set()
        # Order-based (source, translation) pairing for the LLM refine path.
        # Display separators above stay stream-positional; semantic pairing —
        # which translation belongs to which source sentence — lives here
        # exclusively. See sentence_pairing.py for the routing/close rules.
        self._pairer = SentencePairer(self._alloc_sentence_id)
        # Speculative translation state (see SPEC_TRANSLATE_* constants):
        # monotonic time of the last fire (post-fire cooldown), in-flight
        # text -> concurrent Future, and a small LRU text -> result.
        self._spec_last_fired_at = 0.0
        self._spec_inflight: dict[str, object] = {}
        self._spec_cache: dict[str, str] = {}
        # Finalized-sentence history for short-interrupt repair.
        self._interrupt_history = InterruptRepairHistory()

        # Two-way translation language pair (instance-level so it can be changed
        # at runtime from the UI without restarting the process).
        self.target_lang_1: str = normalize_language_code(config.TARGET_LANG_1) or "en"
        self.target_lang_2: str = normalize_language_code(config.TARGET_LANG_2) or "zh"

    # --- RealtimeSessionBase hooks ---

    def _clear_segmentation_state(self) -> None:
        super()._clear_segmentation_state()
        self._pending_endpoint_speakers.clear()
        self._pairer.flush_all()

    def _reset_run_state(self) -> None:
        self._spec_last_fired_at = 0.0
        self._spec_inflight.clear()
        self._spec_cache.clear()
        self._interrupt_history.clear()

    def _close_ws(self, ws, reason: str) -> None:
        ws.close(1000, reason)

    def _request_stream_finalize(self, ws) -> None:
        ws.send(json.dumps({"type": "finalize"}))

    def _warmup_response_ends_stream(self, stream: _StreamState, res) -> bool:
        if res.get("error_code") is not None:
            print(f"⚠️  Soniox warmup error {res.get('error_code')}: {res.get('error_message', '')}")
            return True
        return bool(res.get("finished"))

    def _two_way_pair(self) -> tuple[str, str]:
        return self.target_lang_1, self.target_lang_2

    def _live_osc_preview_suppressed(self) -> bool:
        # 准确 mode suppresses soniox's built-in translation, so there is no live
        # translation stream to preview here (the LLM handles it at finalize).
        # 混合 keeps the built-in translation and previews it live as usual.
        return self._suppress_soniox_translation

    def _on_llm_refine_mode_changed(self, mode: str) -> None:
        if mode == "translate" and self._segment_mode == "translation":
            # Force punctuation segmentation when LLM translate mode is enabled.
            self.set_segment_mode("punctuation")

    def set_translation_target_lang(self, lang: str) -> tuple[bool, str]:
        from config import normalize_language_code, is_supported_language_code

        normalized = normalize_language_code(lang)
        if not is_supported_language_code(normalized):
            return False, f"Unsupported translation target language: {lang}"

        previous = self.translation_target_lang
        self.translation_target_lang = normalized
        if previous != normalized:
            print(f"🌐 Translation target language updated: {previous} -> {normalized}")
        return True, "ok"

    def get_target_langs(self) -> tuple[str, str]:
        return (str(self.target_lang_1 or "en"), str(self.target_lang_2 or "zh"))

    def set_target_langs(self, lang_a: str, lang_b: str) -> tuple[bool, str]:
        """Set the two-way translation language pair (validated)."""
        from config import normalize_language_code, is_supported_language_code

        a = normalize_language_code(lang_a)
        b = normalize_language_code(lang_b)
        if not is_supported_language_code(a):
            return False, f"Unsupported language: {lang_a}"
        if not is_supported_language_code(b):
            return False, f"Unsupported language: {lang_b}"
        if a == b:
            return False, "Two-way translation requires two different languages"
        self.target_lang_1 = a
        self.target_lang_2 = b
        return True, "ok"

    def _alloc_sentence_id(self) -> str:
        self._llm_sentence_counter += 1
        return f"{self._llm_sentence_session_id}-{self._llm_sentence_counter}"

    def _process_token_for_sentence(self, token: dict, *, closes_quote: bool = False) -> None:
        """Route the token twice, for two independent concerns:

        1. SentencePairer — semantic (source, translation) pairing for the
           LLM refine path. Routing happens at arrival (before the outgoing
           copy is minified) so every final token carries the authoritative
           ``llm_sentence_id`` of the sentence it semantically belongs to,
           even when a translation streams in late.
        2. The display sentence buffer — a per-speaker accumulator that only
           feeds separator/boundary decisions (where the line break lands in
           the outgoing stream). It carries no sentence identity.

        `closes_quote`: this source token is the closing quote of the
        buffered sentence whose source already closed (see
        _close_pending_endpoint_before_source).
        """
        text = token.get("text", "")
        if self._is_internal_token(text):
            return

        speaker = str(token.get("speaker", "?"))
        translation_status = token.get("translation_status", "original")

        if translation_status == "translation":
            self._pairer.route_translation_token(
                token, speaker, expected_language=token.get("language")
            )
        elif closes_quote:
            self._continue_closed_source(token, speaker)
        else:
            self._pairer.route_source_token(token, speaker)

        if speaker not in self._sentence_buffers:
            self._sentence_buffers[speaker] = {
                "original_tokens": [],
                "translation_tokens": [],
            }

        buffer = self._sentence_buffers[speaker]

        if translation_status == "translation":
            buffer["translation_tokens"].append(token)
        else:
            buffer["original_tokens"].append(token)

    def _continue_closed_source(self, token: dict, speaker: str) -> None:
        """A closing quote finalized after its sentence's source closed
        belongs to that sentence, not the next one: join its paired entry
        while that still waits for its translation, and carry its id either
        way so the quote renders with the line it closes."""
        originals = (self._sentence_buffers.get(speaker) or {}).get("original_tokens") or []
        sentence_id = originals[-1].get("llm_sentence_id") if originals else None
        if not sentence_id:
            self._pairer.route_source_token(token, speaker)
            return
        if self._pairer.continue_closed_source(token, speaker, str(sentence_id)) is None:
            token["llm_sentence_id"] = sentence_id

    def _pairing_close_source(self, speaker: str, *, reason: str = "punctuation") -> None:
        """Close the source side of the speaker's open paired sentence at a
        display sentence boundary (punctuation / <end> / speaker change).

        Entries that complete right away (no translation expected, or the
        translation already closed) dispatch inline so their finalized
        snapshot exists before later tokens in the same batch consult it
        (the short-interrupt merge does)."""
        entry = self._pairer.open_entry(str(speaker))
        if entry is None:
            return
        self._pairer.close_source(
            str(speaker),
            reason=reason,
            expects_translation=self._expects_translation_stream(entry.source_tokens),
        )
        llm_log.log_event(
            "pairing_source_close",
            sentence_id=entry.sentence_id,
            reason=reason,
            source=self._join_token_texts(entry.source_tokens),
        )
        self._dispatch_paired_sentences(str(speaker))

    def _dispatch_paired_sentences(self, speaker: str | None = None) -> None:
        """Collect fully paired sentences and dispatch the LLM refine path.
        Called at source-close points and once per Soniox response batch so
        the time-based close rules (quiet close, timeout) make progress even
        without boundary tokens."""
        for entry in self._pairer.collect_completed(speaker):
            # Timing evidence for tuning the pairing close constants
            # (RESYNC_GAP / QUIET_CLOSE / MAX_WAIT in sentence_pairing.py):
            # wait_ms is the latency the close rules added after the source
            # ended; first_translation_lag_ms validates MAX_WAIT;
            # max_chunk_gap_ms bounds RESYNC_GAP / QUIET_CLOSE from below.
            llm_log.log_event(
                "pairing_close",
                sentence_id=entry.sentence_id,
                reason=entry.translation_close_reason,
                wait_ms=int(
                    max(0.0, entry.translation_closed_at - entry.source_closed_at) * 1000
                ),
                first_translation_lag_ms=(
                    int((entry.first_translation_at - entry.source_closed_at) * 1000)
                    if entry.first_translation_at
                    else None
                ),
                max_chunk_gap_ms=int(entry.max_chunk_gap * 1000),
                chunks=len(entry.translation_tokens),
                ends_punct=entry.translation_ends_sentence(),
                seeded=entry.translation_was_seeded or None,
                revived=entry.translation_was_revived or None,
            )
            self._interrupt_history.record(
                entry.speaker,
                entry.sentence_id,
                entry.source_tokens,
                entry.translation_tokens,
                is_internal_token=self._is_internal_token,
            )
            if not self.loop:
                continue
            asyncio.run_coroutine_threadsafe(
                self._finalize_sentence_async(
                    entry.speaker,
                    entry.source_tokens,
                    entry.translation_tokens,
                    entry.sentence_id,
                    # A revived entry already passed through finalize once
                    # (empty, at the timeout): skip one-shot side effects.
                    announce=not entry.translation_was_revived,
                ),
                self.loop,
            )

    def _translation_enabled(self) -> bool:
        """False when the session was started with translation "none" (翻译关闭)."""
        return (self.translation or "").strip().lower() not in ("", "none")

    def _expects_translation_stream(self, source_tokens: list[dict]) -> bool:
        """Whether soniox will stream a translation for this source text.

        False — so the source itself drives line breaks and semantic pairing —
        when there is no translation to wait for:
        - 准确 (accurate) mode: soniox's built-in translation is disabled and the
          LLM translates from the source.
        - translation off (翻译关闭): no translation stream exists, so a
          source boundary must break the line immediately instead of waiting
          for translation text that never comes.
        - the speech is already in the target language (built-in translation
          empty), the source-as-output case.
        """
        if self._suppress_soniox_translation:
            return False
        if not self._translation_enabled():
            return False
        return super()._expects_translation_stream(source_tokens)

    def _maybe_speculative_translate(self, non_final_tokens: list[dict]) -> None:
        """准确 mode: fire the LLM for sentences already complete in the
        not-yet-final text so the translation is ready (cached + previewed)
        before soniox finalizes the tokens. Runs on the recv thread."""
        if not self._suppress_soniox_translation:
            return
        if self._llm_refine_mode != "translate":
            return
        if not self._translation_enabled():
            return
        if not is_llm_refine_available() or not self.loop:
            return

        non_final_by_speaker: dict[str, list[dict]] = {}
        for token in non_final_tokens or []:
            text = token.get("text")
            if text is None or self._is_internal_token(text):
                continue
            if token.get("translation_status") == "translation":
                continue
            speaker = str(token.get("speaker", "?"))
            non_final_by_speaker.setdefault(speaker, []).append(token)

        now = time.monotonic()
        for speaker, tail in non_final_by_speaker.items():
            buffer = self._sentence_buffers.get(speaker) or {}
            tokens = list(buffer.get("original_tokens") or []) + tail
            # Already in the target language: finalize won't translate either.
            if self._can_use_source_as_translation(tokens):
                continue
            combined = self._join_token_texts(tokens)
            if not combined:
                continue
            target = (
                self._osc_translation_language_for_comparison(tokens)
                or normalize_language_code(self.get_translation_target_lang())
            )
            for segment in self._split_into_sentence_lines(combined):
                if not self._is_sentence_ending_punctuation(segment):
                    continue  # trailing partial: not a sentence yet
                key = segment.strip()
                if not key or key in self._spec_cache or key in self._spec_inflight:
                    continue
                # Post-fire cooldown: the first candidate fires instantly; a
                # revised reading appearing within the cooldown fires on a
                # later tick, once the cooldown expired and it is still present
                # (the wait doubles as its stability check).
                if (now - self._spec_last_fired_at) < SPEC_TRANSLATE_COOLDOWN_SECONDS:
                    continue
                if len(self._spec_inflight) >= SPEC_TRANSLATE_MAX_INFLIGHT:
                    continue
                self._spec_last_fired_at = now
                future = asyncio.run_coroutine_threadsafe(
                    self._spec_translate(key, target), self.loop
                )
                if not future.done():
                    self._spec_inflight[key] = future

    async def _spec_translate(self, text: str, target_lang: str) -> None:
        """One speculative LLM translation; broadcasts pending + result so the
        frontend can show a placeholder immediately and backfill it."""
        try:
            await self.broadcast_callback({
                "type": "spec_translation_pending",
                "source": text,
                "target_lang": target_lang,
            })
            result = await self._perform_translate(
                text, self._get_dynamic_context_items(), target_lang=target_lang
            )
            translated = ""
            if isinstance(result, dict) and result.get("status") == "ok":
                translated = (result.get("translation") or "").strip()
            if translated:
                self._spec_cache_put(text, translated)
                await self.broadcast_callback({
                    "type": "spec_translation",
                    "source": text,
                    "translation": translated,
                    "target_lang": target_lang,
                })
        except Exception as error:
            print(f"Speculative translate error: {error}")
        finally:
            self._spec_inflight.pop(text, None)

    def _spec_cache_put(self, key: str, value: str) -> None:
        cache = self._spec_cache
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > SPEC_TRANSLATE_CACHE_MAX:
            cache.pop(next(iter(cache)))

    async def _spec_take_for(self, source: str) -> str:
        """Speculative result for this exact source text, awaiting an in-flight
        call so finalization never issues a duplicate (double-billed) request."""
        key = (source or "").strip()
        if not key:
            return ""
        future = self._spec_inflight.get(key)
        if future is not None:
            try:
                await asyncio.wait_for(
                    asyncio.wrap_future(future),
                    timeout=float(config.llm_timeout_seconds()) * 3 + 5,
                )
            except Exception:
                pass
        return self._spec_cache.get(key) or ""

    def _batch_completes_another_sentence(
        self,
        tokens: list[dict],
        index: int,
        speaker: str,
    ) -> bool:
        """Whether a later token in this same final batch completes ANOTHER
        source sentence for this speaker, with nothing in between that would
        break the merge: a translation token (then the FIFO can pair each
        sentence normally), a speaker change, or an <end> (a real utterance
        boundary). Used to keep same-batch complete sentences in one paired
        entry — their translations arrive as one glued burst."""
        for later_index in range(index + 1, len(tokens)):
            later = tokens[later_index]
            text = later.get("text")
            if text is None:
                continue
            if text == "<end>":
                return False
            if self._is_internal_token(later):
                continue
            spk = later.get("speaker")
            if spk is not None and spk != "" and str(spk) != speaker:
                return False
            if later.get("translation_status") == "translation":
                return False
            if self._token_text_is_sentence_ending(tokens, later_index):
                return True
        return False

    def _open_source_in_unclosed_quote(self, speaker: str) -> bool:
        """Whether the speaker's open paired sentence is inside an unclosed
        「」-style quote. Deferred display boundaries (quote/period pendings)
        and translation-punctuation closes must not end such a sentence: the
        quotation is one unit, and closing it mid-quote paired live sentences
        against wrong translations (llm_20260712_083348 ids 30-39, 41)."""
        entry = self._pairer.open_entry(str(speaker))
        if entry is None or not entry.source_tokens:
            return False
        return sentence_segmentation.text_has_unclosed_quote(
            self._join_token_texts(entry.source_tokens)
        )

    def _close_display_sentence(
        self,
        speaker: str,
        outgoing_final_tokens: list,
        separator_type: str,
        finalized_speakers: set | None = None,
    ) -> bool:
        """Close the speaker's display sentence and emit a `separator_type`
        line break. False when the line cannot break yet (no output text)."""
        if not self._trigger_sentence_finalization(speaker):
            return False
        if finalized_speakers is not None:
            finalized_speakers.add(speaker)
        outgoing_final_tokens.append(
            self._minify_token(self._make_separator_token(separator_type))
        )
        return True

    def _close_at_deferred_boundary(
        self,
        speaker: str,
        outgoing_final_tokens: list,
        finalized_speakers: set,
    ) -> None:
        """A boundary that waited for one more token (quote / abbreviation /
        decimal) turned out to be real: close the sentence here, or defer the
        line break until its output text arrives."""
        if speaker in finalized_speakers:
            return
        if self._open_source_in_unclosed_quote(speaker):
            return
        self._pairing_close_source(speaker)
        if not self._close_display_sentence(
            speaker, outgoing_final_tokens, "punctuation", finalized_speakers
        ):
            self._pending_endpoint_speakers.add(speaker)

    def _flush_pending_boundary(
        self,
        token: dict,
        outgoing_final_tokens: list,
        finalized_speakers: set,
    ) -> None:
        if not self._pending_boundaries.flush_before_token(token):
            return
        self._close_at_deferred_boundary(
            str(token.get("speaker", "?")), outgoing_final_tokens, finalized_speakers
        )

    def _flush_stale_quote_boundaries(
        self,
        token: dict,
        outgoing_final_tokens: list,
        finalized_speakers: set,
    ) -> None:
        for speaker in self._pending_boundaries.flush_stale_quote_boundaries_before_incompatible_token(token):
            self._close_at_deferred_boundary(speaker, outgoing_final_tokens, finalized_speakers)

    def _finalize_superseded_speakers(
        self,
        current_speaker: str,
        outgoing_final_tokens: list,
        finalized_speakers: set,
    ) -> None:
        """A different speaker just started talking, so the previous speaker's
        sentence is over. Close out every OTHER speaker's still-open buffer now
        (interleaved punctuation mode): this triggers the LLM refine/translate
        instead of leaving that sentence stuck as a provisional (gray) line
        until its own punctuation/endpoint eventually shows up.

        If the previous speaker's translation hasn't streamed in yet the finalize
        is deferred via _pending_endpoint_speakers, so the pending-speaker pass
        finalizes it as soon as the translation arrives.
        """
        if not current_speaker:
            return
        for other in list(self._sentence_buffers.keys()):
            if other == current_speaker or other in finalized_speakers:
                continue
            self._pairing_close_source(other, reason="speaker_change")
            if not self._close_display_sentence(
                other, outgoing_final_tokens, "speaker_change", finalized_speakers
            ):
                # Translation not ready yet: defer so it finalizes once the
                # translation tokens arrive (see the pending-speaker pass).
                self._clear_pending_boundaries_for_speaker(other)
                self._pending_endpoint_speakers.add(other)

    def _maybe_merge_interrupted_sentence(
        self,
        current_speaker: str,
        token: dict,
        outgoing_final_tokens: list,
    ) -> str | None:
        if token.get("translation_status") == "translation" or self._is_internal_token(token):
            return None
        try:
            current_start_ms = float(token["start_ms"])
        except (KeyError, TypeError, ValueError):
            return None

        match = self._interrupt_history.find_merge_pair(
            current_speaker,
            current_start_ms,
            config=config,
            join_text=self._join_token_texts,
        )
        if not match:
            return None

        previous, _interrupt = match
        self._interrupt_history.retract(previous)
        self._refine_context_history = [
            item for item in self._refine_context_history
            if item.get("sentence_id") != previous.sentence_id
        ]
        merged_sentence_id = self._alloc_sentence_id()
        restored_original = clone_tokens_for_merge(previous.original_tokens, merged_sentence_id)
        restored_translation = clone_tokens_for_merge(previous.translation_tokens, merged_sentence_id)
        self._sentence_buffers[str(current_speaker)] = {
            "original_tokens": restored_original,
            "translation_tokens": restored_translation,
            "interrupt_repair_of": previous.sentence_id,
        }
        # Reopen the merged sentence in the pairer so the continuation's
        # source tokens append to it and any translation still streaming for
        # the retracted fragment routes back into the merged sentence.
        # Independent copies: the display buffer above appends future tokens
        # to its lists; sharing them would double-append via the pairer.
        self._pairer.restore_entry(
            PairedSentence(
                sentence_id=merged_sentence_id,
                speaker=str(current_speaker),
                source_tokens=list(restored_original),
                translation_tokens=list(restored_translation),
            )
        )
        self._pending_endpoint_speakers.discard(str(current_speaker))
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({
                    "type": "subtitle_retract",
                    "sentence_id": previous.sentence_id,
                    "replacement_sentence_id": merged_sentence_id,
                    "reason": "short_interrupt",
                }),
                self.loop,
            )
        outgoing_final_tokens.append(self._minify_token(self._make_separator_token("interrupt_repair")))
        for restored in restored_original + restored_translation:
            outgoing_final_tokens.append(self._minify_token(restored, is_final=True))
        return merged_sentence_id

    def _maybe_merge_from_non_final_tokens(
        self,
        non_final_tokens: list[dict],
        outgoing_final_tokens: list[dict],
    ) -> str | None:
        """Repair as soon as the resumed speaker's first interim source arrives.

        The continuation itself remains non-final for display. Restoring A1 here
        makes the same update frame render A1+A2 immediately and seeds the
        pairer before speculative translation inspects the live source.
        """
        for token in non_final_tokens or []:
            if token.get("translation_status") == "translation":
                continue
            speaker = token.get("speaker")
            if speaker is None or speaker == "":
                continue
            merged_sentence_id = self._maybe_merge_interrupted_sentence(
                str(speaker), token, outgoing_final_tokens
            )
            if merged_sentence_id:
                return merged_sentence_id
        return None

    def _display_sentence_has_output(self, buffer: dict) -> bool:
        """A display line may only break once its output text has started:
        some translation streamed in, or the source itself is the output."""
        if buffer.get("translation_tokens"):
            return True
        return not self._expects_translation_stream(buffer.get("original_tokens") or [])

    def _trigger_sentence_finalization(self, speaker: str) -> bool:
        """Close the speaker's DISPLAY sentence: decides whether a separator
        (line break) may be emitted at this point in the outgoing stream.

        Semantic pairing and the LLM refine dispatch are NOT handled here —
        the SentencePairer owns those (see _dispatch_paired_sentences). This
        keeps the old readiness rule ("wait until some translation text has
        streamed in") purely as a display heuristic: it places the line break
        after the translation text the viewer is watching, and being fooled
        by a late previous-sentence translation no longer corrupts the refine
        pairs."""
        buffer = self._sentence_buffers.get(speaker)
        if not buffer:
            return False

        if not buffer.get("original_tokens"):
            return False

        if not self._display_sentence_has_output(buffer):
            return False

        self._sentence_buffers.pop(speaker, None)
        self._pending_endpoint_speakers.discard(speaker)
        self._clear_pending_boundaries_for_speaker(speaker)
        self._reset_osc_live_state(speaker)
        return True

    async def _finalize_sentence_async(
        self,
        speaker: str,
        original_tokens: list,
        translation_tokens: list,
        sentence_id: str | None = None,
        announce: bool = True,
    ):
        """异步执行 LLM 改进与 OSC 发送"""
        source = self._join_token_texts(original_tokens)
        translation = self._join_token_texts(translation_tokens)

        if not source:
            return
        if self._interrupt_history.is_retracted(sentence_id):
            return

        if announce:
            self._broadcast_foreign_speech(source, original_tokens)

        fallback_to_source = not translation and self._can_use_source_as_translation(original_tokens)
        display_translation = translation if translation else (source if fallback_to_source else "")

        mode = self.get_llm_refine_mode()
        llm_can_run = is_llm_refine_available() and mode != "off"
        # 混合 (refine) improves the built-in draft, so it needs one; 准确
        # (translate) works from the source alone and runs even with soniox
        # translation disabled.
        if mode == "refine" and not translation:
            llm_can_run = False
        # Source already in the target language: nothing to translate — skip the
        # LLM call (saves credits, avoids echoing the same text as a translation).
        if mode == "translate" and fallback_to_source:
            llm_can_run = False
        # 翻译「关闭」: the user turned translation off, so 准确 must not
        # translate through the LLM either (soniox translation is already off).
        if not self._translation_enabled():
            llm_can_run = False
        will_llm_translate = llm_can_run and mode == "translate"

        # In accurate mode there is no built-in translation to show, so only bail
        # when nothing at all will be produced.
        if not display_translation and not will_llm_translate:
            llm_log.log_event(
                "refine_dispatch_skipped",
                sentence_id=sentence_id,
                mode=mode,
                reason="no display translation and no llm translate",
                source=source,
            )
            return

        # Correlates with refine_broadcast below: a dispatch with no matching
        # broadcast means this coroutine died (cancelled/crashed) before
        # delivering its refine_result — the frontend line would stay gray.
        llm_log.log_event(
            "refine_dispatch",
            sentence_id=sentence_id,
            mode=mode,
            llm_can_run=llm_can_run,
            source=source,
            draft=display_translation,
        )

        context_items = self._get_dynamic_context_items()

        # Per-utterance target: the two-way partner of the detected source, or
        # the session target otherwise. Drives both the LLM prompt and the
        # language tag on the frontend's synthesized translation line.
        utterance_target_lang = (
            self._osc_translation_language_for_comparison(original_tokens)
            or normalize_language_code(self.get_translation_target_lang())
        )

        # 混合: push the fast STT draft to the VRChat chatbox immediately (before
        # the LLM), so viewers never wait on refine latency; a high/critical refine
        # replaces that same line afterwards. 准确 has no draft (soniox translation
        # is off), so it publishes once, after the LLM.
        osc_pub = OscDraftPublisher(
            osc_manager,
            enabled=self.get_osc_translation_enabled(),
            wants_draft=(mode != "translate"),
            speaker=speaker,
        )
        if display_translation:
            osc_pub.send_draft(self._select_osc_text(display_translation, source, original_tokens))

        refined_translation = display_translation
        no_change = True

        if llm_can_run:
            try:
                if mode == "refine":
                    refine_kwargs = self._target_lang_kwargs(self._perform_refine, utterance_target_lang)

                    result = await self._perform_refine(
                        source, translation, context_items, **refine_kwargs
                    )
                    if result.get("status") == "ok" and not result.get("no_change"):
                        refined_translation = result.get("refined_translation") or translation
                        no_change = False
                elif mode == "translate":
                    # A speculative call may already have translated this exact
                    # sentence from the non-final text; reuse it (awaiting an
                    # in-flight one) instead of paying for a second request.
                    speculative = await self._spec_take_for(source)
                    if speculative:
                        refined_translation = speculative
                        no_change = False
                    else:
                        # 准确 mode: no speculative result to show — put the
                        # translation-line placeholder up right away while this
                        # finalize-path LLM call runs (refine_result clears it).
                        if self._suppress_soniox_translation:
                            await self.broadcast_callback({
                                "type": "spec_translation_pending",
                                "source": source,
                                "target_lang": utterance_target_lang,
                            })
                        result = await self._perform_translate(source, context_items, target_lang=utterance_target_lang)
                        if result.get("status") == "ok":
                            translated = (result.get("translation") or "").strip()
                            refined_translation = translated if translated else translation
                            no_change = False
            except Exception as error:
                if mode == "translate":
                    refined_translation = translation
                    no_change = False
                print(f"LLM refine error: {error}")

        if self._interrupt_history.is_retracted(sentence_id):
            llm_log.log_event(
                "refine_dropped_retracted",
                sentence_id=sentence_id,
                source=source,
            )
            return

        await self.broadcast_callback({
            "type": "refine_result",
            "sentence_id": sentence_id,
            "source": source,
            "original_translation": display_translation,
            "refined_translation": refined_translation if not no_change else None,
            "no_change": no_change,
            # 准确 mode has no translation tokens carrying a language; tell the
            # frontend which language the synthesized translation line is in.
            "target_lang": utterance_target_lang,
        })

        llm_log.log_event(
            "refine_broadcast",
            sentence_id=sentence_id,
            no_change=no_change,
            refined=refined_translation if not no_change else "",
        )

        osc_pub.publish_final(
            self._select_osc_text(refined_translation, source, original_tokens),
            no_change=no_change,
        )

        self._remember_refine_context({
            "sentence_id": sentence_id,
            "source": source,
            "translation": refined_translation,
        })

    def set_translation_mode(self, mode: str) -> tuple[bool, str, bool]:
        """Apply the unified 翻译模式 (fast/accurate/hybrid/refine).

        Maps onto the internal LLM mode + soniox-translation suppression. Returns
        (ok, normalized_mode, needs_restart) — needs_restart is True when the
        soniox stream must be reopened because its translation on/off changed.
        """
        normalized = str(mode or "").strip().lower()
        # 混合 (hybrid) now shows the STT draft immediately and refines it in place,
        # so it maps onto the internal refine mode. "refine" is accepted as a
        # legacy alias (old stored prefs / clients) and normalized to hybrid.
        if normalized == "refine":
            normalized = "hybrid"
        mapping = {
            "fast": ("off", False),
            "accurate": ("translate", True),
            "hybrid": ("refine", False),
        }
        if normalized not in mapping:
            return False, f"Invalid translation mode: {mode}", False
        llm_mode, suppress = mapping[normalized]
        prev_suppress = self._suppress_soniox_translation
        self._llm_refine_mode = llm_mode
        self._suppress_soniox_translation = bool(suppress)
        if suppress and self._segment_mode == "translation":
            # 准确 mode has no soniox translation stream to segment on. Left in
            # "translation" segment mode the pairer closes once per BATCH, so
            # every complete sentence a batch carries glues into one entry
            # (live 2026-07-12, llm_20260712_080912: four sentences dispatched
            # as one blob, re-translated despite per-sentence spec results).
            # set_llm_refine_mode has the same guard, but this path assigns
            # the mode directly and used to bypass it.
            self.set_segment_mode("punctuation")
        # Re-selecting an LLM mode clears any prepaid-exhausted lock from before.
        if llm_mode != "off":
            self._hosted_llm.reset()
        needs_restart = bool(suppress) != bool(prev_suppress)
        return True, normalized, needs_restart

    def get_translation_mode(self) -> str:
        if self._suppress_soniox_translation:
            return "accurate"
        if self._llm_refine_mode == "refine":
            return "hybrid"
        return "fast"

    def set_segment_mode(self, mode: str) -> tuple[bool, str]:
        """设置断句模式并广播给所有前端"""
        if mode not in ("translation", "endpoint", "punctuation"):
            return False, f"Invalid segment mode: {mode}"

        # 准确 mode disables soniox's built-in translation, so there is no
        # translation stream to segment on. 混合 keeps it, so it is allowed.
        if mode == "translation" and self._suppress_soniox_translation:
            return False, "Segment mode 'translation' is disabled in 准确 (accurate) mode"

        self._segment_mode = mode
        self._sentence_buffers.clear()
        self._pending_endpoint_speakers.clear()
        self._pairer.flush_all()
        self._pending_boundaries.clear()
        self._reset_osc_live_state()
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_callback({
                    "type": "segment_mode_changed",
                    "mode": mode,
                }),
                self.loop,
            )
        return True, "ok"

    def get_segment_mode(self) -> str:
        return self._segment_mode

    def _stream_rollover_seconds(self) -> float | None:
        if config.RELAY_MODE:
            return None
        if not config.SONIOX_USES_TEMP_API_KEY:
            return None
        if SONIOX_STREAM_DURATION_SECONDS is None:
            return None
        try:
            value = float(SONIOX_STREAM_DURATION_SECONDS)
        except Exception:
            return None
        if value <= 0:
            return None
        return value

    def _open_stream_state(
        self,
        api_key: str,
        stream_index: int,
        audio_format: str,
        translation: str,
        translation_target_lang: str,
        *,
        warming: bool = False,
    ) -> _StreamState:
        # Disable Soniox's built-in translation for any STT-only stream: either
        # the user selected no translation, or accurate mode lets the LLM handle it.
        effective_translation = "none" if self._suppress_soniox_translation else translation
        stream_config = get_config(
            api_key,
            "ogg",
            effective_translation,
            translation_target_lang=translation_target_lang,
            target_lang_1=self.target_lang_1,
            target_lang_2=self.target_lang_2,
        )
        label = f"stream #{stream_index}"
        purpose = " warmup" if warming else ""
        # Read dynamically so a runtime region / relay switch takes effect on the
        # next stream.
        if config.RELAY_MODE:
            # Hosted mode: connect through the subtitle-server relay. The relay
            # injects its own upstream key. The account token is exchanged with
            # the server for a short-lived backend ticket before opening the WS.
            stream_config["api_key"] = ""
            relay_info = config.relay_connect_info(
                "soniox",
                model=stream_config.get("model"),
                translation="none" if effective_translation == "none" else None,
                run_id=self.relay_run_id,
            )
            relay_headers = relay_info.get("headers") or {}
            connect_kwargs = {}
            if relay_headers:
                connect_kwargs["additional_headers"] = relay_headers
            print(f"Connecting to relay Soniox ({label}{purpose})...")
            ws = sync_connect(relay_info["url"], **connect_kwargs)
        else:
            print(f"Connecting to Soniox ({label}{purpose})...")
            ws = sync_connect(config.SONIOX_WEBSOCKET_URL)
        try:
            ws.send(json.dumps(stream_config))
            ws = SonioxOpusWebSocket(ws, sample_rate=self.sample_rate)
        except Exception:
            ws.close()
            raise
        state = _StreamState(
            ws=ws,
            index=stream_index,
            api_key=api_key,
            started_at=time.monotonic(),
            ready_at=time.monotonic(),
            all_final_tokens=[],
        )
        print(f"Session started ({label}{purpose}).")
        return state

    def _split_final_token(self, token: dict) -> list[dict]:
        """One token per sentence piece for a final token whose text spans a
        sentence boundary. Internal tokens (<end> etc.) and single-piece texts
        pass through unchanged; split pieces are clones sharing the original's
        metadata, and their texts concatenate back to the original exactly."""
        text = str(token.get("text") or "")
        if self._is_internal_token(text):
            return [token]
        pieces = sentence_segmentation.split_text_at_sentence_boundaries(text)
        if len(pieces) <= 1:
            return [token]
        clones = []
        for piece in pieces:
            clone = dict(token)
            clone["text"] = piece
            clones.append(clone)
        return clones

    def _process_interleaved_punctuation_tokens(
        self,
        new_final_tokens: list[dict],
        non_final_tokens: list[dict] | None = None,
    ) -> tuple[list[dict], str | None, set[str]]:
        """Walk one response's final tokens in order, building the outgoing
        stream and inserting each line break exactly where its sentence ends.

        Returns (outgoing final tokens, last speaker seen, speakers whose
        display sentence was closed in this batch)."""
        batch = _InterleavedBatch(
            tokens=new_final_tokens,
            source_as_output=not self._expects_translation_stream(new_final_tokens),
            non_final=list(non_final_tokens or []),
        )
        for index, token in enumerate(new_final_tokens):
            if token.get("text") is not None:
                self._process_interleaved_token(batch, index, token)
        self._flush_ready_pending_endpoints(batch)
        return batch.outgoing, batch.last_real_speaker, batch.finalized_speakers

    def _process_interleaved_token(self, batch: "_InterleavedBatch", index: int, token: dict) -> None:
        text = token.get("text")
        is_internal = self._is_internal_token(token)
        is_translation = token.get("translation_status") == "translation"
        # Before any flush below can pop this speaker's buffer.
        context_text = "" if is_internal else self._sentence_context_text_for_token(token)

        closes_quote = False
        if not is_internal and not is_translation:
            closes_quote = self._close_pending_endpoint_before_source(batch, token)

        # After the flush above: the check reads the (possibly reset) buffer.
        has_sentence_punctuation = (
            not is_internal
            and self._token_text_is_sentence_ending(
                batch.tokens,
                index,
                source_as_output=batch.source_as_output,
                lookahead_tokens=batch.boundary_lookahead,
            )
        )
        token_speaker = None
        if not is_internal:
            token_speaker = self._token_speaker(token)
            self._handle_speaker_turn(batch, token, token_speaker, is_translation)

        self._process_token_for_sentence(token, closes_quote=closes_quote)
        if is_translation and has_sentence_punctuation and token_speaker:
            self._close_pairing_at_translation_punctuation(token, token_speaker)

        if not is_internal:
            batch.outgoing.append(self._minify_token(token, is_final=True))
            if token_speaker:
                batch.last_real_speaker = token_speaker
            self._pending_boundaries.mark_after_token(
                batch.tokens,
                index,
                context_text=context_text,
                is_internal_token=self._is_internal_token,
                source_as_output=batch.source_as_output,
                lookahead_tokens=batch.boundary_lookahead,
            )

        if text == "<end>" or has_sentence_punctuation:
            self._handle_sentence_boundary(
                batch,
                index,
                token,
                is_translation=is_translation,
                has_sentence_punctuation=has_sentence_punctuation,
            )

    def _close_pending_endpoint_before_source(self, batch: "_InterleavedBatch", token: dict) -> bool:
        """A pending endpoint means an earlier boundary fired before that
        sentence's translation had streamed in, so the line break was deferred
        (see _handle_sentence_boundary). The first *original* token of the next
        sentence is the signal that the previous sentence — together with
        whatever translation has since been buffered — is complete: finalize it
        now and break the line before this new token, so the late translation
        stays attached to the previous sentence instead of merging into this
        one.

        Returns True when `token` is instead the closing quote of that
        sentence (``" gallon."`` ended the previous batch, ``"`` starts this
        one): it belongs to the previous sentence, so the endpoint stays
        pending and lands before the next original token."""
        if not self._pending_endpoint_speakers:
            return False
        speaker = self._token_speaker(token) or batch.last_real_speaker
        if speaker is None and len(self._pending_endpoint_speakers) == 1:
            speaker = next(iter(self._pending_endpoint_speakers))
        if speaker not in self._pending_endpoint_speakers:
            return False
        if self._source_ends_before_closing_quote(speaker, str(token.get("text") or "")):
            return True
        self._pending_endpoint_speakers.discard(speaker)
        self._close_display_sentence(speaker, batch.outgoing, "endpoint", batch.finalized_speakers)
        return False

    def _source_ends_before_closing_quote(self, speaker: str, next_text: str) -> bool:
        """Whether `next_text` starts with the closing quote of the speaker's
        buffered display sentence, which already ends in sentence punctuation."""
        buffer = self._sentence_buffers.get(speaker) or {}
        context = self._join_token_texts(buffer.get("original_tokens") or [])
        return sentence_segmentation.has_sentence_ending_punctuation(
            context
        ) and sentence_segmentation.token_text_starts_with_closing_quote(context, next_text)

    def _source_awaits_closing_quote(self, batch: "_InterleavedBatch", speaker: str) -> bool:
        """Whether the response's non-final tail already shows the closing
        quote of the speaker's finished source sentence. Its deferred line
        break then holds until that quote is final, or the quote would land on
        the next line (same evidence PendingBoundaryState uses when the source
        drives line breaks)."""
        for token in batch.non_final:
            if self._is_internal_token(token) or _is_translation_token(token):
                continue
            if (self._token_speaker(token) or speaker) != speaker:
                continue
            return self._source_ends_before_closing_quote(speaker, str(token.get("text") or ""))
        return False

    def _handle_speaker_turn(
        self,
        batch: "_InterleavedBatch",
        token: dict,
        token_speaker: str | None,
        is_translation: bool,
    ) -> None:
        if not token_speaker:
            return
        # A new speaker's token means the previous speaker is done: finalize
        # their still-open sentence (triggers the LLM) before this speaker's
        # token joins the outgoing stream.
        if token_speaker != batch.last_real_speaker:
            self._finalize_superseded_speakers(token_speaker, batch.outgoing, batch.finalized_speakers)
            if not is_translation:
                self._maybe_merge_interrupted_sentence(token_speaker, token, batch.outgoing)
        self._flush_stale_quote_boundaries(token, batch.outgoing, batch.finalized_speakers)
        self._flush_pending_boundary(token, batch.outgoing, batch.finalized_speakers)

    def _close_pairing_at_translation_punctuation(self, token: dict, speaker: str) -> None:
        """Translation punctuation already closes the DISPLAY sentence. Keep
        the semantic pairer aligned with that boundary too, otherwise the next
        source sentence inherits the same llm_sentence_id and one refine result
        is rendered under both display blocks. Only close when this translation
        belongs to the currently open source; a late translation for an older
        FIFO entry must never close a newer sentence."""
        open_entry = self._pairer.open_entry(speaker)
        token_sentence_id = token.get("llm_sentence_id")
        if (
            open_entry is not None
            and token_sentence_id
            and open_entry.sentence_id == str(token_sentence_id)
            and open_entry.source_tokens
            # A source still inside an unclosed 「」-style quote is not done
            # even if its translation-so-far ends a sentence: the quotation's
            # remaining source (and its translation tail) still belongs to
            # this entry.
            and not self._open_source_in_unclosed_quote(speaker)
        ):
            self._pairing_close_source(speaker, reason="translation_punctuation")

    def _handle_sentence_boundary(
        self,
        batch: "_InterleavedBatch",
        index: int,
        token: dict,
        *,
        is_translation: bool,
        has_sentence_punctuation: bool,
    ) -> None:
        """Sentence-ending punctuation or <end> at `token`."""
        is_endpoint = token.get("text") == "<end>"
        speaker = self._token_speaker(token) or batch.last_real_speaker
        if speaker is None and self._sentence_buffers:
            speaker = next(iter(self._sentence_buffers.keys()))
        if not speaker:
            return
        # Source punctuation while a translation is still expected: the line
        # break waits until translation text shows up.
        defer_source_punctuation = (
            has_sentence_punctuation
            and not is_translation
            and not batch.source_as_output
        )
        # Two source sentences finalized in ONE batch usually get ONE glued
        # zero-gap translation burst, which no time-based rule can split (live
        # 2026-07-11, llm_20260711_210847: the first sentence swallowed both
        # translations and every later pair shifted). The display already
        # keeps them on one line (the break defers until translation text
        # arrives), so keep the semantic sentence together too: skip this
        # close and let the batch's LAST complete sentence close the merged
        # entry. The refine LLM then sees both sentences with both
        # translations.
        if defer_source_punctuation and self._batch_completes_another_sentence(
            batch.tokens, index, speaker
        ):
            return
        # A source-side boundary (source punctuation or <end>) ends the
        # sentence semantically right here, even when the display line break
        # is deferred until translation text shows up.
        if not is_translation:
            self._pairing_close_source(
                speaker,
                reason="endpoint" if is_endpoint else "punctuation",
            )
        if defer_source_punctuation:
            self._pending_endpoint_speakers.add(speaker)
            return
        # The source sentence ended but its closing quote is still non-final:
        # keep the break pending so the quote lands on this line.
        if (
            is_translation
            and speaker in self._pending_endpoint_speakers
            and self._source_awaits_closing_quote(batch, speaker)
        ):
            return
        if not self._close_display_sentence(
            speaker, batch.outgoing, "punctuation", batch.finalized_speakers
        ):
            # The source/endpoint says the sentence ended, but the translation
            # may not have arrived yet. Defer the line break: finalize as soon
            # as this batch (or a later batch) has a translation, or at the
            # latest before the next source sentence starts.
            self._pending_endpoint_speakers.add(speaker)

    def _flush_ready_pending_endpoints(self, batch: "_InterleavedBatch") -> None:
        """Break deferred lines whose output text arrived later in this batch."""
        for speaker in list(self._pending_endpoint_speakers):
            if speaker in batch.finalized_speakers:
                continue
            buffer = self._sentence_buffers.get(speaker)
            if not buffer:
                self._pending_endpoint_speakers.discard(speaker)
                continue
            if not self._display_sentence_has_output(buffer):
                continue
            if self._source_awaits_closing_quote(batch, speaker):
                continue
            self._pending_endpoint_speakers.discard(speaker)
            self._close_display_sentence(speaker, batch.outgoing, "punctuation", batch.finalized_speakers)

    def _process_non_interleaved_tokens(
        self,
        new_final_tokens: list[dict],
        endpoint_detected: bool,
    ) -> tuple[list[dict], list[dict]]:
        """Translation / endpoint segment modes: at most one line break, after
        the whole batch."""
        outgoing_final_tokens: list[dict] = []
        separator_tokens: list[dict] = []
        for token in new_final_tokens:
            if token.get("text") is None:
                continue
            self._process_token_for_sentence(token)
        if self._segment_mode == "translation":
            translation_hit = any(
                t.get("text") is not None
                and not self._is_internal_token(t)
                and t.get("translation_status") == "translation"
                for t in new_final_tokens
            )
            # Same-language speech produces no translation tokens; fall back
            # to sentence-ending punctuation in the source (used as output)
            # so it still segments instead of growing without bound.
            if not translation_hit and not self._expects_translation_stream(new_final_tokens):
                translation_hit = any(
                    t.get("text")
                    and not self._is_internal_token(t)
                    and t.get("translation_status") != "translation"
                    and self._token_text_is_sentence_ending(
                        new_final_tokens,
                        index,
                        source_as_output=True,
                    )
                    for index, t in enumerate(new_final_tokens)
                )
            if translation_hit:
                speaker_value = (
                    self._last_token_speaker(new_final_tokens, _is_translation_token)
                    or self._last_token_speaker(new_final_tokens)
                    or self._any_buffered_speaker()
                )
                if speaker_value:
                    self._pairing_close_source(speaker_value)
                    self._trigger_sentence_finalization(speaker_value)
                separator_tokens.append(self._make_separator_token("translation"))

        elif self._segment_mode == "endpoint":
            endpoint_hit = endpoint_detected or any(
                t.get("text") == "<end>"
                for t in new_final_tokens
            )
            if endpoint_hit:
                speaker_value = (
                    self._last_token_speaker(new_final_tokens)
                    or self._any_buffered_speaker()
                )
                if speaker_value:
                    self._pairing_close_source(speaker_value, reason="endpoint")
                    self._trigger_sentence_finalization(speaker_value)
                separator_tokens.append(self._make_separator_token("endpoint"))
        for token in new_final_tokens:
            if token.get("text") is None:
                continue
            if not self._is_internal_token(token):
                outgoing_final_tokens.append(self._minify_token(token, is_final=True))
        return outgoing_final_tokens, separator_tokens

    def _process_stream_response(
        self,
        res: dict,
        all_final_tokens: list[dict],
        sent_count: int,
        loop: asyncio.AbstractEventLoop,
    ) -> tuple[int, bool, str | None]:
        """Process one Soniox response. Returns sent count, should end stream, reason."""
        soniox_response_log.log_response(
            res,
            segment_mode=self._segment_mode,
            translation_mode=self.get_translation_mode(),
            llm_refine_mode=self._llm_refine_mode,
            suppress_soniox_translation=bool(self._suppress_soniox_translation),
            # Session translation config, so a recording replays faithfully
            # (tools/segmentation_replay.py) without inferring it.
            translation=self.translation,
            translation_target_lang=self.get_translation_target_lang(),
            target_langs=list(self.get_target_langs()),
            sent_count=sent_count,
            all_final_token_count=len(all_final_tokens),
            stream_key=f"{id(all_final_tokens):x}",
        )
        if res.get("error_code") is not None:
            message = res.get("error_message", "")
            reason = f"server error {res['error_code']}: {message}"
            print(f"Error: {res['error_code']} - {message}")
            return sent_count, True, reason

        # Parse tokens from current response.
        non_final_tokens: list[dict] = []

        for token in res.get("tokens", []):
            text = token.get("text")
            if text:
                if token.get("is_final"):
                    # Final tokens累积添加. A final token can carry a sentence
                    # boundary INSIDE its text ("...in Japanese. It's") — the
                    # interleaved boundary scan only looks at token ends, so an
                    # unsplit token glues two sentences into one pairing entry
                    # (live 2026-07-12, llm_20260712_083058). Pre-split it into
                    # one token per sentence piece (exact text, no trimming).
                    all_final_tokens.extend(self._split_final_token(token))
                elif not self._is_internal_token(text):
                    # Non-final tokens每次重置
                    non_final_tokens.append(token)

        # 计算新增的final tokens（增量部分）
        new_final_tokens = all_final_tokens[sent_count:]
        endpoint_detected = bool(res.get("endpoint_detected", False))

        separator_tokens: list[dict] = []
        outgoing_final_tokens: list[dict] = []

        # In punctuation mode the line break must land at the position of the
        # ending punctuation, not at the end of the batch. Soniox sometimes
        # confirms the period together with the words that follow it in a single
        # batch (e.g. "…的。而你是法官"); appending one separator after the whole
        # batch would leave the next sentence stuck on the previous line. So we
        # walk the tokens in order, building the outgoing stream and finalizing /
        # inserting a separator exactly when we cross an ending punctuation.
        interleaved_punctuation = (self._segment_mode == "punctuation")
        last_real_speaker = None
        finalized_speakers: set[str] = set()

        if interleaved_punctuation:
            (
                outgoing_final_tokens,
                last_real_speaker,
                finalized_speakers,
            ) = self._process_interleaved_punctuation_tokens(new_final_tokens, non_final_tokens)

        if interleaved_punctuation and endpoint_detected:
            speaker_value = last_real_speaker
            if speaker_value is None and len(self._sentence_buffers) == 1:
                speaker_value = next(iter(self._sentence_buffers.keys()))
            if speaker_value and speaker_value not in finalized_speakers:
                self._pairing_close_source(speaker_value, reason="endpoint")
                if not self._close_display_sentence(speaker_value, outgoing_final_tokens, "endpoint"):
                    self._pending_endpoint_speakers.add(speaker_value)

        if not interleaved_punctuation:
            (
                outgoing_final_tokens,
                separator_tokens,
            ) = self._process_non_interleaved_tokens(
                new_final_tokens,
                endpoint_detected,
            )

        # Do this after all final-token boundary work so an interrupt finalized
        # in the same Soniox frame is already present in the snapshot history.
        # It still precedes speculative translation, ensuring that the live LLM
        # source contains the restored first fragment from its very first call.
        self._maybe_merge_from_non_final_tokens(
            non_final_tokens, outgoing_final_tokens
        )
        self._maybe_send_live_osc_translation(non_final_tokens)
        self._maybe_speculative_translate(non_final_tokens)

        # 将新的final tokens写入日志
        loggable_tokens = [t for t in new_final_tokens if not self._is_internal_token(t)]
        if loggable_tokens and not self.is_paused:
            self.logger.write_to_log(loggable_tokens)

        # 如果有新的数据，发送给前端（暂停时也显示，只是不记录）
        if self._broadcast_token_update(loop, outgoing_final_tokens, separator_tokens, non_final_tokens):
            # 更新已发送的计数
            sent_count = len(all_final_tokens)
            self.last_sent_count = sent_count

        if new_final_tokens:
            sent_count = len(all_final_tokens)
            self.last_sent_count = sent_count

        # Semantic pairing progress: hand fully paired sentences to the LLM
        # refine path. After the token broadcast so the frontend has the
        # sentence's tokens before its refine_result can arrive.
        self._dispatch_paired_sentences()

        # Session finished.
        if res.get("finished"):
            print("Session finished.")
            return sent_count, True, "session finished"

        return sent_count, False, None

    
    def _audio_router_factory(self):
        return AudioSendRouter

    def _adjust_receive_timeout(self, receive_timeout: float | None) -> float | None:
        pairing_timeout = self._pairer.seconds_until_next_deadline()
        if pairing_timeout is None:
            return receive_timeout
        # Avoid a zero-timeout spin while still waking at the semantic
        # pairing deadline when Soniox goes quiet.
        pairing_timeout = max(0.001, pairing_timeout)
        if receive_timeout is None:
            return pairing_timeout
        return min(receive_timeout, pairing_timeout)

    def _on_receive_timeout(self) -> None:
        self._dispatch_paired_sentences()
