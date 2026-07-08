"""Two-phase VRChat chatbox publish shared by the STT sessions' finalize path.

Kept out of ``osc_manager`` (which pulls in the vrchat OSC transport) so the
sessions and their tests can use it without that heavy dependency; it only
duck-types the manager's ``add_message_and_send`` / ``update_message_and_send``.
"""
from typing import Optional


class OscDraftPublisher:
    """混合 (hybrid) wants the fast draft on screen immediately and then replaced
    in place if the LLM changed it; 准确 (accurate) has no draft and just
    publishes the final text once, after the LLM. One instance per finalized
    sentence::

        pub = OscDraftPublisher(osc_manager, enabled=osc_on,
                                wants_draft=(mode != "translate"), speaker=speaker)
        pub.send_draft(draft_osc_text)      # before the LLM (no-op unless wants_draft)
        ...run the LLM...
        pub.publish_final(final_osc_text, no_change=no_change)
    """

    def __init__(self, manager, *, enabled: bool, wants_draft: bool, speaker: Optional[str] = None):
        self._manager = manager
        self._enabled = bool(enabled)
        self._wants_draft = bool(wants_draft)
        self._speaker = speaker
        self._handle: Optional[int] = None
        self._draft_sent = False

    def send_draft(self, text: str) -> None:
        if not self._enabled or not self._wants_draft:
            return
        text = (text or "").strip()
        if not text:
            return
        try:
            self._handle = self._manager.add_message_and_send(text, ongoing=False, speaker=self._speaker)
            self._draft_sent = True
        except Exception as error:
            print(f"OSC draft send failed: {error}")

    def publish_final(self, text: str, *, no_change: bool) -> None:
        if not self._enabled:
            return
        text = (text or "").strip()
        try:
            if self._draft_sent:
                # The draft is already on the chatbox; only replace it when the LLM
                # actually changed the text (no_change → leave the draft as-is).
                if not no_change and text:
                    self._manager.update_message_and_send(self._handle, text, ongoing=False)
            elif text:
                self._manager.add_message_and_send(text, ongoing=False, speaker=self._speaker)
        except Exception as error:
            print(f"OSC send failed: {error}")
