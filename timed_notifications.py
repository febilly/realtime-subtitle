"""Persistent eligible usage days and admission for nonessential timed notices."""
import json
import secrets
import time
from datetime import datetime

import config
import local_store

STATE_KEY = "timedNotifications.v1"


def _state(store):
    try:
        value = json.loads(store.get(STATE_KEY, "{}"))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _number(value):
    try:
        return max(0, float(value or 0))
    except (ValueError, TypeError):
        return 0


def _save(store, state):
    store[STATE_KEY] = json.dumps(state)


def record_usage(now=None, *, relay_mode=False, local_model=False):
    """Count all BYOK use, and only local-model use in hosted mode."""
    if relay_mode and not local_model:
        return
    timestamp = time.time() if now is None else now

    def update(store):
        state = _state(store)
        previous = _number(state.get("last_use"))
        today = datetime.fromtimestamp(timestamp).date()
        if not previous or today > datetime.fromtimestamp(previous).date():
            state["usage_days"] = int(_number(state.get("usage_days"))) + 1
        # Keep the latest timestamp when clocks move backwards.
        state["last_use"] = max(previous, timestamp)
        _save(store, state)

    local_store.transaction(update)


def snapshot(now=None):
    timestamp = time.time() if now is None else now
    state = _state(local_store.load())
    cooldown = config.TIMED_NOTIFICATION_COOLDOWN_SECONDS
    last = _number(state.get("last_shown"))
    return {
        "retry_after_seconds": max(0, last + cooldown - timestamp) if last and cooldown else 0,
        "star_mode": config.LOCAL_MODEL_STAR_REMINDER,
        "usage_days": int(_number(state.get("usage_days"))),
        "star_disabled": state.get("star_disabled") is True,
        "notices": [
            {"notice_id": key, "notice": notice}
            for key, notice in state.get("server_notices", {}).items()
        ],
    }


def remember_server_notice(notice):
    def update(store):
        state = _state(store)
        pending = state.setdefault("server_notices", {})
        # A reconnect may return the same reminder while it is still waiting.
        for key, value in pending.items():
            if value.get("id") == notice.get("id"):
                return key
        key = secrets.token_hex(12)
        pending[key] = notice
        _save(store, state)
        return key

    return local_store.transaction(update)


def claim(kind, now=None, notice_id=None):
    """Reserve a slot only when a due notice is actually about to be displayed."""
    if kind not in ("server", "invite", "update", "local_star"):
        raise ValueError("Unknown timed notification")
    timestamp = time.time() if now is None else now

    def update(store):
        state = _state(store)
        if kind == "server" and notice_id not in state.get("server_notices", {}):
            return {"shown": False}
        last = _number(state.get("last_shown"))
        cooldown = config.TIMED_NOTIFICATION_COOLDOWN_SECONDS
        retry = max(0, last + cooldown - timestamp) if last and cooldown else 0
        if retry:
            return {"shown": False, "retry_after_seconds": retry}
        if kind == "local_star":
            mode = config.LOCAL_MODEL_STAR_REMINDER
            if mode == "never" or (mode != "always" and (
                state.get("star_disabled") is True or _number(state.get("usage_days")) < 7
            )):
                return {"shown": False}
            state["usage_days"] = 0
            state["star_shown_count"] = int(_number(state.get("star_shown_count"))) + 1
        if kind == "server":
            del state["server_notices"][notice_id]
        state["last_shown"] = timestamp
        _save(store, state)
        return {"shown": True, "retry_after_seconds": cooldown,
                "star_shown_count": state.get("star_shown_count", 0)}

    return local_store.transaction(update)


def disable_star():
    def update(store):
        state = _state(store)
        state["star_disabled"] = True
        _save(store, state)

    local_store.transaction(update)
