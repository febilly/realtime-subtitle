"""Generic OpenAI-compatible LLM client.

This module intentionally avoids provider-specific naming. Configure it via:
- LLM_BASE_URL
- LLM_API_KEY
- LLM_MODEL

The backend uses this for translation refinement.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str


class LlmError(RuntimeError):
    pass


_http_session: Optional[aiohttp.ClientSession] = None
_http_session_lock: Optional[asyncio.Lock] = None


async def _get_http_session() -> aiohttp.ClientSession:
    """Return a shared aiohttp session to enable HTTP keep-alive.

    Creating a new ClientSession per request defeats connection reuse.
    We keep a process-level session and close it during app cleanup.
    """

    global _http_session, _http_session_lock

    if _http_session_lock is None:
        _http_session_lock = asyncio.Lock()

    async with _http_session_lock:
        if _http_session is not None and not _http_session.closed:
            return _http_session

        connector = aiohttp.TCPConnector(
            limit=50,
            limit_per_host=20,
            ttl_dns_cache=300,
            keepalive_timeout=30,
        )

        # Do not set a global timeout here; pass per-request timeouts instead.
        _http_session = aiohttp.ClientSession(connector=connector)
        return _http_session


async def close_llm_http_session() -> None:
    """Close the shared aiohttp session used for LLM calls."""

    global _http_session
    session = _http_session
    _http_session = None
    if session is None:
        return
    if session.closed:
        return
    await session.close()


def _build_chat_completions_url(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if not value:
        raise LlmError("LLM_BASE_URL is not configured")
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


async def chat_completion(
    config: LlmConfig,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    timeout_seconds: float = 45.0,
) -> str:
    """Call an OpenAI-compatible Chat Completions API and return the assistant content."""

    if not config.api_key:
        raise LlmError("LLM api key is not configured")
    if not config.model:
        raise LlmError("LLM model is not configured")

    url = _build_chat_completions_url(config.base_url)

    payload: Dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        # "provider": {
        #     'order': ['google-vertex'],
        # }
    }

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    session = await _get_http_session()
    async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise LlmError(f"LLM request failed: HTTP {resp.status}: {text[:4000]}")

        try:
            data = json.loads(text)
        except Exception as exc:
            raise LlmError(f"LLM returned non-JSON response: {text[:4000]}") from exc

    try:
        return (
            data.get("choices", [])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception as exc:
        raise LlmError(f"LLM response format unexpected: {data}") from exc


_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)


def extract_answer_tag(text: str) -> str:
    """Extract <answer>...</answer> if present; otherwise return trimmed text."""
    if text is None:
        return ""
    raw = str(text).strip()
    if not raw:
        return ""

    match = _ANSWER_RE.search(raw)
    if not match:
        return raw

    return (match.group(1) or "").strip()
