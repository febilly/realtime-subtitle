"""Shared LLM refine / direct-translate logic for the STT sessions.

SonioxSession and GeminiSession refine a draft translation and run direct
translations with identical prompts and response parsing. Keeping that here
means the prompt text lives in exactly one place; each session just passes its
own ``chat`` coroutine (its ``_llm_chat``, which routes to the relay or a local
key) and the per-utterance target language.
"""
import asyncio
import re
from typing import Awaitable, Callable, Optional

import config
from llm_client import extract_answer_tag, LlmError
from hosted_llm import HostedLlmError, HostedLlmDisabled

# ``chat(system_prompt, user_prompt, *, temperature, max_tokens) -> str``
ChatFn = Callable[..., Awaitable[str]]

NO_CHANGE_MARKER = "__NO_CHANGE__"
_REFINE_PLACEHOLDER = "...refined translation..."
_TRANSLATE_PLACEHOLDER = "...translated text..."
_DEFAULT_SEVERITY = "low"
_SEVERITY_LEVELS = ("low", "medium", "high", "critical")
MAX_REFINE_ATTEMPTS = 3
MAX_TRANSLATE_ATTEMPTS = 3


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


def _parse_severity(raw_content: str) -> str:
    severity = _DEFAULT_SEVERITY
    matches = re.findall(r"<severity>(.*?)</severity>", raw_content or "", flags=re.IGNORECASE | re.DOTALL)
    if matches:
        severity = str(matches[-1]).strip().lower()
    if severity not in _SEVERITY_LEVELS:
        severity = _DEFAULT_SEVERITY
    return severity


def _build_refine_prompt(source: str, translation: str, target_lang_value: str, context_block: str) -> str:
    return (
        f"Target language: {config.describe_target_language(target_lang_value)}\n\n"
        "Role: Translation editor for real-time subtitles. Improve the draft translation while preserving its overall sentence structure.\n\n"
        "## Goals\n"
        " - Keep the meaning faithful to the source, including names, numbers, speaker intent, and question/statement form.\n"
        " - Make the translation more accurate, fluent, natural, and easy to read in the target language.\n"
        " - Clean up streaming artifacts, awkward word order, loose punctuation, and minor grammar issues when doing so improves readability.\n\n"
        "## Editing limits\n"
        " - Keep the draft's broad structure and information order unless a small change is needed for correctness or natural target-language flow.\n"
        " - Do NOT add information that is not in the source. Do NOT omit source meaning.\n"
        " - Do NOT rewrite into a new style, summary, explanation, or localization beyond what the source supports.\n"
        " - If the draft is already accurate, fluent, and natural, output the NO CHANGE marker.\n"
        " - Subject-less source: keep a subject/actor only when it can be confidently inferred (and fix it if the draft supplied the wrong one). "
        "If the subject cannot be confidently inferred, prefer leaving it unstated rather than inventing one, where the target language allows subject omission.\n"
        " - Common variations that keep the same meaning are NOT errors and are at most low severity — usually a NO CHANGE. Examples: "
        "active vs passive voice with identical meaning; equivalent ways of listing or exemplifying items "
        "(e.g. \"a and b are X, and c is also X\" vs \"a, b, and c are X\"); reordering coordinate items; synonym choice. "
        "Do NOT raise severity for these.\n\n"
        "## Severity guide\n"
        " - low: Small wording, grammar, punctuation, or fluency improvement; the draft is understandable and meaning is essentially correct. "
        "Meaning-preserving rephrasings (voice swaps, equivalent enumeration/coordination, synonyms) also belong here at most.\n"
        " - medium: Noticeable awkwardness, ambiguity, or minor meaning drift; a reader may pause, but the main message is still recoverable. "
        "Ordinary, not-serious sentence-structure problems (clumsy or non-idiomatic word order, run-on phrasing, awkward clause structure) that do NOT distort the core meaning belong here, NOT in high.\n"
        " - high: Clear mistranslation, omitted/added important detail, wrong relation, number, name, tense, or question intent that would mislead the reader. "
        "This includes a wrong subject/actor that the draft inferred for a subject-less source (e.g. the source omits the subject and the draft supplies the wrong \"I/you/he/she/they\"), since it misattributes who does or receives the action. "
        "Reserve this for meaning-level errors; mere awkward or imperfect sentence structure, voice swaps, or equivalent list phrasings that still convey the correct meaning are low/medium, not high.\n"
        " - critical: Opposite meaning, dangerous instruction, severe hallucination, or a change that would cause the reader to understand the source incorrectly.\n\n"
        "## Output Format\n"
        f" - NO CHANGE: <answer>{NO_CHANGE_MARKER}</answer>\n"
        f" - Refined translation: <answer>{_REFINE_PLACEHOLDER}</answer>\n"
        "   - Severity (only when changed): <severity>low|medium|high|critical</severity>\n\n"
        "Do NOT explain. Do NOT add preamble.\n\n"
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


async def perform_refine(
    chat: ChatFn,
    source: str,
    translation: str,
    context_items: list,
    *,
    target_lang,
) -> dict:
    """Refine an existing draft translation. Returns a dict with ``status`` and
    ``no_change`` (and ``refined_translation`` when a high/critical fix applies)."""
    source = (source or "").strip()
    translation = (translation or "").strip()
    if not source or not translation:
        return {"status": "error", "no_change": True}

    target_lang_value = _clean_target_lang(target_lang)
    context_block = _render_context_block(
        _normalize_context(context_items), mention_translation=True
    )
    prompt = _build_refine_prompt(source, translation, target_lang_value, context_block)

    for attempt in range(MAX_REFINE_ATTEMPTS):
        try:
            content = await chat(
                "You are a precise translation reviewer.",
                prompt,
                temperature=float(config.LLM_TEMPERATURE),
                max_tokens=int(config.LLM_REFINE_MAX_TOKENS),
            )
        except (asyncio.CancelledError, Exception) as exc:
            if isinstance(exc, HostedLlmDisabled):
                return {"status": "error", "message": "llm_disabled", "no_change": True}
            if isinstance(exc, (LlmError, HostedLlmError)):
                return {"status": "error", "message": str(exc), "no_change": True}
            return {"status": "error", "message": "LLM request failed", "no_change": True}

        raw_content = str(content or "").strip()
        refined = extract_answer_tag(raw_content).strip()
        severity = _parse_severity(raw_content)

        if not refined:
            if attempt < MAX_REFINE_ATTEMPTS - 1:
                continue
            return {"status": "ok", "no_change": True}

        refined = _strip_code_fence(refined)

        if refined == _REFINE_PLACEHOLDER:
            if attempt < MAX_REFINE_ATTEMPTS - 1:
                continue
            return {"status": "ok", "no_change": True}

        if refined == NO_CHANGE_MARKER:
            return {"status": "ok", "no_change": True}

        if severity not in ("high", "critical"):
            return {"status": "ok", "no_change": True}

        return {"status": "ok", "no_change": False, "refined_translation": refined}

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

    target_lang_value = _clean_target_lang(target_lang)
    context_block = _render_context_block(
        _normalize_context(context_items), mention_translation=False
    )
    prompt = _build_translate_prompt(source, target_lang_value, context_block)

    for attempt in range(MAX_TRANSLATE_ATTEMPTS):
        try:
            content = await chat(
                "You are a precise real-time translator.",
                prompt,
                temperature=float(config.LLM_TEMPERATURE),
                max_tokens=int(config.LLM_REFINE_MAX_TOKENS),
            )
        except (asyncio.CancelledError, Exception) as exc:
            if isinstance(exc, HostedLlmDisabled):
                return {"status": "error", "message": "llm_disabled"}
            if isinstance(exc, (LlmError, HostedLlmError)):
                return {"status": "error", "message": str(exc)}
            return {"status": "error", "message": "LLM request failed"}

        raw_content = str(content or "").strip()
        translated = extract_answer_tag(raw_content).strip()

        if not translated:
            if attempt < MAX_TRANSLATE_ATTEMPTS - 1:
                continue
            return {"status": "error", "message": "empty translation"}

        translated = _strip_code_fence(translated)

        if translated == _TRANSLATE_PLACEHOLDER:
            if attempt < MAX_TRANSLATE_ATTEMPTS - 1:
                continue
            return {"status": "error", "message": "placeholder translation"}

        return {"status": "ok", "translation": translated}

    return {"status": "error", "message": "translation failed"}
