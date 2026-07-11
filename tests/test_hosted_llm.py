import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import hosted_llm


def test_handle_frame_only_consumes_llm_frames():
    transport = hosted_llm.HostedLlmTransport()
    loop = MagicMock()
    transport.configure(loop, MagicMock())

    assert transport.handle_frame("text") is False
    assert transport.handle_frame({"type": "result"}) is False
    assert transport.handle_frame({"type": "llm.response", "id": "x"}) is True
    loop.call_soon_threadsafe.assert_called_once_with(
        transport._dispatch, {"type": "llm.response", "id": "x"}
    )


@pytest.mark.asyncio
async def test_chat_sends_frame_and_receives_response(monkeypatch):
    loop = asyncio.get_running_loop()
    transport = hosted_llm.HostedLlmTransport()
    credits = MagicMock()
    transport.on_credits = credits

    def send(raw):
        request = json.loads(raw)
        assert request["messages"] == [{"role": "user", "content": "hello"}]
        assert request["temperature"] == 0.25
        assert request["max_tokens"] == 80
        loop.call_soon(
            transport._dispatch,
            {
                "type": "llm.response",
                "id": request["id"],
                "content": "answer",
                "credits": 1.5,
                "usage": {
                    "prompt_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 4},
                    "completion_tokens": 2,
                },
            },
        )

    transport.configure(loop, send)
    monkeypatch.setattr(hosted_llm, "log_event", MagicMock())

    result = await transport.chat(
        [{"role": "user", "content": "hello"}],
        temperature=0.25,
        max_tokens=80,
        timeout_seconds=2,
    )

    assert result == "answer"
    assert transport._pending == {}
    assert (transport._total_uncached, transport._total_cached) == (6, 4)
    assert transport._total_completion == 2
    assert transport._total_credits == 1.5
    credits.assert_called_once_with(1.5)


@pytest.mark.asyncio
async def test_chat_rejects_unready_and_disabled_transport():
    transport = hosted_llm.HostedLlmTransport()
    with pytest.raises(hosted_llm.HostedLlmError, match="transport not ready"):
        await transport.chat([], temperature=0, max_tokens=1, timeout_seconds=1)

    transport.configure(asyncio.get_running_loop(), MagicMock())
    transport._dispatch({"type": "llm.disabled", "reason": "billing_exhausted"})
    assert transport.disabled is True
    with pytest.raises(hosted_llm.HostedLlmDisabled, match="billing_exhausted"):
        await transport.chat([], temperature=0, max_tokens=1, timeout_seconds=1)


@pytest.mark.asyncio
async def test_disabled_frame_fails_pending_requests_and_calls_callback():
    transport = hosted_llm.HostedLlmTransport()
    future = asyncio.get_running_loop().create_future()
    transport._pending["r1"] = future
    transport._started_at["r1"] = 1.0
    callback = MagicMock(side_effect=RuntimeError("callback failure"))
    transport.on_disabled = callback

    transport._dispatch({"type": "llm.disabled", "reason": "no_balance"})

    assert transport.disabled is True
    assert transport._pending == {}
    assert transport._started_at == {}
    with pytest.raises(hosted_llm.HostedLlmDisabled, match="no_balance"):
        await future
    callback.assert_called_once_with("no_balance")


@pytest.mark.asyncio
async def test_error_and_unknown_frames_resolve_matching_future(monkeypatch):
    transport = hosted_llm.HostedLlmTransport()
    monkeypatch.setattr(hosted_llm, "log_event", MagicMock())
    loop = asyncio.get_running_loop()

    error_future = loop.create_future()
    transport._pending["error"] = error_future
    transport._dispatch({"type": "llm.error", "id": "error", "reason": "upstream"})
    with pytest.raises(hosted_llm.HostedLlmError, match="upstream"):
        await error_future

    unknown_future = loop.create_future()
    transport._pending["unknown"] = unknown_future
    transport._dispatch({"type": "llm.future", "id": "unknown"})
    assert await unknown_future == ""


def test_late_response_is_still_counted_and_notified(monkeypatch):
    transport = hosted_llm.HostedLlmTransport()
    callback = MagicMock(side_effect=RuntimeError("ignored"))
    transport.on_credits = callback
    log_event = MagicMock()
    monkeypatch.setattr(hosted_llm, "log_event", log_event)

    transport._dispatch(
        {
            "type": "llm.response",
            "id": "late",
            "content": "late answer",
            "credits": 0.25,
            "usage": {"prompt_tokens": 5, "prompt_cache_hit_tokens": 2},
        }
    )

    assert transport._total_uncached == 3
    assert transport._total_cached == 2
    assert transport._total_credits == 0.25
    assert log_event.call_args.kwargs["late"] is True
    callback.assert_called_once_with(0.25)


@pytest.mark.asyncio
async def test_chat_send_failure_is_wrapped(monkeypatch):
    transport = hosted_llm.HostedLlmTransport()
    transport.configure(
        asyncio.get_running_loop(),
        MagicMock(side_effect=OSError("socket closed")),
    )
    monkeypatch.setattr(hosted_llm, "log_event", MagicMock())

    with pytest.raises(hosted_llm.HostedLlmError, match="send failed: socket closed"):
        await transport.chat([], temperature=0, max_tokens=1, timeout_seconds=1)
    assert transport._pending == {}
    assert transport._started_at == {}


@pytest.mark.asyncio
async def test_chat_does_not_retry_relay_error(monkeypatch):
    loop = asyncio.get_running_loop()
    transport = hosted_llm.HostedLlmTransport()
    sent = []

    def send(raw):
        request = json.loads(raw)
        sent.append(request["id"])
        loop.call_soon(
            transport._dispatch,
            {"type": "llm.error", "id": request["id"], "reason": "input_too_long"},
        )

    transport.configure(loop, send)
    monkeypatch.setattr(hosted_llm, "log_event", MagicMock())

    with pytest.raises(hosted_llm.HostedLlmError, match="input_too_long"):
        await transport.chat([], temperature=0, max_tokens=1, timeout_seconds=1, retries=5)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_chat_retries_timeout_with_fresh_request_id(monkeypatch):
    transport = hosted_llm.HostedLlmTransport()
    transport.configure(asyncio.get_running_loop(), MagicMock())
    monkeypatch.setattr(hosted_llm, "log_event", MagicMock())
    wait_for = AsyncMock(side_effect=asyncio.TimeoutError)
    monkeypatch.setattr(hosted_llm.asyncio, "wait_for", wait_for)

    with pytest.raises(hosted_llm.HostedLlmError, match="timeout"):
        await transport.chat([], temperature=0, max_tokens=1, timeout_seconds=0, retries=2)

    assert transport._send.call_count == 3
    request_ids = [json.loads(call.args[0])["id"] for call in transport._send.call_args_list]
    assert len(set(request_ids)) == 3
    assert transport._pending == {}


@pytest.mark.asyncio
async def test_reset_fails_pending_future_and_reenables_transport():
    transport = hosted_llm.HostedLlmTransport()
    future = asyncio.get_running_loop().create_future()
    transport._pending["r"] = future
    transport._started_at["r"] = 1.0
    transport._disabled = True
    transport._disabled_reason = "old"

    transport.reset()

    with pytest.raises(hosted_llm.HostedLlmError, match="reset"):
        await future
    assert transport.disabled is False
    assert transport._pending == {}
