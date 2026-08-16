"""Shared LLM refine / direct-translate logic for the STT sessions.

SonioxSession and GeminiSession refine a draft translation and run direct
translations with identical prompts and response parsing. Keeping that here
means the prompt text lives in exactly one place; each session just passes its
own ``chat`` coroutine (its ``_llm_chat``, which routes to the relay or a local
key) and the per-utterance target language.
"""
import asyncio
import time
from typing import Awaitable, Callable, Optional

import config
from llm_client import LlmError
from llm_log import log_event
from hosted_llm import HostedLlmError, HostedLlmDisabled

# ``chat(system_prompt, user_prompt, *, temperature, max_tokens) -> str``
ChatFn = Callable[..., Awaitable[str]]

NO_CHANGE_MARKER = "__NO_CHANGE__"
MAX_REFINE_ATTEMPTS = 3
MAX_TRANSLATE_ATTEMPTS = 3

# DeepSeek V4 Flash caches fixed prefixes in 128-token units. These system
# prompts are calibrated so each system message independently spans 130+
# tokens (including chat-template framing). This guarantees that the first
# 128-token cache checkpoint falls wholly inside the static system prompt,
# achieving 100% prefix cache hits regardless of user prompt variation,
# target language, or presence of historical context.
_TRANSLATE_SYSTEM_PROMPT = (
    "You translate real-time subtitles faithfully. Output only the translated text, with no "
    "label, tag, preamble, note, or explanation. Preserve the original meaning, names, numbers, tone, "
    "questions, and useful punctuation. Do not add, omit, or alter information. If a subject is omitted "
    "in the source, do not guess unless the source itself makes it clear. Transliterate names from non-Latin "
    "scripts when the target language normally uses another script. When the source discusses "
    "language or quotes an expression, translate the discussion but preserve the mentioned "
    "expression as appropriate. Translate fragments as fragments; never complete or extrapolate a sentence that "
    "the source has not completed."
)

_REFINE_SYSTEM_PROMPT = (
    "You check a draft real-time subtitle translation against its source text. Change only clear "
    "meaning errors: wrong meaning, incorrect subject, wrong number or name, question form, "
    "omission, addition, untranslated text, or garbled text. Never restyle valid wording for fluency, "
    "naturalness, word order, punctuation, synonyms, or equivalent phrasing. Subtitle segments may be incomplete; "
    "tolerate fragments, fillers, stutters, false starts, and adjacent-segment spillover. If uncertain, "
    "always keep the draft translation. Output only "
    f"{NO_CHANGE_MARKER} or the minimal corrected translation, with no "
    "label, tag, reason, preamble, note, or explanation."
)


def _clean_target_lang(target_lang) -> str:
    try:
        if isinstance(target_lang, str) and target_lang.strip():
            return target_lang.strip().lower()[:16]
    except Exception:
        pass
    return ""


def _normalize_context(context_items) -> list[dict]:
    normalized: list[dict] = []
    max_count = int(config.llm_context_bounds()[1])
    if isinstance(context_items, list) and max_count > 0:
        max_items = max(1, max_count)
        for item in context_items[-max_items:]:
            if isinstance(item, dict):
                ctx_source = item.get("source")
            elif isinstance(item, str):
                ctx_source = item
            else:
                continue
            if not isinstance(ctx_source, str):
                continue
            ctx_source = ctx_source.strip()
            if not ctx_source or len(ctx_source) > 5000:
                continue
            normalized.append({"source": ctx_source})
    return normalized


def _render_context_block(normalized_context: list[dict]) -> str:
    if not normalized_context:
        return ""
    lines = [
        "Previous context (from oldest to newest; reference only, do not translate or output):",
    ]
    for idx, item in enumerate(normalized_context, start=1):
        lines.append(f"{idx}. {item['source']}")
    return "\n".join(lines) + "\n\n"


def _suffix_block() -> str:
    prompt_suffix = (config.LLM_PROMPT_SUFFIX or "").strip()
    return f"\n{prompt_suffix}" if prompt_suffix else ""


def _strip_code_fence(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        first_newline = value.find("\n")
        if first_newline >= 0:
            value = value[first_newline + 1 :]
        if value.rstrip().endswith("```"):
            value = value.rstrip()[:-3]
    return value.strip("`").strip()


def _is_no_change_marker(text: str) -> bool:
    value = str(text or "").strip()
    lowered = value.casefold()
    if lowered.startswith("<answer>") and lowered.endswith("</answer>"):
        value = value[len("<answer>") : -len("</answer>")].strip()
    value = value.strip("`*\"'").strip().rstrip(".。!！")
    normalized = "".join(
        char for char in value.casefold() if char not in {"_", "-", " ", "\t", "\r", "\n"}
    )
    return normalized == "nochange"


def parse_refine_response(raw_content: str, draft: str, source: str = "") -> dict:
    """Parse a refine response into a gate decision.

    The production protocol is deliberately plain text: either the
    ``__NO_CHANGE__`` marker or the corrected translation. Common marker
    spelling variations are accepted so they cannot leak into subtitles. A
    "fix" that merely echoes the draft or untranslated source is discarded.

    Returns ``{"has_answer", "no_change", "refined", "category"}``; offline
    eval tools use this too, so experiments exercise the production gate.
    """
    answer = _strip_code_fence(raw_content)
    if not answer:
        return {"has_answer": False, "no_change": True, "refined": "", "category": ""}
    if (
        _is_no_change_marker(answer)
        or answer == (draft or "").strip()
        or answer == (source or "").strip()
    ):
        return {"has_answer": True, "no_change": True, "refined": "", "category": ""}
    return {"has_answer": True, "no_change": False, "refined": answer, "category": ""}


def _build_refine_prompt(source: str, translation: str, target_lang_value: str, context_block: str) -> str:
    target_desc = config.describe_target_language(target_lang_value)
    return (
        f"{context_block}"
        f"Source:\n{source}\n\n"
        f"Draft:\n{translation}\n\n"
        f"Target language: {target_desc}"
        f"{_suffix_block()}"
    )


def _build_translate_prompt(source: str, target_lang_value: str, context_block: str) -> str:
    target_desc = config.describe_target_language(target_lang_value)
    return (
        f"{context_block}"
        f"Translate the following into {target_desc}:\n"
        f"{source}"
        f"{_suffix_block()}"
    )


def build_refine_messages(
    source: str,
    translation: str,
    context_items: list,
    *,
    target_lang,
) -> list[dict[str, str]]:
    """Build the exact chat messages used by ``perform_refine``.

    Offline evaluation tools import this so experiments exercise the same prompt
    template as the realtime session path.
    """
    target_lang_value = _clean_target_lang(target_lang)
    context_block = _render_context_block(_normalize_context(context_items))
    return [
        {"role": "system", "content": _REFINE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_refine_prompt(
                (source or "").strip(),
                (translation or "").strip(),
                target_lang_value,
                context_block,
            ),
        },
    ]


def build_translate_messages(
    source: str,
    context_items: list,
    *,
    target_lang: Optional[str] = None,
) -> list[dict[str, str]]:
    """Build the exact chat messages used by ``perform_translate``."""
    target_lang_value = _clean_target_lang(target_lang)
    context_block = _render_context_block(_normalize_context(context_items))
    return [
        {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_translate_prompt(
                (source or "").strip(),
                target_lang_value,
                context_block,
            ),
        },
    ]


async def perform_refine(
    chat: ChatFn,
    source: str,
    translation: str,
    context_items: list,
    *,
    target_lang,
) -> dict:
    """Refine an existing draft translation. Returns a dict with ``status`` and
    ``no_change`` (and ``refined_translation`` when a cited meaning-level fix applies)."""
    source = (source or "").strip()
    translation = (translation or "").strip()
    if not source or not translation:
        return {"status": "error", "no_change": True}

    messages = build_refine_messages(
        source,
        translation,
        context_items,
        target_lang=target_lang,
    )

    t0 = time.perf_counter()

    def _log_decision(decision: str, *, attempt: int, raw: str = "", parsed: Optional[dict] = None, error: str = ""):
        log_event(
            "refine_result",
            decision=decision,
            source=source,
            draft=translation,
            target_lang=str(target_lang or ""),
            context_count=len(context_items or []),
            attempts=attempt + 1,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            raw=raw,
            category=(parsed or {}).get("category", ""),
            refined=(parsed or {}).get("refined", ""),
            error=error,
        )

    for attempt in range(MAX_REFINE_ATTEMPTS):
        try:
            content = await chat(
                messages[0]["content"],
                messages[1]["content"],
                temperature=float(config.LLM_TEMPERATURE),
                max_tokens=int(config.LLM_REFINE_MAX_TOKENS),
            )
        except (asyncio.CancelledError, Exception) as exc:
            if isinstance(exc, HostedLlmDisabled):
                _log_decision("error", attempt=attempt, error="llm_disabled")
                return {"status": "error", "message": "llm_disabled", "no_change": True}
            if isinstance(exc, (LlmError, HostedLlmError)):
                _log_decision("error", attempt=attempt, error=str(exc))
                return {"status": "error", "message": str(exc), "no_change": True}
            _log_decision("error", attempt=attempt, error=f"{type(exc).__name__}: {exc}")
            return {"status": "error", "message": "LLM request failed", "no_change": True}

        raw_content = str(content or "")
        parsed = parse_refine_response(raw_content, translation, source)

        if not parsed["has_answer"]:
            if attempt < MAX_REFINE_ATTEMPTS - 1:
                log_event(
                    "refine_retry_no_answer",
                    attempt=attempt + 1,
                    source=source,
                    raw=raw_content,
                )
                continue
            _log_decision("no_answer", attempt=attempt, raw=raw_content, parsed=parsed)
            return {"status": "ok", "no_change": True}

        if parsed["no_change"]:
            _log_decision("no_change", attempt=attempt, raw=raw_content, parsed=parsed)
            return {"status": "ok", "no_change": True}

        _log_decision("applied", attempt=attempt, raw=raw_content, parsed=parsed)
        return {
            "status": "ok",
            "no_change": False,
            "refined_translation": parsed["refined"],
        }

    return {"status": "ok", "no_change": True}


async def perform_translate(
    chat: ChatFn,
    source: str,
    context_items: list,
    *,
    target_lang: Optional[str] = None,
) -> dict:
    """Translate the source directly. ``target_lang`` overrides the session
    target (used for two-way, where each utterance targets its partner language)."""
    source = (source or "").strip()
    if not source:
        return {"status": "error", "message": "empty source"}

    messages = build_translate_messages(
        source,
        context_items,
        target_lang=target_lang,
    )

    t0 = time.perf_counter()

    def _log_translate(decision: str, *, attempt: int, raw: str = "", translated: str = "", error: str = ""):
        log_event(
            "translate_result",
            decision=decision,
            source=source,
            target_lang=str(target_lang or ""),
            context_count=len(context_items or []),
            attempts=attempt + 1,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            raw=raw,
            translation=translated,
            error=error,
        )

    for attempt in range(MAX_TRANSLATE_ATTEMPTS):
        try:
            content = await chat(
                messages[0]["content"],
                messages[1]["content"],
                temperature=float(config.LLM_TEMPERATURE),
                max_tokens=int(config.LLM_REFINE_MAX_TOKENS),
            )
        except (asyncio.CancelledError, Exception) as exc:
            if isinstance(exc, HostedLlmDisabled):
                _log_translate("error", attempt=attempt, error="llm_disabled")
                return {"status": "error", "message": "llm_disabled"}
            if isinstance(exc, (LlmError, HostedLlmError)):
                _log_translate("error", attempt=attempt, error=str(exc))
                return {"status": "error", "message": str(exc)}
            _log_translate("error", attempt=attempt, error=f"{type(exc).__name__}: {exc}")
            return {"status": "error", "message": "LLM request failed"}

        raw_content = str(content or "").strip()
        translated = _strip_code_fence(raw_content)

        if not translated:
            if attempt < MAX_TRANSLATE_ATTEMPTS - 1:
                continue
            _log_translate("empty", attempt=attempt, raw=raw_content)
            return {"status": "error", "message": "empty translation"}

        _log_translate("ok", attempt=attempt, raw=raw_content, translated=translated)
        return {"status": "ok", "translation": translated}

    return {"status": "error", "message": "translation failed"}
