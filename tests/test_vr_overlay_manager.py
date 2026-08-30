import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vr_overlay_manager import VROverlayManager


def fake_exe(tmp_path):
    return tmp_path / "RinBridgeOverlay.exe"


class TestVROverlayManager:
    def test_manifest_written_with_contract(self, tmp_path):
        mgr = VROverlayManager(
            bridge_url="ws://127.0.0.1:8080/vr_ws",
            session_token="tok-123",
            exe_path=fake_exe(tmp_path),
            work_dir=tmp_path,
            parent_pid=1234,
        )
        manifest = mgr.write_manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["contract_version"] == 6
        assert data["bridge_url"] == "ws://127.0.0.1:8080/vr_ws"
        assert data["session_token"] == "tok-123"
        assert data["parent_pid"] == 1234
        assert data["startup_deadline_ms"] == 30000

    def test_event_parsing_updates_status(self):
        mgr = VROverlayManager(
            bridge_url="ws://127.0.0.1:8080/vr_ws",
            session_token="tok",
            exe_path=MagicMock(),
            work_dir=MagicMock(),
            parent_pid=1,
        )
        mgr._on_event_line('EVENT {"type":"overlay_ready","overlay_instance_id":"x"}')
        assert mgr.status == "ready"
        mgr._on_event_line('EVENT {"type":"no_hmd","reason":"steamvr_not_running"}')
        assert mgr.status == "no_hmd"
        mgr._on_event_line('EVENT {"type":"auth_failed","reason":"bad token"}')
        assert mgr.status == "auth_failed"

    def test_auto_fallback_flag_on_no_hmd(self):
        mgr = VROverlayManager(
            bridge_url="ws://127.0.0.1:8080/vr_ws",
            session_token="tok",
            exe_path=MagicMock(),
            work_dir=MagicMock(),
            parent_pid=1,
        )
        mgr._on_event_line('EVENT {"type":"no_hmd","reason":"hmd_not_found"}')
        assert mgr.should_fallback is True


class TestMutexDecision:
    def test_vr_on_blocks_desktop(self):
        from vr_overlay_manager import decide_desktop_overlay
        assert decide_desktop_overlay(True, "ready") is False
        assert decide_desktop_overlay(True, "starting") is False

    def test_vr_off_restores_desktop(self):
        from vr_overlay_manager import decide_desktop_overlay
        assert decide_desktop_overlay(False, "stopped") is True

    def test_no_hmd_fallback_keeps_vr_occupying(self):
        from vr_overlay_manager import decide_desktop_overlay
        assert decide_desktop_overlay(True, "no_hmd") is False
