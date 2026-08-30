"""VR 浮层后端协议: 字幕事件 → snapshot → /vr_ws 广播。

VROverlay 是 ForeignSpeech 事件流的订阅者 (由 ipc_server.broadcast_foreign_speech
尾部挂接)。只负责「字幕该以什么参数显示」; 空间坐标/角度换算/SteamVR 状态
全部在 Rust 侧 (vr_overlay/), 见设计文档 §2 边界原则。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _push_log(line: str) -> None:
    """诊断用: [vr-push] 行同时进控制台与 exe/cwd 旁的 vr-push.log (取证用)."""
    print(line, flush=True)
    try:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path.cwd()
        with open(base / "vr-push.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass

CALIBRATION_DEFAULT = {
    "anchor": "head_locked",
    "offset_x": 0.0,
    "offset_y": -0.40,
    "distance": 1.1,
    "text_scale": 1.0,
    "background_alpha": 0.24,
}


def blank_block(seq: int) -> dict:
    return {
        "id": f"rt:clear:{seq}",
        "occupant_key": "peer:realtime",
        "appearance_seq": seq,
        "channel": "peer",
        "block_variant": "finalized",
        "primary_text": "",
        "secondary_text": "",
        "secondary_enabled": False,
    }


class VROverlay:
    """字幕状态机: 事件 → snapshot; finalized 8s、live 最多 30s 后清屏。"""

    def __init__(
        self,
        broadcast: Callable[[dict], Awaitable[None]],
        clear_after: float = 8.0,
        live_clear_after: float = 30.0,
    ) -> None:
        self.broadcast = broadcast
        self.clear_after = clear_after
        self.live_clear_after = live_clear_after
        self.revision = 0
        self._latest: dict = self._snapshot(0, [])
        self._clear_task: Optional[asyncio.Task] = None
        self._live_clear_task: Optional[asyncio.Task] = None

    def latest_snapshot(self) -> dict:
        """新连接 resync 用 (返回当前快照的浅拷贝)。"""
        return dict(self._latest)

    async def push(
        self,
        source_text: str,
        detected_language: Optional[str] = None,
        translation: Optional[str] = None,
        finalized: bool = True,
    ) -> None:
        """FOREIGN_SPEECH 事件 → snapshot (主行=译文, 副行=原文)。"""
        if not source_text and not translation:
            return
        latest_blocks = (self._latest or {}).get("payload", {}).get("blocks") or []
        if (
            not translation
            and latest_blocks
            and latest_blocks[0].get("secondary_enabled")
        ):
            # A translated pair stays visible until a later translation
            # arrives.  Do not flash the next sentence's source-only draft
            # over it.
            _push_log(
                f"[vr-push] {_now()} SUPPRESS source-draft "
                f"over translated pair src={source_text[:30]!r}"
            )
            return
        if self._clear_task is not None:
            self._clear_task.cancel()
            self._clear_task = None
        if finalized and self._live_clear_task is not None:
            self._live_clear_task.cancel()
            self._live_clear_task = None
        self.revision += 1
        translation_present = bool(translation)
        block = {
            "id": f"rt:{self.revision}",
            "occupant_key": "peer:realtime",
            "appearance_seq": self.revision,
            "channel": "peer",
            "block_variant": "finalized",
            "primary_text": translation or source_text,
            "secondary_text": source_text if translation_present else "",
            "secondary_enabled": translation_present,
            "primary_language": None,
            "secondary_language": detected_language if translation_present else None,
        }
        self._latest = self._snapshot(self.revision, [block])
        _push_log(
            f"[vr-push] {_now()} rev={self.revision} "
            f"finalized={finalized} trans={'y' if translation_present else 'n'} "
            f"src={source_text[:30]!r} prim={(translation or source_text)[:30]!r}"
        )
        await self.broadcast(self._latest)
        if finalized and self.clear_after and self.clear_after > 0:
            self._clear_task = asyncio.create_task(self._clear_later())
        elif (
            not finalized
            and self.live_clear_after
            and self.live_clear_after > 0
            and self._live_clear_task is None
        ):
            self._live_clear_task = asyncio.create_task(
                self._clear_later(self.live_clear_after)
            )

    async def push_view(self, lines: list) -> None:
        """镜像帧: 单块双行 (主行=译文最新值, 副行=原文最新值; 见
        vr_subtitle_mirror)。单写者、恒定 occupant 身份, Rust 写穿后行不动。
        """
        chosen = lines[-2:]
        self.revision += 1
        blocks = []
        for idx, line in enumerate(chosen):
            secondary = line.get("secondary") or ""
            blocks.append(
                {
                    "id": f"rt:{line.get('key', self.revision)}",
                    "occupant_key": line.get("key") or "peer:realtime",
                    "appearance_seq": line.get("seq") or self.revision,
                    "channel": "peer",
                    "block_variant": "finalized",
                    "primary_text": line.get("primary") or "",
                    "secondary_text": secondary,
                    "secondary_enabled": bool(secondary),
                    "primary_language": line.get("lang"),
                    "secondary_language": line.get("secondary_lang") if secondary else None,
                }
            )
        self._latest = self._snapshot(self.revision, blocks)
        _push_log(
            f"[vr-push] {_now()} rev={self.revision} mirror "
            + (" | ".join(
                f"[{b['occupant_key']}#{b['appearance_seq']}] "
                f"prim={b['primary_text'][:40]!r} sec={b['secondary_text'][:40]!r}"
                for b in blocks
            ) or "(empty)")
        )
        await self.broadcast(self._latest)

    async def _clear_later(self, delay: Optional[float] = None) -> None:
        try:
            await asyncio.sleep(self.clear_after if delay is None else delay)
        except asyncio.CancelledError:
            return
        self.revision += 1
        self._latest = self._snapshot(self.revision, [])
        await self.broadcast(self._latest)

    def _snapshot(self, revision: int, blocks: list) -> dict:
        return {
            "type": "snapshot",
            "payload": {
                "revision": revision,
                "calibration": dict(CALIBRATION_DEFAULT),
                "blocks": blocks,
            },
        }
