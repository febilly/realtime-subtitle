"""Shared sentence-boundary helpers for punctuation-based subtitle splitting."""

from collections.abc import Callable

# "…" stays in this set so punctuation-run scans treat it as punctuation
# (e.g. "。…" is one trailing-off run, not a boundary), but
# is_sentence_ender_at never lets it END a sentence.
SENTENCE_END_CHARS = {"。", "！", "？", ".", "!", "?", "︒", "︕", "︖", "…"}
CLOSING_QUOTE_CHARS = "\"'”’»›」』》"
SENTENCE_END_ABBREVIATION_EXCEPTIONS = {
    "a.m.",
    "p.m.",
    "e.g.",
    "i.e.",
    "u.s.",
    "u.k.",
}
SENTENCE_END_ABBREVIATION_PREFIXES = {
    abbreviation[: index + 1]
    for abbreviation in SENTENCE_END_ABBREVIATION_EXCEPTIONS
    for index, ch in enumerate(abbreviation[:-1])
    if ch == "."
}


def is_sentence_ender_at(value: str, index: int) -> bool:
    ch = value[index]
    # Ellipses mean the speaker trailed off, not that the sentence ended.
    # Splitting there created extra pairing handoffs whose misfires shifted
    # source/translation alignment (live 2026-07-11); the fragment before an
    # ellipsis also translates worse on its own.
    if ch == "…":
        return False
    if ch == ".":
        prev_ch = value[index - 1] if index > 0 else ""
        next_ch = value[index + 1] if index + 1 < len(value) else ""
        if prev_ch == "." or next_ch == ".":
            return False  # part of an ASCII ellipsis ".." / "..."
        if prev_ch.isdigit() and next_ch.isdigit():
            return False
    return ch in SENTENCE_END_CHARS


def text_ends_with_ellipsis(text: str) -> bool:
    value = str(text or "").rstrip()
    while value and value[-1] in CLOSING_QUOTE_CHARS:
        value = value[:-1].rstrip()
    return value.endswith("…") or value.endswith("..")


def text_ends_with_abbreviation_segment(text: str, segment: str) -> bool:
    value = str(text or "").rstrip().casefold()
    if not value.endswith(segment):
        return False
    start = len(value) - len(segment)
    return start == 0 or not value[start - 1].isalpha()


def text_ends_with_abbreviation_exception(text: str) -> bool:
    return any(
        text_ends_with_abbreviation_segment(text, abbreviation)
        for abbreviation in SENTENCE_END_ABBREVIATION_EXCEPTIONS
    )


def text_ends_with_abbreviation_prefix(text: str) -> bool:
    return any(
        text_ends_with_abbreviation_segment(text, prefix)
        for prefix in SENTENCE_END_ABBREVIATION_PREFIXES
    )


def text_continues_abbreviation(previous_context: str, next_text: str) -> bool:
    combined = f"{previous_context or ''}{next_text or ''}"
    return (
        text_ends_with_abbreviation_exception(combined)
        or text_ends_with_abbreviation_prefix(combined)
    )


def token_text_starts_with_closing_quote(previous_text: str, next_text: str) -> bool:
    if not previous_text or not next_text:
        return False
    return not previous_text[-1].isspace() and next_text[0] in CLOSING_QUOTE_CHARS


def text_ends_with_closing_quote_after_sentence_punctuation(text: str) -> bool:
    value = str(text or "").rstrip()
    if not value or value[-1] not in CLOSING_QUOTE_CHARS:
        return False
    index = len(value) - 1
    while index >= 0 and value[index] in CLOSING_QUOTE_CHARS:
        index -= 1
    return index >= 0 and is_sentence_ender_at(value, index)


def is_sentence_ending_punctuation(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    while value and value[-1] in CLOSING_QUOTE_CHARS:
        value = value[:-1].rstrip()
    if not value:
        return False
    if text_ends_with_abbreviation_exception(value) or text_ends_with_abbreviation_prefix(value):
        return False
    for index in range(len(value) - 1, -1, -1):
        if value[index] in SENTENCE_END_CHARS:
            return is_sentence_ender_at(value, index)
        if not value[index].isspace():
            return False
    return False


def split_into_sentence_lines(text: str) -> list[str]:
    value = text or ""
    if not value:
        return []

    lines: list[str] = []
    start = 0
    length = len(value)
    i = 0
    while i < length:
        ch = value[i]
        if ch not in SENTENCE_END_CHARS or not is_sentence_ender_at(value, i):
            i += 1
            continue
        if i + 1 < length and value[i + 1] in SENTENCE_END_CHARS:
            i += 1
            continue

        end = i + 1
        while end < length and value[end] in CLOSING_QUOTE_CHARS:
            end += 1

        segment = value[start:end]
        if text_ends_with_abbreviation_exception(segment) or text_ends_with_abbreviation_prefix(segment):
            i += 1
            continue

        lines.append(segment)
        start = end
        i = end

    if start < length:
        lines.append(value[start:])
    return [segment for segment in (line.strip() for line in lines) if segment]


class PendingBoundaryState:
    """Track sentence boundaries that need one more token before cutting.

    Streaming recognizers often finalize a token ending in punctuation before the
    next tiny token arrives, e.g. ``1.`` + ``5.``, ``a.`` + ``m.``, or ``.`` +
    ``"``. This class holds those ambiguous boundaries and tells callers whether
    to insert a separator before the current token.
    """

    def __init__(self) -> None:
        self.numeric_period_tokens: dict[tuple[str, str], dict] = {}
        self.abbreviation_prefix_tokens: dict[tuple[str, str], dict] = {}
        self.quote_boundary_tokens: dict[tuple[str, str], dict] = {}

    def clear(self) -> None:
        self.numeric_period_tokens.clear()
        self.abbreviation_prefix_tokens.clear()
        self.quote_boundary_tokens.clear()

    def clear_speaker(self, speaker: str) -> None:
        speaker_value = str(speaker)
        for store in (
            self.numeric_period_tokens,
            self.abbreviation_prefix_tokens,
            self.quote_boundary_tokens,
        ):
            for key in list(store.keys()):
                if key[0] == speaker_value:
                    store.pop(key, None)

    def key(self, token: dict) -> tuple[str, str]:
        speaker = str(token.get("speaker", "?"))
        status = "translation" if token.get("translation_status") == "translation" else "original"
        return speaker, status

    def next_compatible_text(
        self,
        tokens: list[dict],
        index: int,
        *,
        is_internal_token: Callable[[object], bool],
        source_as_output: bool,
    ) -> str | None:
        token = tokens[index]
        speaker = token.get("speaker")
        is_translation = token.get("translation_status") == "translation"
        for next_token in tokens[index + 1:]:
            if is_internal_token(next_token):
                continue
            if next_token.get("speaker") != speaker:
                continue
            next_is_translation = next_token.get("translation_status") == "translation"
            if next_is_translation != is_translation and not (source_as_output and not is_translation):
                continue
            next_text = str(next_token.get("text") or "")
            if next_text:
                return next_text
        return None

    def is_token_sentence_ending(
        self,
        tokens: list[dict],
        index: int,
        *,
        context_text: str,
        is_internal_token: Callable[[object], bool],
        source_as_output: bool,
    ) -> bool:
        token = tokens[index]
        text = str(token.get("text") or "")
        if not is_sentence_ending_punctuation(text):
            return False
        # context_text ends with this token's text: a lone "." whose buffered
        # context already ends with "." is part of an ellipsis, not a boundary.
        if text_ends_with_ellipsis(context_text):
            return False
        if text_ends_with_abbreviation_exception(context_text):
            return False
        if text_ends_with_abbreviation_prefix(context_text):
            return False
        next_text = self.next_compatible_text(
            tokens,
            index,
            is_internal_token=is_internal_token,
            source_as_output=source_as_output,
        )
        if next_text is not None and token_text_starts_with_closing_quote(text, next_text):
            return False
        stripped = text.strip()
        if next_text is None and stripped == ".":
            return False
        stripped = text.rstrip()
        if not stripped.endswith("."):
            return True
        if next_text is not None and next_text.startswith("."):
            return False  # an ASCII ellipsis is still streaming ("." + "..")
        prev_ch = stripped[-2] if len(stripped) >= 2 else ""
        if not prev_ch.isdigit():
            return True
        if next_text is None:
            return False
        return not token_text_continues_decimal(text, next_text)

    def flush_before_token(self, token: dict) -> bool:
        key = self.key(token)
        quote = self.quote_boundary_tokens.pop(key, None)
        if quote:
            if quote.get("awaiting_quote") and token_text_starts_with_closing_quote(
                str(quote.get("context_text") or ""),
                str(token.get("text") or ""),
            ):
                self.quote_boundary_tokens[key] = {
                    "context_text": f"{quote.get('context_text') or ''}{token.get('text') or ''}",
                    "awaiting_quote": False,
                }
                return False
            return True

        abbreviation = self.abbreviation_prefix_tokens.pop(key, None)
        if abbreviation:
            if text_continues_abbreviation(
                str(abbreviation.get("context_text") or ""),
                str(token.get("text") or ""),
            ):
                return False
            return True

        numeric = self.numeric_period_tokens.pop(key, None)
        if numeric:
            return not token_text_continues_decimal(
                str(numeric.get("text") or ""),
                str(token.get("text") or ""),
            )
        return False

    def mark_after_token(
        self,
        tokens: list[dict],
        index: int,
        *,
        context_text: str,
        is_internal_token: Callable[[object], bool],
        source_as_output: bool,
    ) -> None:
        token = tokens[index]
        is_translation = token.get("translation_status") == "translation"
        if not is_translation and not source_as_output:
            return

        key = self.key(token)
        if text_ends_with_abbreviation_prefix(context_text):
            self.abbreviation_prefix_tokens[key] = {"context_text": context_text}

        quote_boundary = self._pending_quote_boundary(
            tokens,
            index,
            context_text=context_text,
            is_internal_token=is_internal_token,
            source_as_output=source_as_output,
        )
        if quote_boundary:
            self.quote_boundary_tokens[key] = quote_boundary

        if self._has_unresolved_numeric_period(
            tokens,
            index,
            is_internal_token=is_internal_token,
            source_as_output=source_as_output,
        ):
            self.numeric_period_tokens[key] = dict(token)

    def _pending_quote_boundary(
        self,
        tokens: list[dict],
        index: int,
        *,
        context_text: str,
        is_internal_token: Callable[[object], bool],
        source_as_output: bool,
    ) -> dict | None:
        token = tokens[index]
        if text_ends_with_closing_quote_after_sentence_punctuation(context_text):
            return {"context_text": context_text, "awaiting_quote": False}
        if text_ends_with_abbreviation_exception(context_text) or text_ends_with_abbreviation_prefix(context_text):
            return None
        text = str(token.get("text") or "")
        if not is_sentence_ending_punctuation(text):
            return None
        stripped = text.strip()
        if stripped.endswith("."):
            prev_ch = stripped[-2] if len(stripped) >= 2 else ""
            if prev_ch.isdigit():
                return None
        next_text = self.next_compatible_text(
            tokens,
            index,
            is_internal_token=is_internal_token,
            source_as_output=source_as_output,
        )
        if next_text is not None:
            if token_text_starts_with_closing_quote(text, next_text):
                return {"context_text": context_text, "awaiting_quote": True}
            return None
        if stripped == ".":
            return {"context_text": context_text, "awaiting_quote": True}
        return None

    def flush_stale_quote_boundaries_before_incompatible_token(self, token: dict) -> list[str]:
        """Flush quote waits that this token cannot possibly satisfy."""
        current_key = self.key(token)
        speakers: list[str] = []
        for key in list(self.quote_boundary_tokens.keys()):
            if key == current_key:
                continue
            self.quote_boundary_tokens.pop(key, None)
            speakers.append(key[0])
        return speakers

    def _has_unresolved_numeric_period(
        self,
        tokens: list[dict],
        index: int,
        *,
        is_internal_token: Callable[[object], bool],
        source_as_output: bool,
    ) -> bool:
        token = tokens[index]
        text = str(token.get("text") or "")
        stripped = text.rstrip()
        if len(stripped) < 2 or not stripped.endswith(".") or not stripped[-2].isdigit():
            return False
        next_text = self.next_compatible_text(
            tokens,
            index,
            is_internal_token=is_internal_token,
            source_as_output=source_as_output,
        )
        return next_text is None


def token_text_continues_decimal(previous_text: str, next_text: str) -> bool:
    if not previous_text or not next_text:
        return False
    return not previous_text[-1].isspace() and not next_text[0].isspace() and next_text[0].isdigit()
