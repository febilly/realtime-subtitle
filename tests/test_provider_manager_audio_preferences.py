import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from server import ProviderManager


class FakeSession:
    def __init__(self, logger, broadcast_callback):
        self.logger = logger
        self.broadcast_callback = broadcast_callback
        self.audio_source = "system"
        self.microphone_device_id = ""
        self.started = False
        self.stopped = False
        self.translation_target_lang = None
        self.target_langs = None

    def stop(self):
        self.stopped = True

    def start(self, api_key, audio_format, translation_mode, loop, translation_target_lang=None):
        self.started = True
        self.start_args = {
            "api_key": api_key,
            "audio_format": audio_format,
            "translation_mode": translation_mode,
            "translation_target_lang": translation_target_lang,
        }

    def get_audio_source(self):
        return self.audio_source

    def set_audio_source(self, source):
        self.audio_source = source
        return True, "ok"

    def get_microphone_device_id(self):
        return self.microphone_device_id

    def set_microphone_device_id(self, device_id):
        self.microphone_device_id = str(device_id or "").strip()
        return True, "ok"

    def set_translation_target_lang(self, target_lang):
        self.translation_target_lang = target_lang
        return True, "ok"

    def set_target_langs(self, lang_1, lang_2):
        self.target_langs = (lang_1, lang_2)
        return True, "ok"


@pytest.mark.asyncio
async def test_provider_switch_preserves_audio_source_and_microphone_device(monkeypatch):
    logger = MagicMock()
    ipc_server = MagicMock()
    osc_manager = MagicMock()
    manager = ProviderManager(logger, ipc_server, osc_manager, AsyncMock())
    manager.loop = asyncio.get_running_loop()

    old_session = FakeSession(logger, manager.broadcast_callback)
    old_session.set_audio_source("microphone")
    old_session.set_microphone_device_id("mic-123")
    manager.web_server = SimpleNamespace(session=old_session, get_api_key=None)

    fake_session_module = SimpleNamespace(ipc_server=None)
    monkeypatch.setattr(
        manager,
        "_provider_modules",
        lambda provider: (fake_session_module, FakeSession, lambda: "env-key"),
    )

    result = await manager.apply_provider("soniox", api_key="runtime-key")

    new_session = manager.web_server.session
    assert result["started"] is True
    assert old_session.stopped is True
    assert new_session.started is True
    assert new_session.get_audio_source() == "microphone"
    assert new_session.get_microphone_device_id() == "mic-123"
    assert manager.audio_source == "microphone"
    assert manager.microphone_device_id == "mic-123"
