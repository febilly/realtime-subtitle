"""Hosted-mode telemetry for enabled feature packs and runtime feature usage.

Multiplexes lightweight JSON control frames onto the active relay WebSocket
(`feature_packs` and `feature_used`) with zero extra HTTP requests and zero
activity outside `config.RELAY_MODE`.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Iterable, Optional

import realtime_subtitle.core.config as config
import realtime_subtitle.core.local_store as local_store

logger = logging.getLogger(__name__)

FEATURE_PACK_IDS = ("vrchat", "learning", "obs", "transcript")
FEATURE_IDS = (
    "osc",
    "vr_overlay",
    "furigana_ja",
    "pinyin",
    "ko_roman",
    "obs",
    "autosave",
    "export",
)
READING_LANG_TO_FEATURE = {
    "ja": "furigana_ja",
    "zh": "pinyin",
    "ko": "ko_roman",
}
PENDING_EXPORT_STORE_KEY = "pendingFeatureUsageExport.v1"


def normalize_enabled_packs(packs: dict | Iterable[str] | None) -> list[str]:
    """Return ordered canonical pack IDs that are enabled."""
    if isinstance(packs, dict):
        return [pack_id for pack_id in FEATURE_PACK_IDS if bool(packs.get(pack_id, False))]
    if packs is None:
        return []
    seen = {str(item).strip().lower() for item in packs if isinstance(item, str)}
    return [pack_id for pack_id in FEATURE_PACK_IDS if pack_id in seen]


class FeatureUsageReporter:
    """Tracks the active relay stream and reports feature-pack / usage frames."""

    def __init__(
        self,
        *,
        get_enabled_packs: Optional[Callable[[], dict | Iterable[str] | None]] = None,
        get_active_stream_features: Optional[Callable[[], Iterable[str]]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._active_ws = None
        self._sent_features_on_stream: set[str] = set()
        self._get_enabled_packs = get_enabled_packs
        self._get_active_stream_features = get_active_stream_features

    def set_providers(
        self,
        *,
        get_enabled_packs: Optional[Callable[[], dict | Iterable[str] | None]] = None,
        get_active_stream_features: Optional[Callable[[], Iterable[str]]] = None,
    ) -> None:
        with self._lock:
            if get_enabled_packs is not None:
                self._get_enabled_packs = get_enabled_packs
            if get_active_stream_features is not None:
                self._get_active_stream_features = get_active_stream_features

    @staticmethod
    def is_relay_active() -> bool:
        return bool(getattr(config, "RELAY_MODE", False))

    def enabled_packs(self) -> list[str]:
        provider = self._get_enabled_packs
        if not callable(provider):
            return []
        try:
            return normalize_enabled_packs(provider())
        except Exception:
            logger.debug("Failed to read enabled feature packs", exc_info=True)
            return []

    def _active_stream_features(self) -> list[str]:
        provider = self._get_active_stream_features
        if not callable(provider):
            return []
        try:
            items = list(provider() or ())
        except Exception:
            logger.debug("Failed to read active stream features", exc_info=True)
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw in items:
            feature = str(raw or "").strip().lower()
            if feature in FEATURE_IDS and feature not in seen:
                seen.add(feature)
                result.append(feature)
        return result

    @staticmethod
    def _send_frame(ws, payload: dict) -> bool:
        if ws is None or not hasattr(ws, "send"):
            return False
        try:
            ws.send(json.dumps(payload, separators=(",", ":")))
            return True
        except Exception:
            logger.debug("Failed to send feature usage frame %s", payload.get("type"), exc_info=True)
            return False

    @staticmethod
    def _has_pending_export() -> bool:
        try:
            store = local_store.load()
            return str(store.get(PENDING_EXPORT_STORE_KEY, "")).lower() == "true"
        except Exception:
            return False

    @staticmethod
    def _set_pending_export(pending: bool) -> None:
        try:
            def _update(data: dict) -> None:
                if pending:
                    data[PENDING_EXPORT_STORE_KEY] = "true"
                else:
                    data.pop(PENDING_EXPORT_STORE_KEY, None)

            local_store.transaction(_update)
        except Exception:
            logger.debug("Failed to persist pending export telemetry flag", exc_info=True)

    def attach_stream(self, ws) -> None:
        """Bind a newly active relay stream and push initial packs + pending/active features."""
        if ws is None or not self.is_relay_active():
            self.detach_stream()
            return
        with self._lock:
            if self._active_ws is ws:
                return
            self._active_ws = ws
            self._sent_features_on_stream.clear()
            self._send_frame(ws, {"type": "feature_packs", "enabled": self.enabled_packs()})
            for feature in self._active_stream_features():
                if self._send_frame(ws, {"type": "feature_used", "feature": feature}):
                    self._sent_features_on_stream.add(feature)
            if self._has_pending_export():
                if self._send_frame(ws, {"type": "feature_used", "feature": "export"}):
                    self._sent_features_on_stream.add("export")
                    self._set_pending_export(False)

    def detach_stream(self, ws=None) -> None:
        """Clear the active stream reference when a relay WS stops or sleeps."""
        with self._lock:
            if ws is None or self._active_ws is ws:
                self._active_ws = None
                self._sent_features_on_stream.clear()

    def report_packs_changed(self) -> bool:
        """Send updated `feature_packs` state if a relay stream is currently live."""
        if not self.is_relay_active():
            return False
        with self._lock:
            ws = self._active_ws
            if ws is None:
                return False
            return self._send_frame(ws, {"type": "feature_packs", "enabled": self.enabled_packs()})

    def report_feature_used(self, feature: str) -> bool:
        """Record a feature as used on the current relay stream (deduplicated per stream)."""
        normalized = str(feature or "").strip().lower()
        if normalized not in FEATURE_IDS:
            return False
        if not self.is_relay_active():
            return False
        with self._lock:
            ws = self._active_ws
            if ws is None:
                if normalized == "export":
                    self._set_pending_export(True)
                return False
            if normalized in self._sent_features_on_stream:
                return False
            if self._send_frame(ws, {"type": "feature_used", "feature": normalized}):
                self._sent_features_on_stream.add(normalized)
                if normalized == "export" and self._has_pending_export():
                    self._set_pending_export(False)
                return True
            if normalized == "export":
                self._set_pending_export(True)
            return False
