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
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    extra_headers: Optional[Dict[str, str]] = None
    extra_json: Optional[Dict[str, Any]] = None


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
    }

    # Merge extra JSON body fields (may override standard fields)
    if config.extra_json:
        payload.update(config.extra_json)

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    # Merge extra HTTP headers (may override default headers)
    if config.extra_headers:
        headers.update(config.extra_headers)

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    session = await _get_http_session()
    _t0 = time.perf_counter()
    async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
        text = await resp.text()
        _elapsed_ms = int((time.perf_counter() - _t0) * 1000)
        if resp.status >= 400:
            raise LlmError(f"LLM request failed: HTTP {resp.status}: {text[:4000]}")

        try:
            data = json.loads(text)
        except Exception as exc:
            raise LlmError(f"LLM returned non-JSON response: {text[:4000]}") from exc

    # Extract usage info
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    cached_tokens = 0
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cached_tokens = details.get("cached_tokens", 0) or 0
    # Some providers use a different key
    if not cached_tokens:
        cached_tokens = usage.get("prompt_cache_hit_tokens", 0) or 0
    uncached_tokens = max(0, prompt_tokens - cached_tokens)
    completion_tokens = usage.get("completion_tokens", 0)

    global _llm_total_uncached, _llm_total_cached, _llm_total_completion
    _llm_total_uncached += uncached_tokens
    _llm_total_cached += cached_tokens
    _llm_total_completion += completion_tokens

    print(
        f"⚡ LLM ({config.model}) {_elapsed_ms:>4}ms  "
        f"↑{uncached_tokens:<3} + {cached_tokens:<3}c  ↓{completion_tokens:<3}  "
        f"total: ↑{_llm_total_uncached}+{_llm_total_cached}c  ↓{_llm_total_completion}"
    )

    try:
        return (
            data.get("choices", [])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception as exc:
        raise LlmError(f"LLM response format unexpected: {data}") from exc


_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Cumulative token counters across all LLM calls in this process
_llm_total_uncached: int = 0
_llm_total_cached: int = 0
_llm_total_completion: int = 0


def extract_answer_tag(text: str) -> str:
    """Extract <answer>...</answer> if present; otherwise return trimmed text.

    If multiple <answer>...</answer> blocks exist, prefer the LAST one.
    """
    if text is None:
        return ""
    raw = str(text).strip()
    if not raw:
        return ""

    # Strip any chain-of-thought tags to avoid leaking them.
    raw = _THINK_RE.sub("", raw).strip()
    if not raw:
        return ""

    last_match = None
    for match in _ANSWER_RE.finditer(raw):
        last_match = match

    if last_match is None:
        # Fallback: if <answer> exists without a closing tag, return content after the last <answer>.
        lowered = raw.lower()
        tag = "<answer>"
        if tag in lowered:
            idx = lowered.rfind(tag)
            candidate = raw[idx + len(tag):].strip()
            candidate = re.sub(r"</answer>\s*$", "", candidate, flags=re.IGNORECASE).strip()
            return candidate
        return raw

    return (last_match.group(1) or "").strip()
