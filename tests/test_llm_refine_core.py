from unittest.mock import MagicMock

import pytest

import llm_refine


@pytest.fixture(autouse=True)
def stable_prompt_config(monkeypatch):
    monkeypatch.setattr(llm_refine.config, "LLM_REFINE_CONTEXT_MAX_COUNT", 2, raising=False)
    monkeypatch.setattr(llm_refine.config, "LLM_REFINE_CONTEXT_MIN_COUNT", 0, raising=False)
    monkeypatch.setattr(
        llm_refine.config,
        "llm_context_bounds",
        lambda: (
            int(getattr(llm_refine.config, "LLM_REFINE_CONTEXT_MIN_COUNT", 0)),
            int(getattr(llm_refine.config, "LLM_REFINE_CONTEXT_MAX_COUNT", 0)),
        ),
        raising=False,
    )
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
            "fixed",
            "draft",
            "source",
            {"has_answer": True, "no_change": False, "refined": "fixed", "category": ""},
        ),
        (
            "```text\nfixed\n```",
            "draft",
            "source",
            {"has_answer": True, "no_change": False, "refined": "fixed", "category": ""},
        ),
        (
            "draft",
            "draft",
            "source",
            {"has_answer": True, "no_change": True, "refined": "", "category": ""},
        ),
        (
            "source",
            "draft",
            "source",
            {"has_answer": True, "no_change": True, "refined": "", "category": ""},
        ),
        (
            "__NO_CHANGE__",
            "draft",
            "source",
            {"has_answer": True, "no_change": True, "refined": "", "category": ""},
        ),
        (
            "",
            "draft",
            "source",
            {"has_answer": False, "no_change": True, "refined": "", "category": ""},
        ),
    ],
)
def test_parse_refine_response_gate(raw, draft, source, expected):
    assert llm_refine.parse_refine_response(raw, draft, source) == expected


def test_parse_refine_response_strips_code_fence():
    assert llm_refine.parse_refine_response("```text\nnew value\n```", "draft", "source") == {
        "has_answer": True,
        "no_change": False,
        "refined": "new value",
        "category": "",
    }


@pytest.mark.parametrize(
    "raw",
    [
        "__NO_CHANGE__",
        "NO_CHANGE",
        "NO CHANGE",
        "no-change.",
        "```text\nNO_CHANGE\n```",
        "<answer>__NO_CHANGE__</answer>",
    ],
)
def test_parse_refine_response_accepts_no_change_marker_variants(raw):
    assert llm_refine.parse_refine_response(raw, "draft", "source") == {
        "has_answer": True,
        "no_change": True,
        "refined": "",
        "category": "",
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
        {"source": "recent"},
        {"source": "latest"},
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
    assert "Previous context (from oldest to newest; reference only, do not translate or output):" in prompt
    assert "1. before" in prompt
    assert "Translation" not in prompt
    assert "之前" not in prompt
    assert "Source:\nsource\n\n" in prompt
    assert "Draft:\ndraft\n\n" in prompt
    assert prompt.endswith("CUSTOM RULE")


def test_build_translate_messages_contains_source_only_context():
    messages = llm_refine.build_translate_messages(
        " question? ",
        [{"source": "before", "translation": "之前"}],
        target_lang="zh",
    )
    prompt = messages[1]["content"]
    assert "Previous context (from oldest to newest; reference only, do not translate or output):" in prompt
    assert "1. before" in prompt
    assert "Translation" not in prompt
    assert "之前" not in prompt
    assert "Translate the following into Chinese:\nquestion?" in prompt


def test_zero_context_omits_context_from_both_prompts(monkeypatch):
    monkeypatch.setattr(llm_refine.config, "LLM_REFINE_CONTEXT_MAX_COUNT", 0)
    supplied_context = [{"source": "before", "translation": "之前"}]

    refine_messages = llm_refine.build_refine_messages(
        "source", "draft", supplied_context, target_lang="zh"
    )
    translate_messages = llm_refine.build_translate_messages(
        "source", supplied_context, target_lang="zh"
    )

    for messages in (refine_messages, translate_messages):
        rendered = "\n".join(message["content"] for message in messages)
        assert "Context" not in rendered
        assert "before" not in rendered
        assert "之前" not in rendered


def test_prompts_request_plain_text_outputs():
    refine_messages = llm_refine.build_refine_messages(
        "source", "draft", [], target_lang="zh"
    )
    translate_messages = llm_refine.build_translate_messages(
        "source", [], target_lang="zh"
    )

    assert llm_refine.NO_CHANGE_MARKER in refine_messages[0]["content"]
    for messages in (refine_messages, translate_messages):
        rendered = "\n".join(message["content"] for message in messages)
        assert "<answer>" not in rendered
        assert "<error>" not in rendered


def test_zero_context_prompts_match_expected_format():
    refine_user = llm_refine.build_refine_messages(
        "source", "draft", [], target_lang="zh"
    )[1]["content"]
    translate_user = llm_refine.build_translate_messages(
        "source", [], target_lang="zh"
    )[1]["content"]

    assert refine_user == "Source:\nsource\n\nDraft:\ndraft\n\nTarget language: Chinese"
    assert translate_user == "Translate the following into Chinese:\nsource"


@pytest.mark.asyncio
async def test_perform_refine_retries_missing_answer_then_applies_fix(monkeypatch):
    replies = iter(
        [
            "",
            "fixed",
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
    }
    assert any(call.args[0] == "refine_retry_no_answer" for call in log_event.call_args_list)
    assert log_event.call_args.kwargs["decision"] == "applied"


@pytest.mark.asyncio
async def test_perform_refine_empty_and_no_change_paths():
    assert await llm_refine.perform_refine(
        MagicMock(), "", "draft", [], target_lang="zh"
    ) == {"status": "error", "no_change": True}

    async def chat(*args, **kwargs):
        return "__NO_CHANGE__"

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
async def test_perform_translate_retries_empty_then_succeeds():
    replies = iter(["", "", "```\n完成\n```"])

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
