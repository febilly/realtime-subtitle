import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from overlay_window import (
    BACKGROUND_MODE_TEXT,
    BACKGROUND_MODE_WINDOW,
    HIT_TEST_ALPHA,
    TEXT_BACKDROP_RADIUS,
    OverlayWindow,
    SubtitleTextEdit,
    _normalize_background_mode,
    _paint_overlay_background,
)

APP = QApplication.instance() or QApplication([])


def test_background_mode_normalization_defaults_to_whole_window():
    assert _normalize_background_mode(BACKGROUND_MODE_TEXT) == BACKGROUND_MODE_TEXT
    assert _normalize_background_mode("invalid") == BACKGROUND_MODE_WINDOW
    assert _normalize_background_mode(None) == BACKGROUND_MODE_WINDOW


def test_text_backdrop_includes_badge_and_adds_equal_side_insets():
    edit = SubtitleTextEdit()
    edit.resize(400, 120)
    edit.setHtml(
        '<div style="margin:0; line-height:110%; font-size:34px;">'
        '<img src="langtag:18-JA" style="vertical-align:middle;">Hello</div>')
    edit.setTextBackdrop(True, 128, 6)
    edit.show()
    APP.processEvents()

    block = edit.document().begin()
    line = block.layout().lineAt(0)
    natural = line.naturalTextRect()
    backdrop = edit._backdrop_rects()[0]

    # naturalTextRect includes the inline language-badge image. The custom
    # backdrop adds the same explicit inset on its left and right.
    assert backdrop.width() == natural.width() + 12
    assert backdrop.left() == 0

    content_top, content_bottom = edit._line_content_vertical_bounds(block, line)
    block_top = edit.document().documentLayout().blockBoundingRect(block).top()
    viewport_offset = -edit.verticalScrollBar().value()
    content_top += block_top + viewport_offset
    content_bottom += block_top + viewport_offset
    assert abs((content_top - backdrop.top())
               - (backdrop.bottom() - content_bottom)) < 0.01


def test_text_backdrop_can_be_disabled_without_changing_subtitle_html():
    edit = SubtitleTextEdit()
    edit.setHtml('<div style="font-size:20px;">Hello</div>')
    original_html = edit.toHtml()
    edit.setTextBackdrop(True, 128, 4)
    edit.setTextBackdrop(False, 0, 0)

    assert edit.toHtml() == original_html
    assert edit._backdrop_enabled is False


def test_text_backdrop_radius_is_retained_for_easy_restoration():
    assert TEXT_BACKDROP_RADIUS == 14


def test_overlay_buttons_use_neutral_gray_backgrounds():
    assert "background: rgba(128,128,128,0.30)" in OverlayWindow._BTN_QSS
    assert "background: rgba(128,128,128,0.34)" in OverlayWindow._BTN_QSS
    assert "background: rgba(128,128,128,0.50)" in OverlayWindow._BTN_QSS_ACTIVE
    assert "color: #bfdbfe" in OverlayWindow._BTN_QSS_ACTIVE


def test_text_only_mode_uses_darker_less_transparent_button_backgrounds():
    assert "background: rgba(96,96,96,0.40)" in OverlayWindow._BTN_QSS_TEXT
    assert "background: rgba(96,96,96,0.52)" in OverlayWindow._BTN_QSS_TEXT
    assert "background: rgba(96,96,96,0.58)" in OverlayWindow._BTN_QSS_TEXT_ACTIVE
    assert "color: #bfdbfe" in OverlayWindow._BTN_QSS_TEXT_ACTIVE


def test_transparent_window_keeps_a_nearly_invisible_mouse_hit_layer_everywhere():
    assert HIT_TEST_ALPHA == 1
    image = QImage(100, 50, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    _paint_overlay_background(
        painter, QRect(0, 0, 100, 50), 0, BACKGROUND_MODE_WINDOW)
    painter.end()

    assert image.pixelColor(0, 0).alpha() == HIT_TEST_ALPHA
    assert image.pixelColor(50, 25).alpha() == HIT_TEST_ALPHA


def test_text_background_mode_hides_window_outline_until_hovered():
    image = QImage(100, 50, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    _paint_overlay_background(
        painter, QRect(0, 0, 100, 50), 180, BACKGROUND_MODE_TEXT)
    painter.end()

    assert image.pixelColor(50, 25).alpha() == HIT_TEST_ALPHA
    border_alphas = [image.pixelColor(x, 1).alpha() for x in range(image.width())]
    assert max(border_alphas) == HIT_TEST_ALPHA

    hovered = QImage(100, 50, QImage.Format_ARGB32)
    hovered.fill(0)
    painter = QPainter(hovered)
    _paint_overlay_background(
        painter, QRect(0, 0, 100, 50), 180, BACKGROUND_MODE_TEXT,
        show_outline=True)
    painter.end()

    border_alphas = [hovered.pixelColor(x, 1).alpha() for x in range(hovered.width())]
    assert max(border_alphas) > HIT_TEST_ALPHA
