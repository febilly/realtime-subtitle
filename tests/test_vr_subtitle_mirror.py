"""VR 镜像: 单块双行模型 (主行=译文最新值, 副行=原文最新值)。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vr_overlay import VROverlay  # noqa: E402
from vr_subtitle_mirror import VRSubtitleMirror  # noqa: E402


def tok(text, *, status="none", speaker="1", lang="ja", sid=None, final=True):
    tk = {
        "text": text,
        "speaker": speaker,
        "language": lang,
        "translation_status": status,
        "is_final": final,
    }
    if sid:
        tk["llm_sentence_id"] = sid
    return tk


SEP = {"is_separator": True, "is_final": True}


def make_mirror():
    snaps = []
    vr = VROverlay(broadcast=lambda s: asyncio.sleep(0))
    vr.broadcast = lambda s: snaps.append(s) or asyncio.sleep(0)
    mirror = VRSubtitleMirror(vr.push_view)
    return mirror, snaps


def update_msg(final=None, non_final=None):
    return {"type": "update", "final_tokens": final or [], "non_final_tokens": non_final or []}


def blocks_of(snap):
    return snap["payload"]["blocks"]


def run(mirror, msg):
    asyncio.run(mirror.handle_broadcast(msg))


def _peek(snaps):
    return blocks_of(snaps[-1])


def test_always_at_most_one_block():
    """单块模型: 任何阶段画面都只有一个 block。"""
    mirror, snaps = make_mirror()
    run(mirror, update_msg(non_final=[tok("喂喂", final=False)]))
    assert len(_peek(snaps)) == 1
    run(mirror, update_msg(final=[tok("喂喂。", sid="s1"), tok("喂喂译", status="translation", sid="s1")]))
    assert len(_peek(snaps)) == 1
    run(mirror, update_msg(final=[tok("第二句", sid=None, final=False)]))
    assert len(_peek(snaps)) == 1


def test_translation_row_holds_while_source_row_updates():
    """新句原文只顶原文行, 不动译文行; 新译文出来才顶译文行。"""
    mirror, snaps = make_mirror()
    # 首句 live: 尚无译文 → 原文兜底大字行
    run(mirror, update_msg(non_final=[tok("第一句说到", final=False)]))
    b = _peek(snaps)[0]
    assert (b["primary_text"], b["secondary_text"]) == ("第一句说到", "")

    # 首句译文落地 → 主行=译文, 副行=原文
    run(mirror, update_msg(final=[tok("第一句说到。", sid="s1"), tok("译一", status="translation", sid="s1")], non_final=[]))
    b = _peek(snaps)[0]
    assert (b["primary_text"], b["secondary_text"]) == ("译一", "第一句说到。")

    # 第二句开说: 只有原文行被顶掉, 译文行保持
    run(mirror, update_msg(non_final=[tok("第二句正在说", final=False)]))
    b = _peek(snaps)[0]
    assert (b["primary_text"], b["secondary_text"]) == ("译一", "第二句正在说")

    # 第二句译文落地 → 译文行才被替换
    run(mirror, update_msg(final=[tok("第二句正在说。", sid="s2"), tok("译二", status="translation", sid="s2")], non_final=[]))
    b = _peek(snaps)[0]
    assert (b["primary_text"], b["secondary_text"]) == ("译二", "第二句正在说。")


def test_late_refine_updates_translation_row_in_place():
    mirror, snaps = make_mirror()
    run(mirror, update_msg(final=[tok("A。", sid="s1"), tok("粗译", status="translation", sid="s1")]))
    run(mirror, {"type": "refine_result", "sentence_id": "s1", "refined_translation": "精译", "target_lang": "zh"})
    b = _peek(snaps)[0]
    assert (b["primary_text"], b["secondary_text"]) == ("精译", "A。")

    # 更老句子的迟到 refine 不得覆盖更新句子的译文行
    run(mirror, update_msg(final=[tok("B。", sid="s2"), tok("译B", status="translation", sid="s2")]))
    run(mirror, {"type": "refine_result", "sentence_id": "s1", "refined_translation": "精译改"})
    assert _peek(snaps)[0]["primary_text"] == "译B"


def test_refine_no_change_is_ignored():
    mirror, snaps = make_mirror()
    run(mirror, update_msg(final=[tok("A。", sid="s1"), tok("t1", status="translation", sid="s1")]))
    n = len(snaps)
    run(mirror, {"type": "refine_result", "sentence_id": "s1", "no_change": True})
    assert len(snaps) == n


def test_next_sentence_draft_does_not_pollute_translated_row():
    """回归: 句1 定稿(译文未回), 句2 live 不得与句1 混句; 迟到译文按 sid 归位。"""
    mirror, snaps = make_mirror()
    run(mirror, update_msg(final=[tok("こんにちは。", sid="s1")]))
    run(mirror, update_msg(non_final=[tok("次の文です", lang=None, final=False)]))
    run(
        mirror,
        update_msg(
            final=[tok("你好。", status="translation", sid="s1")],
            non_final=[tok("次の文です", lang=None, final=False)],
        ),
    )
    b = _peek(snaps)[0]
    # 译文行 = 句1 译文; 原文行 = 句2 live —— 两行各自干净
    assert b["primary_text"] == "你好。"
    assert b["secondary_text"] == "次の文です"


def test_clear_empties_view_and_revisions_are_monotonic():
    mirror, snaps = make_mirror()
    run(mirror, update_msg(final=[tok("喂喂。", sid="s1")]))
    run(mirror, {"type": "clear"})
    assert _peek(snaps) == []
    revs = [s["payload"]["revision"] for s in snaps]
    assert revs == sorted(revs) and len(set(revs)) == len(revs)


def test_single_block_identity_is_constant():
    """单块身份恒定 → Rust 写穿, 行位置/缓存永不动。"""
    mirror, snaps = make_mirror()
    run(mirror, update_msg(non_final=[tok("一", final=False)]))
    first = _peek(snaps)[0]
    run(mirror, update_msg(final=[tok("一。", sid="s1"), tok("译", status="translation", sid="s1")]))
    second = _peek(snaps)[0]
    assert (first["occupant_key"], first["appearance_seq"]) == (second["occupant_key"], second["appearance_seq"])


def test_irrelevant_broadcasts_are_ignored():
    mirror, snaps = make_mirror()
    run(mirror, {"type": "ipc_status", "connected": True})
    run(mirror, {"type": "speaker_labels_changed", "enabled": True})
    assert snaps == []
