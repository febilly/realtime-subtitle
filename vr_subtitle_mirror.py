"""VR 字幕镜像: 订阅与桌面浮窗完全相同的 /ws 广播, 用同一分句模型出内容。

显示模型(用户定义, 2026-08-30): 一个框, 两行, 各行独立更新——
- 译文行(主行): 最近一句「已出译文」的译文; 新译文出来才顶旧译文。
- 原文行(副行): 正在说/刚说完的原文(live 逐字增长); 新句原文直接顶旧原文。
映射到协议恰好是一个 snapshot block(primary=译文行, secondary=原文行),
单写者、单块、恒定位置, 无多槽分配/行序问题。

分句复用 subtitle_model.SubtitleModel(与 overlay_window 同一份代码);
VR 侧启用 sid 分行(后端 pairer 权威句身份); LLM refine 按 sentence_id
原位覆盖(与桌面同语义), 覆盖后若命中的是「最近已译句」, 译文行就地更新。
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from subtitle_model import SubtitleModel, sentence_sid

RefineMapCap = 200

# 单块恒定身份: Rust 写穿后行位置/缓存永不动。
_LIVE_KEY = "peer:live"
_LIVE_SEQ = 1


def join_token_text(tokens: list) -> str:
    return "".join(str(tk.get("text") or "") for tk in tokens).strip()


class VRSubtitleMirror:
    """把桌面广播折算成「译文行 + 原文行」单块 snapshot。"""

    def __init__(self, vr_push: Callable[[list], Awaitable[None]]):
        # sid 分行只在 VR 镜像启用; 桌面浮窗继续用默认(旧启发式)模型。
        self.model = SubtitleModel(sid_boundaries=True)
        self.vr_push = vr_push
        self._refined_by_sid: dict[str, str] = {}
        self._refined_lang_by_sid: dict[str, str] = {}
        self._last_lines: Optional[list] = None
        self._update_count = 0

    async def handle_broadcast(self, data: dict) -> None:
        """入口: web_server.broadcast_to_clients 的旁路订阅 (与浮窗同源)。"""
        mtype = data.get("type")
        if mtype == "update":
            self.model.apply_update(data)
            self._update_count += 1
            if self._update_count % 200 == 0:
                self.model.trim_final_tokens_to_recent_sentences(40)
        elif mtype == "refine_result":
            self._apply_refine_result(data)
        elif mtype == "clear":
            self.model.clear(preserve_existing=bool(data.get("preserve_existing")))
            if not data.get("preserve_existing"):
                self._refined_by_sid.clear()
                self._refined_lang_by_sid.clear()
        else:
            return
        await self._push_view()

    # ── 与 overlay_window._apply_refine_result / _sentence_translation_override 同语义 ──
    def _apply_refine_result(self, data: dict) -> None:
        sid = data.get("sentence_id")
        if not sid or data.get("no_change"):
            return
        refined = (data.get("refined_translation") or "").strip()
        if not refined:
            return
        sid = str(sid)
        self._refined_by_sid[sid] = refined
        target_lang = (data.get("target_lang") or "").strip()
        if target_lang:
            self._refined_lang_by_sid[sid] = target_lang
        while len(self._refined_by_sid) > RefineMapCap:
            self._refined_by_sid.pop(next(iter(self._refined_by_sid)))
        while len(self._refined_lang_by_sid) > RefineMapCap:
            self._refined_lang_by_sid.pop(next(iter(self._refined_lang_by_sid)))

    def _sentence_override(self, sentence: dict) -> Optional[tuple]:
        sid = sentence_sid(sentence)
        if not sid:
            return None
        text = self._refined_by_sid.get(sid)
        if not text:
            return None
        lang = sentence.get("translation_lang") or self._refined_lang_by_sid.get(sid)
        return text, lang

    # ── 出帧 ──
    def _sentences(self) -> list:
        rows = []
        for block in self.model.build_blocks():
            for s in block["sentences"]:
                original = join_token_text(s["original"])
                override = self._sentence_override(s)
                translation = (override[0] if override else join_token_text(s["translation"])) or ""
                if not original and not translation:
                    continue
                rows.append(
                    {
                        "original": original,
                        "translation": translation,
                        "translated": bool(translation),
                        "src_lang": s.get("original_lang"),
                        "trans_lang": (s.get("translation_lang") if translation else None)
                        or (s.get("original_lang") if override else None),
                    }
                )
        return rows

    def visible_lines(self) -> list:
        """一个框, 两行, 各行独立取「自己那一行的最新值」:
        译文行 = 最近一个已译句的译文, 新译文才顶旧的;
        原文行 = 最后一句的原文(live), 新原文直接顶旧的。
        尚无任何译文时原文兜底进大字行 (避免首句只有小字)。"""
        rows = self._sentences()
        if not rows:
            return []
        translation = next((r for r in reversed(rows) if r["translated"]), None)
        last = rows[-1]
        if translation is None:
            primary, secondary = last["original"], ""
            primary_lang = last["src_lang"]
        else:
            primary, secondary = translation["translation"], last["original"]
            primary_lang = translation["trans_lang"]
        if not primary and not secondary:
            return []
        return [
            {
                "primary": primary,
                "secondary": secondary,
                "lang": primary_lang,
                "secondary_lang": last["src_lang"],
                "key": _LIVE_KEY,
                "seq": _LIVE_SEQ,
            }
        ]

    async def _push_view(self) -> None:
        lines = self.visible_lines()
        if lines == self._last_lines:
            return
        self._last_lines = lines
        await self.vr_push(lines)
