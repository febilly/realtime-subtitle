import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import overlay_window
from overlay_window import OverlayWindow, SubtitleModel


def row(identifier, source, *, translation="", revision=1, order=0, final=False):
    return dict(id=identifier, source=source, translation=translation, revision=revision,
                order=order, source_language="zh", target_language="en",
                requires_translation=True, is_final=final)


def sources(model):
    return ["".join(t["text"] for t in s["original"])
            for b in model.build_blocks() for s in b["sentences"]]


def token(text):
    return dict(text=text, speaker="0", translation_status="original", is_final=True)


def test_local_rows_update_in_place_and_ignore_stale_revisions():
    model = SubtitleModel()
    model.apply_update({"local_segments": [row("a", "第一句。"), row("b", "第二句。", order=1)]})
    assert sources(model) == ["第一句。", "第二句。"]
    model.apply_update({"local_segments": [row("a", "第一句，", translation="First.", revision=3)]})
    model.apply_update({"local_segments": [row("a", "不能回来", revision=2)]})
    assert sources(model) == ["第一句，", "第二句。"]
    sentences = model.build_blocks()[0]["sentences"]
    assert [t["text"] for t in sentences[0]["translation"]] == ["First."]
    assert sentences[1]["translation"] == []


def test_local_removal_tombstone_reset_and_clear():
    model = SubtitleModel()
    model.apply_update({"local_segments": [row("a", "旧句")]})
    model.apply_update({"local_removed_segment_ids": ["a"], "local_segments": [row("a", "迟到", revision=8)]})
    assert sources(model) == []
    model.apply_update({"local_reset": True, "local_segments": [row("a", "新句")]})
    model.clear(preserve_existing=True)
    assert sources(model) == ["新句"]
    model.clear()
    assert sources(model) == []


def test_mixed_local_online_local_arrival_order():
    model = SubtitleModel()
    model.apply_update({"local_segments": [row("a", "本地先")]})
    model.apply_update({"final_tokens": [token("在线中间")]})
    model.apply_update({"local_segments": [row("b", "本地后", order=1)]})
    assert sources(model) == ["本地先", "在线中间", "本地后"]
    model.apply_update({"local_segments": [row("a", "本地先修订", revision=2)]})
    assert sources(model) == ["本地先修订", "在线中间", "本地后"]


def test_local_translation_does_not_clear_or_split_ordinary_live_sentence():
    model = SubtitleModel()
    model.apply_update({"local_segments": [row("a", "本地历史")]})
    model.apply_update({"final_tokens": [token("Hello ")], "non_final_tokens": [token("world")]})
    model.apply_update({"local_segments": [row("a", "本地历史", revision=2, translation="History")]})
    assert sources(model) == ["本地历史", "Hello world"]
    model.apply_update({"final_tokens": [token("world")], "non_final_tokens": []})
    assert sources(model) == ["本地历史", "Hello world"]


def test_new_split_is_inserted_between_existing_local_rows():
    model = SubtitleModel()
    model.apply_update({"local_segments": [row("a", "甲", order=0), row("c", "丙", order=2)]})
    model.apply_update({"local_segments": [row("b", "乙", order=1)]})
    assert sources(model) == ["甲", "乙", "丙"]


def test_split_updates_neighbor_order_in_the_same_frame_before_placing_new_row():
    model = SubtitleModel()
    model.apply_update({"local_segments": [row("a", "甲乙", order=0), row("c", "丙", order=1)]})
    model.apply_update({"final_tokens": [token("稍后在线")]})
    model.apply_update({"local_segments": [row("a", "甲", revision=2), row("b", "乙", order=1),
                                           row("c", "丙", order=2, revision=2)]})
    assert sources(model) == ["甲", "乙", "丙", "稍后在线"]


def test_history_trimming_keeps_live_rows_and_rejects_trimmed_rows():
    model = SubtitleModel()
    model.apply_update({"final_tokens": [token("旧在线")]})
    model.apply_update({"local_segments": [row("a", "旧本地", final=True), row("b", "当前", order=1)]})
    model.trim_final_tokens_to_recent_sentences(1)
    assert sources(model) == ["当前"]
    model.apply_update({"local_segments": [row("a", "迟到历史", final=True, revision=10)]})
    assert sources(model) == ["当前"]


def test_ordinary_only_stream_keeps_existing_sentence_behavior():
    model = SubtitleModel()
    model.apply_update({"final_tokens": [token("Hello ")], "non_final_tokens": [token("world")]})
    assert sources(model) == ["Hello world"]
    model.apply_update({"final_tokens": [token("world")], "non_final_tokens": []})
    assert sources(model) == ["Hello world"]


def test_native_window_renders_local_frames_without_generic_tokens(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = overlay_window.QSettings(str(tmp_path / "overlay.ini"), overlay_window.QSettings.IniFormat)
    monkeypatch.setattr(overlay_window, "QSettings", lambda *args: settings)
    monkeypatch.setattr(OverlayWindow, "_init_ws", lambda self: None)
    monkeypatch.setattr(OverlayWindow, "_apply_windows_no_activate_style", lambda self: None)
    win = OverlayWindow("http://127.0.0.1:1")
    try:
        win.resize(900, 320)
        win.show()
        app.processEvents()
        win._on_message(json.dumps({"type": "update", "local_segments": [
            row("a", "第一句。"), row("b", "第二句。", order=1),
        ]}))
        assert "第一句。" in win.text.toPlainText()
        assert "第二句。" in win.text.toPlainText()
        assert "等待字幕" not in win.text.toPlainText()
        win._on_message(json.dumps({"type": "update", "local_segments": [
            row("a", "第一句，", translation="First sentence.", revision=2),
        ]}))
        assert win.text.toPlainText().count("First sentence.") == 1
        assert sources(win.model) == ["第一句，", "第二句。"]
    finally:
        win._hover_timer.stop()
        win.hide()
        win.deleteLater()
        app.processEvents()
