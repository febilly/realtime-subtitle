"""Hosted LLM transport for relay (托管) mode.

In relay mode the client does not call the LLM upstream directly. Instead it
multiplexes chat-completion requests as text frames onto the existing STT relay
WebSocket; the relay backend (Cloudflare DO or the VPS) proxies them to the
upstream model, bills the prepaid balance, and returns the result on the same
socket. This module owns the request/response correlation, per-request timeout
with retry, and the fallback signal.

Frame protocol (client -> relay):
    {"type": "llm.request", "id": <str>, "messages": [...],
     "temperature": <float>, "max_tokens": <int>}
Frames (relay -> client):
    {"type": "llm.response", "id": <str>, "content": <str>,
     "usage": {...}, "credits": <float>}
    {"type": "llm.error",    "id": <str>, "reason": <str>}
    {"type": "llm.disabled", "reason": <str>}   # prepaid exhausted -> fall back
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class HostedLlmError(RuntimeError):
    """A hosted LLM request failed. `reason` carries the relay's reason code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class HostedLlmDisabled(HostedLlmError):
    """The relay refused because prepaid balance is exhausted (fall back to fast)."""


# Reasons that must not be retried (retrying cannot help / would waste credits).
_NON_RETRYABLE = {"input_too_long", "empty_request", "llm_not_configured"}


class HostedLlmTransport:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._send: Optional[Callable[[str], None]] = None
        self._pending: Dict[str, "asyncio.Future[str]"] = {}
        # Per-request send time (perf_counter), for the console latency readout.
        self._started_at: Dict[str, float] = {}
        self._disabled = False
        self._disabled_reason = ""
        # Cumulative usage across this transport's lifetime (mirrors llm_client's
        # own-key counters) plus total credits spent, for the console log.
        self._total_uncached = 0
        self._total_cached = 0
        self._total_completion = 0
        self._total_credits = 0.0
        # Called (on the event loop thread) with the credits a request consumed.
        self.on_credits: Optional[Callable[[float], None]] = None
        # Called (on the event loop thread) when the relay disables hosted LLM.
        self.on_disabled: Optional[Callable[[str], None]] = None

    def configure(self, loop: asyncio.AbstractEventLoop, send: Callable[[str], None]) -> None:
        self._loop = loop
        self._send = send

    def reset(self) -> None:
        """Clear pending state for a fresh session (keeps callbacks/config)."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(HostedLlmError("reset"))
        self._pending.clear()
        self._started_at.clear()
        self._disabled = False
        self._disabled_reason = ""

    @property
    def disabled(self) -> bool:
        return self._disabled

    def handle_frame(self, data: Any) -> bool:
        """Called from the recv thread. Returns True if `data` was an llm.* frame
        (and therefore already consumed / must not be treated as STT)."""
        if not isinstance(data, dict):
            return False
        frame_type = data.get("type")
        if not isinstance(frame_type, str) or not frame_type.startswith("llm."):
            return False
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._dispatch, data)
        return True

    def _dispatch(self, data: Dict[str, Any]) -> None:
        frame_type = data.get("type")
        if frame_type == "llm.disabled":
            self._disabled = True
            self._disabled_reason = str(data.get("reason") or "")
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(HostedLlmDisabled(self._disabled_reason or "disabled"))
            self._pending.clear()
            self._started_at.clear()
            if callable(self.on_disabled):
                try:
                    self.on_disabled(self._disabled_reason)
                except Exception:
                    pass
            return

        rid = data.get("id")
        fut = self._pending.pop(rid, None) if isinstance(rid, str) else None
        if frame_type == "llm.response":
            # Log + count the spend even if the caller already timed out — the
            # relay still billed this (late) response, so it must count toward the
            # totals shown in the console and the balance estimate.
            self._log_response(rid, data)
            credits = data.get("credits")
            if isinstance(credits, (int, float)) and credits > 0 and callable(self.on_credits):
                try:
                    self.on_credits(float(credits))
                except Exception:
                    pass
            if fut is not None and not fut.done():
                fut.set_result(str(data.get("content") or ""))
        elif frame_type == "llm.error":
            if isinstance(rid, str):
                self._started_at.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_exception(HostedLlmError(str(data.get("reason") or "error")))
        else:
            # Unknown llm.* frame: resolve empty so the caller can fall back.
            if isinstance(rid, str):
                self._started_at.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_result("")

    def _log_response(self, rid: Any, data: Dict[str, Any]) -> None:
        """Print an own-key-style call line plus this-call and total credits."""
        started = self._started_at.pop(rid, None) if isinstance(rid, str) else None
        elapsed_ms = int((time.perf_counter() - started) * 1000) if started is not None else -1

        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        try:
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        except Exception:
            prompt_tokens = 0
        cached = 0
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens", 0) or 0)
        if not cached:
            cached = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        uncached = max(0, prompt_tokens - cached)
        completion = int(usage.get("completion_tokens", 0) or 0)
        credits = data.get("credits")
        credits = float(credits) if isinstance(credits, (int, float)) else 0.0

        self._total_uncached += uncached
        self._total_cached += cached
        self._total_completion += completion
        self._total_credits += credits

        elapsed_str = f"{elapsed_ms:>4}ms" if elapsed_ms >= 0 else "   ?ms"
        print(
            f"⚡ LLM (relay) {elapsed_str}  "
            f"↑{uncached:<4} + {cached:<4}c  ↓{completion:<3}  "
            f"credits {credits:.3f} (total {self._total_credits:.3f})  "
            f"tokens: ↑{self._total_uncached}+{self._total_cached}c  ↓{self._total_completion}"
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        retries: int = 2,
    ) -> str:
        """Send one request and await the content. Retries on timeout only."""
        if self._send is None or self._loop is None:
            raise HostedLlmError("transport not ready")
        if self._disabled:
            raise HostedLlmDisabled(self._disabled_reason or "disabled")

        attempts = max(1, int(retries) + 1)
        last_exc: Optional[BaseException] = None
        for _ in range(attempts):
            rid = uuid.uuid4().hex
            fut: "asyncio.Future[str]" = self._loop.create_future()
            self._pending[rid] = fut
            self._started_at[rid] = time.perf_counter()
            frame = json.dumps(
                {
                    "type": "llm.request",
                    "id": rid,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
            )
            try:
                self._send(frame)
            except Exception as exc:
                self._pending.pop(rid, None)
                self._started_at.pop(rid, None)
                raise HostedLlmError(f"send failed: {exc}")
            try:
                return await asyncio.wait_for(fut, timeout=max(1.0, float(timeout_seconds)))
            except asyncio.TimeoutError:
                # Drop the stale future (a late response for this id is ignored)
                # and retry with a fresh id. The relay still bills the old one.
                self._pending.pop(rid, None)
                last_exc = HostedLlmError("timeout")
                continue
            except HostedLlmDisabled:
                raise
            except HostedLlmError as exc:
                self._pending.pop(rid, None)
                if exc.reason in _NON_RETRYABLE:
                    raise
                # Other errors (e.g. too_many_inflight, upstream failure): do not
                # spin — surface immediately so the caller keeps the fast result.
                raise
        raise last_exc or HostedLlmError("timeout")
