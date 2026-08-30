import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


@pytest.fixture
def restore_config_module():
    """Reloading config replaces sys.modules['config']; other test modules
    (e.g. test_ipc_server) install mocks there or pop it entirely, so snapshot
    and restore to keep reload() working and avoid cross-test pollution."""
    original = sys.modules.get("config")
    yield
    if original is not None:
        sys.modules["config"] = original
    else:
        sys.modules.pop("config", None)


def _reload_config():
    # Whatever is currently registered may be another test's mock (or absent);
    # force the real module back into sys.modules before reload().
    sys.modules["config"] = config
    return importlib.reload(config)


def test_vr_overlay_enabled_env_parsing(restore_config_module):
    old = os.environ.get("VR_OVERLAY_ENABLED")
    try:
        os.environ["VR_OVERLAY_ENABLED"] = "1"
        # 重新加载 config 以观察 env 生效
        _reload_config()
        assert config.VR_OVERLAY_ENABLED is True

        os.environ["VR_OVERLAY_ENABLED"] = ""
        _reload_config()
        assert config.VR_OVERLAY_ENABLED is False
    finally:
        if old is None:
            os.environ.pop("VR_OVERLAY_ENABLED", None)
        else:
            os.environ["VR_OVERLAY_ENABLED"] = old
        _reload_config()


def test_vr_overlay_exe_path_uses_frozen_bundle_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert Path(config.get_vr_overlay_exe_path()) == tmp_path / "vr_overlay" / "RinBridgeOverlay.exe"


def test_vr_overlay_exe_path_source_prefers_bundled_then_cargo(monkeypatch, tmp_path):
    """Source runs: bundle-root copy wins; otherwise fall back to cargo output."""
    monkeypatch.delattr(config.sys, "_MEIPASS", raising=False)
    root = tmp_path / "repo"
    bundle_dir = root / "vr_overlay"
    bundle_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "__file__", str(root / "config.py"))

    bundled = bundle_dir / "RinBridgeOverlay.exe"
    bundled.write_bytes(b"MZ")
    assert Path(config.get_vr_overlay_exe_path()) == bundled

    bundled.unlink()
    assert Path(config.get_vr_overlay_exe_path()) == (
        root / "vr_overlay" / "target" / "release" / "RinBridgeOverlay.exe"
    )
