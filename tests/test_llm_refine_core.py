from unittest.mock import MagicMock

import pytest

import llm_refine


@pytest.fixture(autouse=True)
def stable_prompt_config(monkeypatch):
    monkeypatch.setattr(llm_refine.config, "LLM_REFINE_CONTEXT_MAX_COUNT", 2, raising=False)
    monkeypatch.setattr(llm_refine.config, "LLM_PROMPT_SUFFIX", "", raising=False)
    monkeypatch.setattr(llm_refine.config, "LLM_TEMPERATURE", 0.2, raising=False)
    monkeypatch.setattr(llm_refine.config, "LLM_REFINE_MAX_TOKENS", 256, raising=False)
    monkeypatch.setattr(
        llm_refine.config,
        "describe_target_language",
        lambda value: {"zh": "Chinese"}.get(value, value or "default"),
        raising=False,
    )


@pytest.mark.parametrize(
    ("raw", "draft", "source", "expected"),
    [
        (
            "<answer>fixed</answer><error>mistranslation</error>",
            "draft",
            "source",
            {"has_answer": True, "no_change": False, "refined": "fixed", "category": "mistranslation"},
        ),
        (
            "<answer>fixed</answer><error>style</error>",
            "draft",
            "source",
            {"has_answer": True, "no_change": True, "refined": "", "category": ""},
        ),
        (
            "<answer>draft</answer><error>omission</error>",
            "draft",
            "source",
            {"has_answer": True, "no_change": True, "refined": "", "category": "omission"},
        ),
        (
            "<answer>source</answer><error>untranslated</error>",
            "draft",
            "source",
            {"has_answer": True, "no_change": True, "refined": "", "category": "untranslated"},
        ),
        (
            "<check>none</check><answer>__NO_CHANGE__</answer>",
            "draft",
            "source",
            {"has_answer": True, "no_change": True, "refined": "", "category": ""},
        ),
        (
            "<check>missing answer</check>",
            "draft",
            "source",
            {"has_answer": False, "no_change": True, "refined": "", "category": ""},
        ),
    ],
)
def test_parse_refine_response_gate(raw, draft, source, expected):
    assert llm_refine.parse_refine_response(raw, draft, source) == expected


def test_parse_refine_response_uses_last_tags_and_strips_fence():
    raw = (
        "<answer>old</answer><error>style</error>"
        "<answer>```text\nnew value\n```</answer><error>GARBLED</error>"
    )
    assert llm_refine.parse_refine_response(raw, "draft", "source") == {
        "has_answer": True,
        "no_change": False,
        "refined": "new value",
        "category": "garbled",
    }


def test_normalize_context_keeps_latest_valid_items_only():
    oversized = "x" * 5001
    context = [
        {"source": "too old", "translation": "旧"},
        "invalid",
        {"source": oversized, "translation": "skip"},
        {"source": "  recent  ", "translation": " 最近 "},
        {"source": "latest", "translation": "最新"},
    ]
    assert llm_refine._normalize_context(context) == [
        {"source": "recent", "translation": "最近"},
        {"source": "latest", "translation": "最新"},
    ]


def test_build_refine_messages_contains_clean_inputs_context_and_suffix(monkeypatch):
    monkeypatch.setattr(llm_refine.config, "LLM_PROMPT_SUFFIX", "CUSTOM RULE")
    messages = llm_refine.build_refine_messages(
        "  source  ",
        "  draft  ",
        [{"source": "before", "translation": "之前"}],
        target_lang=" ZH ",
    )
    prompt = messages[1]["content"]
    assert messages[0]["role"] == "system"
    assert "Target language: Chinese" in prompt
    assert "1. Source: before" in prompt
    assert "Translation: 之前" in prompt
    assert "source/translation" in prompt
    assert "\nsource\n" in prompt
    assert "\ndraft\n" in prompt
    assert prompt.endswith("CUSTOM RULE")


def test_build_translate_messages_uses_source_only_context_warning():
    messages = llm_refine.build_translate_messages(
        " question? ",
        [{"source": "before", "translation": "之前"}],
        target_lang="zh",
    )
    prompt = messages[1]["content"]
    assert "even if the source is short" in prompt
    assert "source/translation is short" not in prompt
    assert "question?" in prompt


@pytest.mark.asyncio
async def test_perform_refine_retries_missing_answer_then_applies_fix(monkeypatch):
    replies = iter(
        [
            "no tags",
            "<answer>fixed</answer><error>wrong-subject</error>",
        ]
    )

    async def chat(*args, **kwargs):
        return next(replies)

    log_event = MagicMock()
    monkeypatch.setattr(llm_refine, "log_event", log_event)
    result = await llm_refine.perform_refine(
        chat, "source", "draft", [], target_lang="zh"
    )

    assert result == {
        "status": "ok",
        "no_change": False,
        "refined_translation": "fixed",
        "error_category": "wrong-subject",
    }
    assert any(call.args[0] == "refine_retry_no_answer" for call in log_event.call_args_list)
    assert log_event.call_args.kwargs["decision"] == "applied"


@pytest.mark.asyncio
async def test_perform_refine_empty_and_no_change_paths():
    assert await llm_refine.perform_refine(
        MagicMock(), "", "draft", [], target_lang="zh"
    ) == {"status": "error", "no_change": True}

    async def chat(*args, **kwargs):
        return "<answer>__NO_CHANGE__</answer>"

    assert await llm_refine.perform_refine(
        chat, "source", "draft", [], target_lang="zh"
    ) == {"status": "ok", "no_change": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (llm_refine.HostedLlmDisabled("disabled"), "llm_disabled"),
        (llm_refine.LlmError("bad key"), "bad key"),
        (ValueError("boom"), "LLM request failed"),
    ],
)
async def test_perform_refine_maps_chat_errors(error, message):
    async def chat(*args, **kwargs):
        raise error

    result = await llm_refine.perform_refine(
        chat, "source", "draft", [], target_lang="zh"
    )
    assert result["status"] == "error"
    assert result["no_change"] is True
    assert result["message"] == message


@pytest.mark.asyncio
async def test_perform_translate_retries_empty_and_placeholder_then_succeeds():
    replies = iter(["", "<answer>...translated text...</answer>", "<answer>```\n完成\n```</answer>"])

    async def chat(*args, **kwargs):
        return next(replies)

    result = await llm_refine.perform_translate(chat, "source", [], target_lang="zh")
    assert result == {"status": "ok", "translation": "完成"}


@pytest.mark.asyncio
async def test_perform_translate_empty_source_and_exhausted_empty_reply():
    assert await llm_refine.perform_translate(MagicMock(), " ", [], target_lang="zh") == {
        "status": "error",
        "message": "empty source",
    }

    async def chat(*args, **kwargs):
        return ""

    assert await llm_refine.perform_translate(chat, "source", [], target_lang="zh") == {
        "status": "error",
        "message": "empty translation",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (llm_refine.HostedLlmDisabled("disabled"), "llm_disabled"),
        (llm_refine.HostedLlmError("upstream"), "upstream"),
        (RuntimeError("boom"), "LLM request failed"),
    ],
)
async def test_perform_translate_maps_chat_errors(error, message):
    async def chat(*args, **kwargs):
        raise error

    result = await llm_refine.perform_translate(chat, "source", [], target_lang="zh")
    assert result == {"status": "error", "message": message}
