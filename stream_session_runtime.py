"""Shared runtime primitives for long-lived streaming speech sessions."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from websockets import ConnectionClosed

from relay_errors import relay_error_info


EAST_ASIAN_TIGHT_SPACING_CLASS = (
    r"\u3000-\u303F"
    r"\u3040-\u30FF"
    r"\u31F0-\u31FF"
    r"\u3400-\u4DBF"
    r"\u4E00-\u9FFF"
    r"\uF900-\uFAFF"
    r"\uFF01-\uFF60"
    r"\uFF66-\uFF9D"
    r"\uFFE0-\uFFEE"
)
EAST_ASIAN_TIGHT_SPACING_RE = re.compile(
    rf"([{EAST_ASIAN_TIGHT_SPACING_CLASS}])\s+([{EAST_ASIAN_TIGHT_SPACING_CLASS}])"
)
STREAM_ROLLOVER_RECV_TIMEOUT_SECONDS = 0.25
STREAM_ROLLOVER_FINALIZE_TIMEOUT_SECONDS = 1.5
STREAM_ROLLOVER_AUDIO_BUFFER_CHUNKS = 200
STREAM_ROLLOVER_NEAR_LIMIT_RATIO = 0.8
STREAM_ROLLOVER_SWITCH_PATIENCE_SECONDS = 25.0
STREAM_ROLLOVER_FORCE_GUARD_SECONDS = 2.0
STREAM_ROLLOVER_SILENCE_HOLD_SECONDS = 0.5
STREAM_ROLLOVER_WARMUP_DRAIN_LIMIT = 8


@dataclass
class StreamState:
    ws: Any
    index: int
    api_key: str
    started_at: float
    all_final_tokens: list[dict]
    sent_count: int = 0
    ready_at: float | None = None
    silence_sender: RealtimeSilenceSender | None = None
    silence_started_at: float = 0.0


class RealtimeSilenceSender:
    """Send realtime-paced PCM silence to a stream being warmed up."""

    def __init__(
        self,
        ws,
        *,
        bytes_per_chunk: int,
        chunk_interval_seconds: float,
        session_stop_event: threading.Event | None,
        thread_name: str,
    ) -> None:
        self.ws = ws
        self.payload = b"\0" * max(2, int(bytes_per_chunk))
        self.chunk_interval_seconds = max(0.01, float(chunk_interval_seconds))
        self.session_stop_event = session_stop_event
        self.thread_name = thread_name
        self.error: Exception | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=self.thread_name,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        next_send_at = time.monotonic()
        while not self._stop_event.is_set():
            if self.session_stop_event and self.session_stop_event.is_set():
                break
            try:
                self.ws.send(self.payload)
            except Exception as error:
                self.error = error
                break
            next_send_at += self.chunk_interval_seconds
            delay = next_send_at - time.monotonic()
            if delay < 0:
                next_send_at = time.monotonic()
                delay = self.chunk_interval_seconds
            self._stop_event.wait(delay)


@dataclass(frozen=True)
class StreamRuntimeSettings:
    provider_name: str
    key_thread_prefix: str
    finalize_thread_prefix: str
    recv_timeout_seconds: float
    audio_buffer_chunks: int
    silence_hold_seconds: float
    rollover_vad_threshold: float
    sleep_idle_seconds: float
    sleep_pre_roll_seconds: float
    sleep_speech_grace_seconds: float
    sleep_speech_window_seconds: float
    sleep_vad_threshold: float
    sleep_wake_speech_seconds: float
    sleep_wake_speech_window_seconds: float
    sleep_wake_vad_threshold: float


@dataclass(frozen=True)
class StreamSessionHooks:
    open_stream: Callable[..., StreamState]
    close_stream: Callable[..., None]
    process_response: Callable[..., tuple[int, bool, str | None]]
    adjust_receive_timeout: Callable[[float | None], float | None]
    on_receive_timeout: Callable[[], None]
    should_rollover_finished: Callable[[str | None, float | None, float | None], bool]
    finished_rollover_description: Callable[[str | None], str]


def is_api_key_error_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    if not text:
        return False
    needles = (
        "api key", "api_key", "apikey", "unauthorized", "authentication",
        "invalid key", "invalid api", "permission", "forbidden", "401", "403",
    )
    return any(needle in text for needle in needles)


def normalize_east_asian_translation_spacing(text: str) -> str:
    value = "" if text is None else str(text)
    if not value:
        return ""
    return EAST_ASIAN_TIGHT_SPACING_RE.sub(r"\1\2", value)


def run_stream_session(
    session,
    api_key: str,
    audio_format: str,
    translation: str,
    translation_target_lang: str,
    loop: asyncio.AbstractEventLoop,
    *,
    settings: StreamRuntimeSettings,
    hooks: StreamSessionHooks,
    audio_router_factory: Callable[..., Any],
) -> None:
    """Run one provider session, including sleep and rollover lifecycle."""
    provider_name = settings.provider_name
    if not api_key:
        print("❌ _run_session called without API key. Exiting session thread.")
        asyncio.run_coroutine_threadsafe(
            session.broadcast_callback({
                "type": "error",
                "code": "api_key",
                "message": f"{provider_name} API key is missing. Please configure it in Settings."
            }),
            loop
        )
        return

    rollover_seconds = session._stream_rollover_seconds()
    if rollover_seconds is not None:
        print(f"🔁 {provider_name} stream rollover enabled: {rollover_seconds:.1f}s per stream")
    sleep_idle_seconds = session._sleep_idle_seconds()
    sleep_enabled = sleep_idle_seconds is not None
    if sleep_idle_seconds is not None:
        print(
            f"💤 {provider_name} silence sleep enabled: {sleep_idle_seconds:.1f}s idle, "
            f"{float(settings.sleep_pre_roll_seconds):.2f}s pre-roll, "
            f"{float(settings.sleep_speech_grace_seconds):.2f}s speech/"
            f"{float(settings.sleep_speech_window_seconds):.2f}s window, "
            f"{float(settings.sleep_wake_speech_seconds):.2f}s wake speech/"
            f"{float(settings.sleep_wake_speech_window_seconds):.2f}s wake window"
        )

    session.stop_event = threading.Event()
    disconnect_reason = "connection ended"
    notify_disconnect = True
    relay_close = None  # (tag, terminal, message) when a relay code closes us
    current_api_key = api_key
    stream_index = 1
    active_stream: StreamState | None = None
    warmup_stream: StreamState | None = None
    dormant_for_silence = False
    next_prepare_attempt_at = 0.0
    warmup_future: concurrent.futures.Future | None = None
    key_fetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=settings.key_thread_prefix)
    audio_router = audio_router_factory(
        max_buffered_chunks=settings.audio_buffer_chunks,
        sample_rate=session.sample_rate,
        chunk_size=session.chunk_size,
        silence_hold_seconds=settings.silence_hold_seconds,
        vad_speech_threshold=settings.rollover_vad_threshold,
        sleep_idle_seconds=float(settings.sleep_idle_seconds),
        sleep_pre_roll_seconds=settings.sleep_pre_roll_seconds,
        sleep_speech_grace_seconds=settings.sleep_speech_grace_seconds,
        sleep_speech_window_seconds=settings.sleep_speech_window_seconds,
        sleep_vad_threshold=settings.sleep_vad_threshold,
        sleep_wake_speech_seconds=settings.sleep_wake_speech_seconds,
        sleep_wake_speech_window_seconds=settings.sleep_wake_speech_window_seconds,
        sleep_wake_vad_threshold=settings.sleep_wake_vad_threshold,
    )

    try:
        try:
            active_stream = hooks.open_stream(
                current_api_key,
                stream_index,
                audio_format,
                translation,
                translation_target_lang,
            )
        except ConnectionClosed as error:
            info = relay_error_info(error)
            if info is not None:
                relay_close = info
                disconnect_reason = f"relay: {info[0]}"
            else:
                disconnect_reason = f"connection closed: {error}"
            return
        except Exception as error:
            info = relay_error_info(error)
            if info is not None:
                relay_close = info
                disconnect_reason = f"relay: {info[0]}"
            else:
                disconnect_reason = f"connection error: {error}"
            print(f"Error connecting to {provider_name}: {error}")
            return
        session.ws = active_stream.ws
        session.last_sent_count = 0
        session._notify_relay_session(loop, "session_connected")

        if not audio_router.set_target(active_stream.ws):
            disconnect_reason = f"failed to attach audio to {provider_name} stream"
            return

        session._start_audio_streamer(audio_router)

        while True:
            if session.stop_event and session.stop_event.is_set():
                notify_disconnect = False
                break

            current_sleep_enabled = session._sleep_idle_seconds() is not None
            if current_sleep_enabled != sleep_enabled:
                sleep_enabled = current_sleep_enabled
                audio_router.reset_sleep_tracking()
                print(f"💤 {provider_name} automatic sleep {'enabled' if sleep_enabled else 'disabled'} at runtime.")

            if active_stream is None:
                if dormant_for_silence:
                    resume_because_disabled = not sleep_enabled
                    if audio_router.wake_ready() or resume_because_disabled:
                        buffered_count = audio_router.buffered_count()
                        try:
                            next_api_key = session._fetch_api_key_for_next_stream(current_api_key)
                            resumed_stream = hooks.open_stream(
                                next_api_key,
                                stream_index + 1,
                                audio_format,
                                translation,
                                translation_target_lang,
                            )
                            if not audio_router.set_target(resumed_stream.ws):
                                hooks.close_stream(resumed_stream)
                                disconnect_reason = "failed to attach audio after silence sleep"
                                break
                            active_stream = resumed_stream
                            stream_index = active_stream.index
                            current_api_key = active_stream.api_key
                            session.ws = active_stream.ws
                            session.last_sent_count = active_stream.sent_count
                            dormant_for_silence = False
                            session._notify_relay_session(loop, "session_connected")
                            disconnect_reason = "silence sleep resumed"
                            if resume_because_disabled:
                                print(
                                    f"▶️  Automatic sleep disabled; reopened {provider_name} stream "
                                    f"#{active_stream.index} and flushed {buffered_count} buffered chunks."
                                )
                            else:
                                print(
                                    f"▶️  Speech detected after silence; reopened {provider_name} stream "
                                    f"#{active_stream.index} and flushed {buffered_count} buffered chunks."
                                )
                        except Exception as error:
                            disconnect_reason = f"failed to reopen {provider_name} stream after silence: {error}"
                            print(f"⚠️  {disconnect_reason}")
                            break
                    else:
                        time.sleep(0.05)
                        continue
                else:
                    disconnect_reason = "stream rollover failed"
                    break

            if (
                sleep_enabled
                and not dormant_for_silence
                and active_stream is not None
                and audio_router.sleep_ready()
            ):
                if warmup_stream is not None:
                    if warmup_stream.silence_sender is not None:
                        warmup_stream.silence_sender.stop()
                        warmup_stream.silence_sender = None
                    hooks.close_stream(warmup_stream)
                    warmup_stream = None
                if warmup_future is not None:
                    warmup_future.cancel()
                    warmup_future = None

                print(
                    f"💤 No speech detected for "
                    f"{audio_router.sleep_confirmed_silence_seconds():.1f}s; closing {provider_name} stream."
                )
                sleeping_stream = active_stream
                if not audio_router.enter_sleep_buffering(sleeping_stream.ws):
                    disconnect_reason = "failed to detach audio for silence sleep"
                    break
                active_stream = None
                session.ws = None
                dormant_for_silence = True
                session._notify_relay_session(loop, "session_idle")
                sleeping_stream.sent_count = session._finalize_stream_before_rollover(
                    sleeping_stream.ws,
                    sleeping_stream.all_final_tokens,
                    sleeping_stream.sent_count,
                    loop,
                )
                hooks.close_stream(sleeping_stream, "silence_sleep")
                continue

            if active_stream is None:
                disconnect_reason = "stream rollover failed"
                break

            if (
                rollover_seconds is not None
                and warmup_stream is None
                and warmup_future is None
                and time.monotonic() >= next_prepare_attempt_at
                and session._should_prepare_rollover_stream(active_stream.started_at, rollover_seconds)
            ):
                warmup_future = key_fetch_executor.submit(
                    session._prepare_warmup_stream,
                    current_api_key, stream_index, audio_format,
                    translation, translation_target_lang,
                )
                stream_index += 1
                next_prepare_attempt_at = time.monotonic() + 1.0

            if warmup_future is not None:
                if warmup_future.done():
                    try:
                        warmup_stream = warmup_future.result(timeout=0)
                        warmup_future = None
                        warmup_stream.silence_sender.start()
                        warmup_stream.silence_started_at = time.monotonic()
                        print(
                            f"🔁 {provider_name} stream #{warmup_stream.index} is warming with realtime silence; "
                            f"waiting for a quiet audio gap to switch."
                        )
                    except Exception as error:
                        print(f"⚠️  Failed to prepare next {provider_name} stream for rollover: {error}")
                        warmup_future = None
                        warmup_stream = None
                        stream_index -= 1
                        next_prepare_attempt_at = time.monotonic() + 1.0
            if (
                rollover_seconds is not None
                and warmup_stream is None
                and session._should_force_rollover_switch(active_stream.started_at, rollover_seconds)
            ):
                switched = session._open_and_switch_to_replacement_stream(
                    audio_router,
                    active_stream,
                    current_api_key,
                    stream_index,
                    audio_format,
                    translation,
                    translation_target_lang,
                    loop,
                    "rollover guard deadline without warmup",
                )
                if switched is None:
                    disconnect_reason = f"failed to switch {provider_name} stream before configured duration"
                    break
                active_stream, current_api_key, stream_index = switched
                session.ws = active_stream.ws
                session.last_sent_count = active_stream.sent_count
                disconnect_reason = "stream rollover"
                continue

            if warmup_stream is not None:
                warmup_alive = True
                silence_sender = warmup_stream.silence_sender
                if silence_sender is not None and silence_sender.error is not None:
                    print(f"⚠️  {provider_name} warmup silence failed: {silence_sender.error}")
                    warmup_alive = False
                elif not session._drain_warmup_stream(warmup_stream):
                    warmup_alive = False

                if not warmup_alive:
                    hooks.close_stream(warmup_stream)
                    warmup_stream = None
                    next_prepare_attempt_at = time.monotonic() + 1.0
                else:
                    switch_on_silence = audio_router.silence_ready(min_observed_at=warmup_stream.ready_at)
                    force_switch = session._should_force_rollover_switch(
                        active_stream.started_at,
                        rollover_seconds,
                    )
                    silence_elapsed = time.monotonic() - warmup_stream.silence_started_at if warmup_stream.silence_started_at else 0.0
                    if silence_elapsed >= 2.0 and (switch_on_silence or force_switch):
                        switch_reason = (
                            f"quiet gap ({audio_router.consecutive_silence_seconds():.2f}s, silence sent {silence_elapsed:.1f}s)"
                            if switch_on_silence
                            else "rollover guard deadline"
                        )
                        print(
                            f"🔁 Switching {provider_name} audio from stream #{active_stream.index} "
                            f"to stream #{warmup_stream.index} at {switch_reason}."
                        )

                        old_stream = active_stream
                        if warmup_stream.silence_sender is not None:
                            warmup_stream.silence_sender.stop()
                            warmup_stream.silence_sender = None

                        if not audio_router.switch_target(
                            warmup_stream.ws,
                            expected_current=old_stream.ws,
                        ):
                            disconnect_reason = f"failed to switch audio to warmed {provider_name} stream"
                            break

                        active_stream = warmup_stream
                        warmup_stream = None
                        current_api_key = active_stream.api_key
                        session.ws = active_stream.ws
                        session.last_sent_count = active_stream.sent_count

                        # Finalize old stream in background so main loop
                        # immediately starts processing new stream responses.
                        threading.Thread(
                            target=session._finalize_and_close_stream,
                            args=(old_stream, loop),
                            daemon=True,
                            name=f"{settings.finalize_thread_prefix}{old_stream.index}",
                        ).start()
                        disconnect_reason = "stream rollover"
                        continue

            try:
                recv_timeout = (
                    settings.recv_timeout_seconds
                    if rollover_seconds is not None or sleep_enabled
                    else None
                )
                recv_timeout = hooks.adjust_receive_timeout(recv_timeout)
                message = active_stream.ws.recv(timeout=recv_timeout)
            except TimeoutError:
                hooks.on_receive_timeout()
                continue
            except ConnectionClosed as error:
                if rollover_seconds is not None and session._stream_is_near_rollover_limit(
                    active_stream.started_at,
                    rollover_seconds,
                ):
                    print(
                        f"🔁 {provider_name} stream #{active_stream.index} closed near configured duration; "
                        "rolling over..."
                    )
                    audio_router.clear_target(active_stream.ws)
                    hooks.close_stream(active_stream, "rollover")

                    if warmup_stream is not None:
                        if warmup_stream.silence_sender is not None:
                            warmup_stream.silence_sender.stop()
                            warmup_stream.silence_sender = None
                        active_stream = warmup_stream
                        warmup_stream = None
                        current_api_key = active_stream.api_key
                        session.ws = active_stream.ws
                        session.last_sent_count = active_stream.sent_count
                        if not audio_router.set_target(active_stream.ws):
                            disconnect_reason = f"failed to attach warmed {provider_name} stream after closure"
                            break
                        continue

                    try:
                        replacement = session._open_and_switch_to_replacement_stream(
                            audio_router,
                            active_stream,
                            current_api_key,
                            stream_index,
                            audio_format,
                            translation,
                            translation_target_lang,
                            loop,
                            "stream closed near configured duration",
                        )
                        if replacement is None:
                            disconnect_reason = f"failed to attach replacement {provider_name} stream"
                            break
                        active_stream, current_api_key, stream_index = replacement
                        session.ws = active_stream.ws
                        session.last_sent_count = active_stream.sent_count
                        continue
                    except Exception as reconnect_error:
                        disconnect_reason = f"connection closed during rollover and reconnect failed: {reconnect_error}"
                        print(f"Error reconnecting to {provider_name} after rollover closure: {reconnect_error}")
                        break

                info = relay_error_info(error)
                if info is not None:
                    relay_close = info
                    disconnect_reason = f"relay: {info[0]}"
                else:
                    disconnect_reason = f"connection closed: {error}"
                break
            except KeyboardInterrupt:
                disconnect_reason = "interrupted by user"
                notify_disconnect = False
                print("\n⏹️ Interrupted by user.")
                if session.stop_event:
                    session.stop_event.set()
                break
            except Exception as error:
                disconnect_reason = f"connection error: {error}"
                print(f"Error connecting to {provider_name}: {error}")
                break

            try:
                res = json.loads(message)
            except Exception as error:
                print(f"⚠️  Failed to parse {provider_name} response: {error}")
                continue

            if isinstance(res, dict) and session._hosted_llm.handle_frame(res):
                continue

            active_stream.sent_count, should_end, reason = hooks.process_response(
                res,
                active_stream.all_final_tokens,
                active_stream.sent_count,
                loop,
            )
            if should_end:
                disconnect_reason = reason or "stream ended"
                if hooks.should_rollover_finished(
                    reason,
                    active_stream.started_at,
                    rollover_seconds,
                ):
                    print(
                        f"🔁 {provider_name} stream #{active_stream.index} ended "
                        f"{hooks.finished_rollover_description(reason)}; "
                        "rolling over..."
                    )
                    audio_router.clear_target(active_stream.ws)
                    hooks.close_stream(active_stream, "rollover")

                    if warmup_stream is not None:
                        if warmup_stream.silence_sender is not None:
                            warmup_stream.silence_sender.stop()
                            warmup_stream.silence_sender = None
                        active_stream = warmup_stream
                        warmup_stream = None
                        current_api_key = active_stream.api_key
                        session.ws = active_stream.ws
                        session.last_sent_count = active_stream.sent_count
                        if not audio_router.set_target(active_stream.ws):
                            disconnect_reason = f"failed to attach warmed {provider_name} stream after finish"
                            break
                        continue
                    replacement = session._open_and_switch_to_replacement_stream(
                        audio_router,
                        active_stream,
                        current_api_key,
                        stream_index,
                        audio_format,
                        translation,
                        translation_target_lang,
                        loop,
                        "stream finished near configured duration",
                    )
                    if replacement is None:
                        disconnect_reason = f"failed to attach replacement {provider_name} stream after finish"
                        break
                    active_stream, current_api_key, stream_index = replacement
                    session.ws = active_stream.ws
                    session.last_sent_count = active_stream.sent_count
                    disconnect_reason = "stream rollover"
                    continue
                break

    finally:
        # Clean up background warmup future
        if warmup_future is not None:
            if warmup_future.done() and not warmup_future.cancelled():
                try:
                    leaked = warmup_future.result(timeout=0)
                    hooks.close_stream(leaked)
                except Exception:
                    pass
            else:
                warmup_future.cancel()
            warmup_future = None
        key_fetch_executor.shutdown(wait=False)
        session._relay_session_active = False
        audio_router.close()
        session._stop_audio_streamer()
        # Serialize this decision with stop(): whichever path claims the
        # close first determines whether it is a natural end or user_stop.
        with session._stream_close_lock:
            stop_requested = bool(session.stop_event and session.stop_event.is_set())
            if session.stop_event:
                session.stop_event.set()
            close_reason = "user_stop" if stop_requested else "stream_close"
            if warmup_stream is not None:
                hooks.close_stream(warmup_stream, close_reason)
            if active_stream is not None:
                hooks.close_stream(active_stream, close_reason)
            session.stop_event = None
            session.ws = None
        session.thread = None
        if notify_disconnect and not stop_requested:
            try:
                disconnect_payload = {
                    "type": "session_disconnected",
                    "reason": disconnect_reason,
                }
                if relay_close is not None:
                    tag, terminal, message = relay_close
                    disconnect_payload["code"] = tag
                    disconnect_payload["relay_terminal"] = bool(terminal)
                    disconnect_payload["message"] = message
                elif is_api_key_error_reason(disconnect_reason):
                    disconnect_payload["code"] = "api_key"
                session.last_disconnect_payload = disconnect_payload
                asyncio.run_coroutine_threadsafe(
                    session.broadcast_callback(disconnect_payload),
                    loop,
                )
            except Exception as notify_error:
                print(f"⚠️  Failed to notify clients about {provider_name} disconnect: {notify_error}")
        elif stop_requested:
            session.last_disconnect_payload = None
