"""VR 浮层进程管理器: spawn/kill RinBridgeOverlay.exe + manifest + EVENT 状态。

契约与 glue.py 一致 (contract_version 6)。状态经 exe 的 stderr EVENT 行上报:
overlay_ready / auth_failed / connect_failed / no_hmd / startup_error。
Python 侧只做生命周期与状态, 空间/渲染全在 Rust。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

CONTRACT_VERSION = 6


class VROverlayManager:
    def __init__(
        self,
        bridge_url: str,
        session_token: str,
        exe_path: Path,
        work_dir: Path,
        parent_pid: int,
    ) -> None:
        self.bridge_url = bridge_url
        self.session_token = session_token
        self.exe_path = Path(exe_path)
        self.work_dir = Path(work_dir)
        self.parent_pid = parent_pid
        self._proc: Optional[subprocess.Popen] = None
        self._pump: Optional[threading.Thread] = None
        self.status: str = "stopped"  # stopped|starting|ready|no_hmd|auth_failed|connect_failed|crashed
        self.should_fallback = False  # no_hmd/auth_failed → server 回退桌面模式

    def manifest_path(self) -> Path:
        return self.work_dir / "overlay_manifest.json"

    def write_manifest(self) -> Path:
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "app_version": "0.1.0",
            "overlay_instance_id": f"realtime-subtitle-{os.getpid()}",
            "bridge_url": self.bridge_url,
            "session_token": self.session_token,
            "parent_pid": self.parent_pid,
            "startup_deadline_ms": 30000,
            "log_dir": str(self.work_dir / "overlay-logs"),
            "log_level": "INFO",
            "locale": "zh-CN",
            "logging_mode": "basic",
        }
        path = self.manifest_path()
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    def is_open(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.is_open():
            return True
        if not self.exe_path.exists():
            print(f"⚠️  VR overlay exe not found: {self.exe_path}")
            self.status = "crashed"
            return False
        manifest = self.write_manifest()
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            self._proc = subprocess.Popen(
                [str(self.exe_path), "--config", str(manifest)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
        except Exception as error:
            print(f"⚠️  Failed to launch VR overlay: {error}")
            self._proc = None
            self.status = "crashed"
            return False
        self.status = "starting"
        self._pump = threading.Thread(target=self._pump_stdout, daemon=True)
        self._pump.start()
        return True

    def close(self) -> bool:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self.status = "stopped"
        self.should_fallback = False
        return False

    def _pump_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                if line.startswith("EVENT "):
                    self._on_event_line(line[len("EVENT "):])
        except Exception as error:
            print(f"⚠️  VR overlay stdout pump error: {error}")
        # 进程退出 → 若未显式关闭, 视为崩溃
        if self._proc is not None and self._proc.poll() is not None and self.status not in ("stopped",):
            self.status = "crashed"

    def _on_event_line(self, payload: str) -> None:
        # `_pump_stdout` 已剥掉 "EVENT " 前缀; 直接调用这里时也可能带上,
        # 故对两种情况都容忍。
        if payload.startswith("EVENT "):
            payload = payload[len("EVENT "):]
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        etype = event.get("type")
        if etype == "overlay_ready":
            self.status = "ready"
        elif etype == "no_hmd":
            self.status = "no_hmd"
            self.should_fallback = True
        elif etype == "auth_failed":
            self.status = "auth_failed"
            self.should_fallback = True
        elif etype == "connect_failed":
            self.status = "connect_failed"
        elif etype == "startup_error":
            self.status = "crashed"


def decide_desktop_overlay(vr_enabled: bool, vr_status: str) -> bool:
    """互斥编排: VR 开着(含启动中)就不拉起桌面浮层; VR 回退时恢复桌面。"""
    if vr_enabled and vr_status in ("starting", "ready", "no_hmd", "auth_failed", "connect_failed", "crashed"):
        return False
    return True
