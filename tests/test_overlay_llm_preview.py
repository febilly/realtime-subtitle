import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from overlay_window import OverlayWindow


APP = QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(monkeypatch):
    # Exercise the real WebSocket frame handler and renderer without a network
    # connection or a visible desktop window.
    monkeypatch.setattr(OverlayWindow, "_init_ws", lambda self: None)
    window = OverlayWindow("http://127.0.0.1:1")
    window.display_mode = "both"
    window.furigana_enabled = False
    yield window
    window._hover_timer.stop()
    window.deleteLater()


def send(window, **frame):
    window._on_message(json.dumps(frame, ensure_ascii=False))


def test_speculative_translation_appears_before_asr_finalization(overlay):
    send(overlay, type="update", final_tokens=[], non_final_tokens=[
        {"text": "Hello.", "language": "en", "speaker": "1", "is_final": False},
    ])
    assert "你好。" not in overlay.text.toPlainText()

    send(overlay, type="spec_translation_pending", source="Hello.", target_lang="zh")
    groups, _ = overlay._build_line_groups(overlay.model.build_blocks())
    assert groups[0][-1]["lang"] == "zh"

    send(overlay, type="spec_translation", source="Hello.",
         translation="你好。", target_lang="zh")
    assert "你好。" in overlay.text.toPlainText()
    groups, _ = overlay._build_line_groups(overlay.model.build_blocks())
    preview = groups[0][-1]
    assert preview["tokens"] == [{"text": "你好。", "is_final": False}]

    send(overlay, type="update", final_tokens=[
        {"text": "Hello.", "language": "en", "speaker": "1",
         "is_final": True, "llm_sentence_id": "s1"},
    ], non_final_tokens=[])
    send(overlay, type="refine_result", sentence_id="s1", source="Hello.",
         refined_translation="您好。", target_lang="zh", no_change=False)
    assert "您好。" in overlay.text.toPlainText()
    assert "你好。" not in overlay.text.toPlainText()


def test_speculative_translation_matches_completed_segment_in_longer_nonfinal_source(overlay):
    send(overlay, type="update", final_tokens=[], non_final_tokens=[
        {"text": "First sentence. More words", "language": "en",
         "speaker": "1", "is_final": False},
    ])
    send(overlay, type="spec_translation", source="First sentence.",
         translation="第一句。", target_lang="zh")
    assert "第一句。" in overlay.text.toPlainText()

    send(overlay, type="clear", preserve_existing=False)
    assert not overlay._spec_by_source
    assert "第一句。" not in overlay.text.toPlainText()
