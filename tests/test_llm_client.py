import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import llm_client


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._body


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://example.test/v1", "https://example.test/v1/chat/completions"),
        (" https://example.test/v1/ ", "https://example.test/v1/chat/completions"),
        ("https://example.test/chat/completions", "https://example.test/chat/completions"),
    ],
)
def test_build_chat_completions_url(base_url, expected):
    assert llm_client._build_chat_completions_url(base_url) == expected


def test_build_chat_completions_url_rejects_empty_value():
    with pytest.raises(llm_client.LlmError, match="LLM_BASE_URL"):
        llm_client._build_chat_completions_url("  ")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("  plain text  ", "plain text"),
        ("<think>private</think><answer>first</answer><answer>last</answer>", "last"),
        ("before <ANSWER> unfinished ", "unfinished"),
        ("<think>only reasoning</think>", ""),
    ],
)
def test_extract_answer_tag(value, expected):
    assert llm_client.extract_answer_tag(value) == expected


@pytest.mark.asyncio
async def test_chat_completion_builds_request_and_tracks_usage(monkeypatch):
    body = json.dumps(
        {
            "choices": [{"message": {"content": "translated"}}],
            "usage": {
                "prompt_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 5},
                "completion_tokens": 3,
            },
        }
    )
    session = FakeSession(FakeResponse(body=body))
    monkeypatch.setattr(llm_client, "_get_http_session", AsyncMock(return_value=session))
    log_event = MagicMock()
    monkeypatch.setattr(llm_client, "log_event", log_event)
    monkeypatch.setattr(llm_client, "_llm_total_uncached", 0)
    monkeypatch.setattr(llm_client, "_llm_total_cached", 0)
    monkeypatch.setattr(llm_client, "_llm_total_completion", 0)

    config = llm_client.LlmConfig(
        base_url="https://example.test/v1/",
        api_key="secret",
        model="model-a",
        extra_headers={"X-Test": "yes", "Authorization": "Custom auth"},
        extra_json={"temperature": 0.7, "vendor_flag": True},
    )
    result = await llm_client.chat_completion(
        config,
        [{"role": "user", "content": "hello"}],
        temperature=0.1,
        max_tokens=77,
        timeout_seconds=9,
    )

    assert result == "translated"
    url, kwargs = session.calls[0]
    assert url == "https://example.test/v1/chat/completions"
    assert kwargs["json"] == {
        "model": "model-a",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.7,
        "max_tokens": 77,
        "stream": False,
        "vendor_flag": True,
    }
    assert kwargs["headers"]["Authorization"] == "Custom auth"
    assert kwargs["headers"]["X-Test"] == "yes"
    assert kwargs["timeout"].total == 9
    assert (llm_client._llm_total_uncached, llm_client._llm_total_cached) == (7, 5)
    assert llm_client._llm_total_completion == 3
    assert log_event.call_args.kwargs["empty"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "message"),
    [
        (llm_client.LlmConfig("https://x", "", "m"), "api key"),
        (llm_client.LlmConfig("https://x", "k", ""), "model"),
    ],
)
async def test_chat_completion_validates_required_config(config, message):
    with pytest.raises(llm_client.LlmError, match=message):
        await llm_client.chat_completion(config, [])


@pytest.mark.asyncio
async def test_chat_completion_reports_http_error(monkeypatch):
    session = FakeSession(FakeResponse(status=429, body="rate limited"))
    monkeypatch.setattr(llm_client, "_get_http_session", AsyncMock(return_value=session))
    log_event = MagicMock()
    monkeypatch.setattr(llm_client, "log_event", log_event)

    with pytest.raises(llm_client.LlmError, match="HTTP 429: rate limited"):
        await llm_client.chat_completion(llm_client.LlmConfig("https://x", "k", "m"), [])

    assert log_event.call_args.kwargs["status"] == 429
    assert log_event.call_args.kwargs["error"] == "rate limited"


@pytest.mark.asyncio
async def test_chat_completion_rejects_non_json(monkeypatch):
    session = FakeSession(FakeResponse(body="not json"))
    monkeypatch.setattr(llm_client, "_get_http_session", AsyncMock(return_value=session))
    monkeypatch.setattr(llm_client, "log_event", MagicMock())

    with pytest.raises(llm_client.LlmError, match="non-JSON response"):
        await llm_client.chat_completion(llm_client.LlmConfig("https://x", "k", "m"), [])


@pytest.mark.asyncio
async def test_chat_completion_rejects_unexpected_response_shape(monkeypatch):
    session = FakeSession(FakeResponse(body=json.dumps({"choices": None})))
    monkeypatch.setattr(llm_client, "_get_http_session", AsyncMock(return_value=session))
    monkeypatch.setattr(llm_client, "log_event", MagicMock())

    with pytest.raises(llm_client.LlmError, match="format unexpected"):
        await llm_client.chat_completion(llm_client.LlmConfig("https://x", "k", "m"), [])


@pytest.mark.asyncio
async def test_chat_completion_logs_and_reraises_transport_error(monkeypatch):
    session = FakeSession(error=ConnectionError("offline"))
    monkeypatch.setattr(llm_client, "_get_http_session", AsyncMock(return_value=session))
    log_event = MagicMock()
    monkeypatch.setattr(llm_client, "log_event", log_event)

    with pytest.raises(ConnectionError, match="offline"):
        await llm_client.chat_completion(llm_client.LlmConfig("https://x", "k", "m"), [])

    assert log_event.call_args.kwargs["error"] == "offline"


@pytest.mark.asyncio
async def test_shared_http_session_is_reused_and_closed(monkeypatch):
    created = []

    class Session:
        def __init__(self, **kwargs):
            self.closed = False
            self.kwargs = kwargs
            created.append(self)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(llm_client.aiohttp, "TCPConnector", lambda **kwargs: ("connector", kwargs))
    monkeypatch.setattr(llm_client.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(llm_client, "_http_session", None)
    monkeypatch.setattr(llm_client, "_http_session_lock", None)

    first = await llm_client._get_http_session()
    second = await llm_client._get_http_session()
    assert first is second
    assert len(created) == 1

    await llm_client.close_llm_http_session()
    assert first.closed is True
    assert llm_client._http_session is None
    await llm_client.close_llm_http_session()
