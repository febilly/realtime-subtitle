"""Shared LLM refine / direct-translate logic for the STT sessions.

SonioxSession and GeminiSession refine a draft translation and run direct
translations with identical prompts and response parsing. Keeping that here
means the prompt text lives in exactly one place; each session just passes its
own ``chat`` coroutine (its ``_llm_chat``, which routes to the relay or a local
key) and the per-utterance target language.
"""
import asyncio
import re
import time
from typing import Awaitable, Callable, Optional

import config
from llm_client import extract_answer_tag, LlmError
from llm_log import log_event
from hosted_llm import HostedLlmError, HostedLlmDisabled

# ``chat(system_prompt, user_prompt, *, temperature, max_tokens) -> str``
ChatFn = Callable[..., Awaitable[str]]

NO_CHANGE_MARKER = "__NO_CHANGE__"
_REFINE_PLACEHOLDER = "...refined translation..."
_TRANSLATE_PLACEHOLDER = "...translated text..."
MAX_REFINE_ATTEMPTS = 3
MAX_TRANSLATE_ATTEMPTS = 3

# Meaning-level error categories the refine model must cite to justify a
# change. Naming a discrete category is an easier, better-calibrated ask for
# small models than self-grading a severity level, and style rewrites have no
# category to hide behind — a proposal without a recognized category is
# discarded.
ERROR_CATEGORIES = frozenset(
    {
        "mistranslation",
        "wrong-subject",
        "wrong-number-or-name",
        "question-form",
        "omission",
        "hallucination",
        "untranslated",
        "garbled",
    }
)

_ANSWER_TAG_RE = re.compile(r"<answer>(.*?)</answer>", re.IGNORECASE | re.DOTALL)
_ERROR_TAG_RE = re.compile(r"<error>(.*?)</error>", re.IGNORECASE | re.DOTALL)


def _clean_target_lang(target_lang) -> str:
    try:
        if isinstance(target_lang, str) and target_lang.strip():
            return target_lang.strip().lower()[:16]
    except Exception:
        pass
    return ""


def _normalize_context(context_items) -> list[dict]:
    normalized: list[dict] = []
    max_count = int(config.LLM_REFINE_CONTEXT_MAX_COUNT)
    if isinstance(context_items, list) and max_count > 0:
        max_items = max(1, max_count)
        for item in context_items[-max_items:]:
            if not isinstance(item, dict):
                continue
            ctx_source = item.get("source")
            ctx_translation = item.get("translation")
            if not isinstance(ctx_source, str) or not isinstance(ctx_translation, str):
                continue
            ctx_source = ctx_source.strip()
            ctx_translation = ctx_translation.strip()
            if not ctx_source or not ctx_translation:
                continue
            if len(ctx_source) > 5000 or len(ctx_translation) > 5000:
                continue
            normalized.append({"source": ctx_source, "translation": ctx_translation})
    return normalized


def _render_context_block(normalized_context: list[dict], *, mention_translation: bool) -> str:
    if not normalized_context:
        return ""
    short_clause = "source/translation" if mention_translation else "source"
    lines = [
        "Context (for coherence only; do NOT quote it; do NOT merge or rewrite it into the current translation; "
        f"even if the {short_clause} is short, do NOT output the context; use it only to resolve pronouns, references, and coherence):",
    ]
    for idx, item in enumerate(normalized_context, start=1):
        lines.append(f"{idx}. Source: {item['source']}")
        lines.append(f"   Translation: {item['translation']}")
    return "\n".join(lines) + "\n\n"


def _suffix_block() -> str:
    prompt_suffix = (config.LLM_PROMPT_SUFFIX or "").strip()
    return f"\n{prompt_suffix}" if prompt_suffix else ""


def _strip_code_fence(text: str) -> str:
    value = text
    if value.startswith("```"):
        value = re.sub(r"^```[^\n]*\n", "", value)
        value = re.sub(r"\n```$", "", value.strip())
    return value.strip("`").strip()


def parse_refine_response(raw_content: str, draft: str, source: str = "") -> dict:
    """Parse a refine response into a gate decision.

    Requires a literal ``<answer>`` tag (no fallback to the whole text: the
    response also carries a ``<check>`` scan line that must never leak into the
    subtitle). A changed answer is applied only when the model cites a
    recognized meaning-level error category in ``<error>``. A "fix" that
    merely echoes the draft or the untranslated source is discarded.

    Returns ``{"has_answer", "no_change", "refined", "category"}``; offline
    eval tools use this too, so experiments exercise the production gate.
    """
    raw = str(raw_content or "")
    matches = _ANSWER_TAG_RE.findall(raw)
    answer = _strip_code_fence(str(matches[-1]).strip()) if matches else ""

    category = ""
    error_matches = _ERROR_TAG_RE.findall(raw)
    if error_matches:
        category = str(error_matches[-1]).strip().lower()
    if category not in ERROR_CATEGORIES:
        category = ""

    if not matches or answer == _REFINE_PLACEHOLDER:
        return {"has_answer": False, "no_change": True, "refined": "", "category": category}
    if (
        answer == NO_CHANGE_MARKER
        or not answer
        or answer == (draft or "").strip()
        or answer == (source or "").strip()
        or not category
    ):
        return {"has_answer": True, "no_change": True, "refined": "", "category": category}
    return {"has_answer": True, "no_change": False, "refined": answer, "category": category}


def _build_refine_prompt(source: str, translation: str, target_lang_value: str, context_block: str) -> str:
    return (
        f"Target language: {config.describe_target_language(target_lang_value)}\n\n"
        "Role: Error checker for real-time subtitle translations. The draft below was produced by a fast "
        "streaming translator. Decide whether the draft contains a MEANING-LEVEL ERROR against the source "
        "utterance. Fix only meaning-level errors; never restyle.\n\n"
        "## Meaning-level errors (the ONLY reasons to change the draft)\n"
        " - mistranslation: the draft says something different from the source (wrong meaning, reversed logic, "
        "negation lost, tense that changes meaning)\n"
        " - wrong-subject: the action is attributed to the wrong person (e.g. the source omits the subject and "
        "the draft guessed the wrong \"I/you/he/she/they\")\n"
        " - wrong-number-or-name: wrong number, date, quantity, or named entity\n"
        " - question-form: a question rendered as a statement, or the reverse\n"
        " - omission: meaning-bearing source words are missing from the draft and not covered by a context line\n"
        " - hallucination: the draft contains meaning found in neither the source nor the context lines\n"
        " - untranslated: source-language words left untranslated in the draft (names may stay in their usual "
        "written form)\n"
        " - garbled: the draft text is corrupted or unreadable\n\n"
        "## Not errors — NEVER change these\n"
        " - unidiomatic but understandable wording, word order, synonyms, punctuation, active/passive voice, "
        "equivalent phrasings; a defensible word choice is not a mistranslation — flag mistranslation only when "
        "the draft's meaning actually differs from the source's\n"
        " - the source is a mid-sentence fragment and the draft translates it as a fragment; incompleteness "
        "that mirrors the source is normal\n"
        " - dangling sentence-final particles, fillers, stray tense markers, or word repetitions that "
        "segmentation cut off (e.g. a lone \"た。\" or trailing \"from\") carry no translatable meaning — "
        "leaving them out is not an omission, and restoring them is not a fix\n"
        " - the draft borrows a subject, name, or a few words from the context lines — the streaming translator "
        "hears neighboring speech, so this is correct behavior, not hallucination; if unsure whether a word came "
        "from neighboring speech, keep the draft\n"
        " - names transliterated as the speaker actually said them; never replace a name with a different name "
        "you infer from context\n"
        " - source text that is already in the target language; the draft may keep or drop it — neither is an "
        "omission or untranslated; if most of the source is already in the target language, output NO CHANGE\n\n"
        "Streaming caution: sources are cut mid-sentence. Before calling omission or hallucination, check the "
        "context lines — meaning that appears in a neighboring line is already covered there. If the source is "
        "too fragmentary to judge, output NO CHANGE.\n\n"
        "## Procedure\n"
        "First output one short <check> line: `none`, or `category: evidence`. The evidence must quote the exact "
        "words as they appear in the source and/or draft above — if you cannot quote them, output NO CHANGE. "
        "Then output the answer. A change without a category from the list above will be discarded.\n\n"
        "## Output format (exactly one of)\n"
        "1. No meaning-level error:\n"
        "<check>none</check>\n"
        f"<answer>{NO_CHANGE_MARKER}</answer>\n"
        "2. Meaning-level error found — minimal fix, keep all other draft wording:\n"
        "<check>category: evidence</check>\n"
        f"<answer>{_REFINE_PLACEHOLDER}</answer>\n"
        "<error>mistranslation|wrong-subject|wrong-number-or-name|question-form|omission|hallucination|"
        "untranslated|garbled</error>\n\n"
        "## Examples (target language Chinese here; apply the same rules for the actual target language)\n"
        "Source: 行くって言ってたよ。 (context: talking about a friend)\n"
        "Draft: 我说过我会去。\n"
        "<check>wrong-subject: 言ってた reports the friend, draft says 我</check>\n"
        "<answer>他说过他会去。</answer>\n"
        "<error>wrong-subject</error>\n\n"
        "Source: and then we could probably\n"
        "Draft: 然后我们大概可以\n"
        "<check>none</check>\n"
        f"<answer>{NO_CHANGE_MARKER}</answer>\n\n"
        "Source: った。それでね、\n"
        "Draft: 然后呢，\n"
        "<check>none — the stray った。 was cut off by segmentation, not an omission</check>\n"
        f"<answer>{NO_CHANGE_MARKER}</answer>\n\n"
        "Source: said she would be late (context mentions 小美 speaking)\n"
        "Draft: 小美说她会迟到\n"
        "<check>none</check>\n"
        f"<answer>{NO_CHANGE_MARKER}</answer>\n\n"
        "Source: I haven't seen him since Monday.\n"
        "Draft: 我周一见过他。\n"
        "<check>mistranslation: haven't seen since Monday became 见过</check>\n"
        "<answer>我从周一起就没见过他。</answer>\n"
        "<error>mistranslation</error>\n\n"
        "Source: つまりこういう感じで、\n"
        "Draft: 所以就买了这个，\n"
        "<check>hallucination: 买了这个 is in neither source nor context</check>\n"
        "<answer>就是这种感觉，</answer>\n"
        "<error>hallucination</error>\n\n"
        "Do NOT explain outside the tags.\n\n"
        f"{context_block}"
        "Source:\n```\n"
        f"{source}\n"
        "```\n\n"
        "Draft translation:\n```\n"
        f"{translation}\n"
        "```\n"
        f"{_suffix_block()}"
    )


def _build_translate_prompt(source: str, target_lang_value: str, context_block: str) -> str:
    return (
        f"Target language: {config.describe_target_language(target_lang_value)}\n\n"
        "You are a professional real-time translator. Translate the source text into the target language.\n"
        "\n"
        "Rules:\n"
        "1. Output ONLY the translation; no explanations or extra text.\n"
        "2. Preserve the original meaning, named entities, numbers, and tone.\n"
        "3. If the source is a question, keep it a question in the translation (preserve question intent and punctuation such as '?' where appropriate).\n"
        "4. Do NOT add or omit information.\n"
        "5. If the source omits the subject/actor, supply it only when it can be confidently inferred from context; "
        "otherwise leave it unstated rather than guessing, where the target language allows subject omission.\n\n"
        "Output ONLY the translation wrapped exactly as:\n"
        f"<answer>{_TRANSLATE_PLACEHOLDER}</answer>\n\n"
        f"{context_block}"
        "Source:\n```\n"
        f"{source}\n"
        "```\n"
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
    context_block = _render_context_block(
        _normalize_context(context_items), mention_translation=True
    )
    return [
        {"role": "system", "content": "You are a precise translation error checker."},
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
    context_block = _render_context_block(
        _normalize_context(context_items), mention_translation=False
    )
    return [
        {"role": "system", "content": "You are a precise real-time translator."},
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
            "error_category": parsed["category"],
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
        translated = extract_answer_tag(raw_content).strip()

        if not translated:
            if attempt < MAX_TRANSLATE_ATTEMPTS - 1:
                continue
            _log_translate("empty", attempt=attempt, raw=raw_content)
            return {"status": "error", "message": "empty translation"}

        translated = _strip_code_fence(translated)

        if translated == _TRANSLATE_PLACEHOLDER:
            if attempt < MAX_TRANSLATE_ATTEMPTS - 1:
                continue
            _log_translate("placeholder", attempt=attempt, raw=raw_content)
            return {"status": "error", "message": "placeholder translation"}

        _log_translate("ok", attempt=attempt, raw=raw_content, translated=translated)
        return {"status": "ok", "translation": translated}

    return {"status": "error", "message": "translation failed"}
