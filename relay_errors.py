"""Shared mapping for subtitle-server relay WebSocket close codes.

When running in relay (hosted) mode the upstream WebSocket is the
subtitle-server relay, which closes with application-specific codes documented
in subtitle-server/docs/api-user.md. Both the Soniox and Gemini sessions use
this module to turn a numeric close code into a structured tag that the frontend
maps to a localized message, plus a "terminal" flag telling the client whether
auto-restart should be suppressed.
"""

# code -> (tag, terminal)
# terminal=True means retrying immediately is pointless (login/billing/model);
# the client should stop auto-restart and usually prompt the user.
RELAY_CLOSE_CODES = {
    4001: ("billing_exhausted", True),
    4002: ("upstream_key_error", False),
    4003: ("forbidden", True),
    4004: ("model_not_allowed", True),
    4005: ("concurrency_limit", True),
}

# Default English fallback messages (the frontend localizes by tag).
RELAY_TAG_MESSAGES = {
    "billing_exhausted": "Credits or free quota exhausted.",
    "upstream_key_error": "No upstream key available; please try again later.",
    "forbidden": "Login expired or account not allowed; please sign in again.",
    "model_not_allowed": "The server does not allow this model.",
    "concurrency_limit": "Too many simultaneous free sessions.",
}


def relay_close_info(code):
    """Return (tag, terminal, message) for a relay close code, or None.

    Returns None for non-relay / normal close codes so callers can fall back to
    their existing handling.
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    entry = RELAY_CLOSE_CODES.get(code)
    if entry is None:
        return None
    tag, terminal = entry
    return tag, terminal, RELAY_TAG_MESSAGES.get(tag, "Relay connection closed.")
