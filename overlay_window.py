"""原生（PySide6）半透明字幕悬浮窗。

作为独立进程运行（与 pywebview 主窗口分离，避免两个 GUI 事件循环冲突）：

    python overlay_window.py --url http://127.0.0.1:PORT

冻结（PyInstaller）后由主程序通过 `--run-overlay` 重新拉起自身进入这里。

窗口特性：
  * 无边框、半透明黑底或文字背板、白字、圆角
  * 任意位置鼠标拖动；贴近边缘鼠标缩放
  * 鼠标移入才在右下角显示一排常用按钮（字号 +/-、暂停/继续、关闭）
  * 通过 WebSocket(`/ws`) 接收字幕，样式与网页版基本一致
"""

import os
import sys
import json
import math
import time
import queue
import base64
import argparse
import asyncio
import threading
import urllib.request
from html import escape as _html_escape

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPoint, QPointF, QRect, QRectF, QSettings, QSize, QEvent, QLocale
from PySide6.QtGui import (
    QCursor,
    QPainter,
    QColor,
    QBrush,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QFontMetricsF,
    QIcon,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QProxyStyle,
    QWidget,
    QTextEdit,
    QPushButton,
    QHBoxLayout,
    QStyle,
    QLabel,
    QGraphicsOpacityEffect,
)
from PySide6.QtSvg import QSvgRenderer

try:
    import websockets
except Exception:  # pragma: no cover - 仅在缺依赖时触发
    websockets = None


# ---------------------------------------------------------------------------
# 颜色 / 样式常量（与网页版 dark 主题大致对应）
# ---------------------------------------------------------------------------
FINAL_COLOR = "#e5e7eb"        # 已确定文本（接近白）
NONFINAL_COLOR = "#60a5fa"     # 进行中（蓝）
PLACEHOLDER_COLOR = "#9ca3af"  # 空状态 / 占位
TAG_BG = "rgba(255,255,255,0.16)"
TAG_FG = "#d1d5db"
SPEAKER_COLOR = "#9ca3af"
RUBY_RT_COLOR = "#9ca3af"      # 假名注音（与网页版 dark 主题 ruby rt 一致）
RUBY_RT_SCALE = 0.7            # 注音/正文字号比（对齐 350px 宽主窗口的 0.7em）
FONT_SC_FAMILY = "Noto Sans CJK SC"
FONT_JP_FAMILY = "Noto Sans CJK JP"
FONT_KR_FAMILY = "Noto Sans CJK KR"
SYSTEM_FONT_STACK = "'Segoe UI', 'Microsoft YaHei UI', 'Microsoft YaHei', 'Yu Gothic', 'Meiryo', 'Malgun Gothic', sans-serif"
BUNDLED_CJK_FONT_STACK = f"'{FONT_SC_FAMILY}', '{FONT_JP_FAMILY}', '{FONT_KR_FAMILY}', 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', sans-serif"
BUNDLED_SC_FONT_STACK = f"'{FONT_SC_FAMILY}', '{FONT_JP_FAMILY}', '{FONT_KR_FAMILY}', 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', sans-serif"
BUNDLED_JP_FONT_STACK = f"'{FONT_JP_FAMILY}', '{FONT_SC_FAMILY}', '{FONT_KR_FAMILY}', 'Yu Gothic', 'Meiryo', 'Segoe UI', sans-serif"
BUNDLED_KR_FONT_STACK = f"'{FONT_KR_FAMILY}', '{FONT_SC_FAMILY}', '{FONT_JP_FAMILY}', 'Malgun Gothic', 'Segoe UI', sans-serif"

RESIZE_MARGIN = 8              # 边缘缩放热区（像素）
MIN_W, MIN_H = 220, 120
BG_RADIUS = 14
HIT_TEST_ALPHA = 1             # Windows layered window: alpha=0 pixels cannot receive mouse input
BACKGROUND_MODE_WINDOW = "window"
BACKGROUND_MODE_TEXT = "text"
# At the standard 24px subtitle size: language-tag radius 7.6px + 6px inset
# = 13.6px. A fixed 14px radius keeps the two left arcs visually concentric.
TEXT_BACKDROP_RADIUS = 14


def _build_alpha_levels(n: int = 11) -> list[int]:
    """背景不透明度挡位：按 CIE L*（人眼感知亮度）等距取点。

    人眼感知的是「透过黑幕看到的背景亮度」，它正比于透光率 (1-alpha)，而非 alpha 本身；
    且对暗处更敏感。所以让透光率的感知亮度 L* 等距，再反推 alpha：
    透明端步子粗、不透明端步子细——这样从透明拨到不透明，每一挡的「变暗感」大致相同。
    两端固定为完全透明(0) / 完全不透明(255)。
    """
    levels = []
    for i in range(n):
        p = i / (n - 1)                           # 0=透明, 1=不透明
        l_star = 100.0 * (1.0 - p)                # 透过的背景亮度，感知等距
        if l_star <= 8.0:                         # CIE 线性段
            y = l_star / 903.3
        else:
            y = ((l_star + 16.0) / 116.0) ** 3    # CIE 立方段
        levels.append(round((1.0 - y) * 255))     # alpha = 1 - 透光率
    levels[0], levels[-1] = 0, 255                # 钉死两端
    return levels


# ≈ [0, 60, 110, 151, 183, 208, 226, 239, 247, 252, 255]
ALPHA_LEVELS = _build_alpha_levels()
# 默认沿用此前 ~150 的观感，取最接近的挡位（151）。
DEFAULT_ALPHA = min(ALPHA_LEVELS, key=lambda a: abs(a - 150))


def _resource_path(*parts: str) -> str:
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, *parts)


def get_custom_font_path() -> str | None:
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        font_path = os.path.join(exe_dir, "NotoSansCJK-Regular.ttc")
    else:
        font_path = os.path.join(os.getcwd(), "NotoSansCJK-Regular.ttc")
    if os.path.exists(font_path):
        return font_path
    
    # Fallback to dev static folder font path
    static_font_path = _resource_path("static", "fonts", "NotoSansCJK-Regular.ttc")
    if os.path.exists(static_font_path):
        return static_font_path
    return None


def custom_font_exists() -> bool:
    return get_custom_font_path() is not None


def load_bundled_fonts() -> None:
    font_path = get_custom_font_path()
    if not font_path:
        return
    font_id = QFontDatabase.addApplicationFont(font_path)
    if font_id < 0:
        print(f"⚠️  Failed to load CJK font: {font_path}")


class InstantToolTipStyle(QProxyStyle):
    """Remove the default hover delay for overlay controls."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return 0
        return super().styleHint(hint, option, widget, returnData)


_I18N_DATA = {
    "zh": {
        "font_dec": "减小字号",
        "font_inc": "增大字号",
        "alpha_dec": "背景更透明",
        "alpha_inc": "背景更不透明",
        "background_window": "背景模式：整个窗口 (点击改为仅文字背板)",
        "background_text": "背景模式：仅文字背板 (点击改为整个窗口)",
        "display_both": "当前：显示原文与译文 (点击切换为仅原文)",
        "display_original": "当前：仅显示原文 (点击切换为仅译文)",
        "display_translation": "当前：仅显示译文 (点击切换为显示全部)",
        "flow_up": "字幕向上流动 (点击改为向下流动)",
        "flow_down": "字幕向下流动 (点击改为向上流动)",
        "passthrough_off": "穿透模式：关闭 (开启后除按钮外鼠标均可穿透)",
        "passthrough_on": "穿透模式：开启 (关闭后鼠标无法穿透悬浮窗)",
        "furigana_off": "开启假名注音",
        "furigana_on": "关闭假名注音",
        "restart": "重启识别",
        "restarting": "正在重启识别",
        "restart_failed": "重启失败，点击重试",
        "pause": "暂停识别",
        "resume": "继续识别",
        "close": "关闭悬浮窗"
    },
    "en": {
        "font_dec": "Decrease font size",
        "font_inc": "Increase font size",
        "alpha_dec": "More transparent background",
        "alpha_inc": "More opaque background",
        "background_window": "Background: whole window (click for text only)",
        "background_text": "Background: text only (click for whole window)",
        "display_both": "Current: original + translation (click for original only)",
        "display_original": "Current: original only (click for translation only)",
        "display_translation": "Current: translation only (click for both)",
        "flow_up": "Subtitles flow upward (click to flow downward)",
        "flow_down": "Subtitles flow downward (click to flow upward)",
        "passthrough_off": "Click-through mode: disabled (click to enable)",
        "passthrough_on": "Click-through mode: enabled (click to disable)",
        "furigana_off": "Enable furigana for Japanese",
        "furigana_on": "Disable furigana",
        "restart": "Restart recognition",
        "restarting": "Restarting recognition",
        "restart_failed": "Restart failed, click to retry",
        "pause": "Pause recognition",
        "resume": "Resume recognition",
        "close": "Close subtitle overlay window"
    },
    "ja": {
        "font_dec": "フォントサイズを小さくする",
        "font_inc": "フォントサイズを大きくする",
        "alpha_dec": "背景の透明度を上げる",
        "alpha_inc": "背景の不透明度を上げる",
        "background_window": "背景：ウィンドウ全体 (クリックで文字のみ)",
        "background_text": "背景：文字の背面のみ (クリックでウィンドウ全体)",
        "display_both": "現在：原文＋訳文 (クリックで原文のみ表示)",
        "display_original": "現在：原文のみ (クリックで訳文のみ表示)",
        "display_translation": "現在：訳文のみ (クリックで両方表示)",
        "flow_up": "字幕は上方向に流れます (クリックで下方向に変更)",
        "flow_down": "字幕は下方向に流れます (クリックで上方向に変更)",
        "passthrough_off": "マウスクリック透過：無効 (クリックで有効化)",
        "passthrough_on": "マウスクリック透過：有効 (クリックで無効化)",
        "furigana_off": "ふりがな表示を有効にする",
        "furigana_on": "ふりがな表示を無効にする",
        "restart": "認識を再起動",
        "restarting": "認識を再起動中",
        "restart_failed": "再起動に失敗、クリックして再試行",
        "pause": "認識を一時停止",
        "resume": "認識を再開",
        "close": "字幕オーバーレイウィンドウを閉じる"
    },
    "ko": {
        "font_dec": "글꼴 크기 줄이기",
        "font_inc": "글꼴 크기 늘리기",
        "alpha_dec": "배경을 더 투명하게",
        "alpha_inc": "배경을 더 불투명하게",
        "background_window": "배경: 전체 창 (클릭하여 글자 뒤만 표시)",
        "background_text": "배경: 글자 뒤만 (클릭하여 전체 창 표시)",
        "display_both": "현재: 원문+번역문 (클릭 시 원문만 표시)",
        "display_original": "현재: 원문만 표시 (클릭 시 번역문만 표시)",
        "display_translation": "현재: 번역문만 표시 (클릭 시 둘 다 표시)",
        "flow_up": "자막이 위로 흐릅니다 (클릭하여 아래로 변경)",
        "flow_down": "자막이 아래로 흐릅니다 (클릭하여 위로 변경)",
        "passthrough_off": "마우스 클릭 관통: 비활성화 (클릭 시 활성화)",
        "passthrough_on": "마우스 클릭 관통: 활성화 (클릭 시 비활성화)",
        "furigana_off": "후리가나 활성화",
        "furigana_on": "후리가나 비활성화",
        "restart": "인식 재시작",
        "restarting": "인식 재시작 중",
        "restart_failed": "재시작 실패, 클릭하여 재시도",
        "pause": "인식 일시정지",
        "resume": "인식 재개",
        "close": "자막 오버레이 창 닫기"
    },
    "ru": {
        "font_dec": "Уменьшить размер шрифта",
        "font_inc": "Увеличить размер шрифта",
        "alpha_dec": "Сделать фон более прозрачным",
        "alpha_inc": "Сделать фон менее прозрачным",
        "background_window": "Фон: всё окно (нажмите для фона только под текстом)",
        "background_text": "Фон: только под текстом (нажмите для всего окна)",
        "display_both": "Сейчас: оригинал + перевод (нажмите для оригинала)",
        "display_original": "Сейчас: только оригинал (нажмите для перевода)",
        "display_translation": "Сейчас: только перевод (нажмите для всего)",
        "flow_up": "Субтитры движутся вверх (нажмите, чтобы направить вниз)",
        "flow_down": "Субтитры движутся вниз (нажмите, чтобы направить вверх)",
        "passthrough_off": "Режим сквозного клика: отключен (нажмите для включения)",
        "passthrough_on": "Режим сквозного клика: включен (нажмите для отключения)",
        "furigana_off": "Включить фуригану для японского",
        "furigana_on": "Выключить фуригану",
        "restart": "Перезапустить распознавание",
        "restarting": "Перезапуск распознавания...",
        "restart_failed": "Сбой перезапуска, нажмите для повтора",
        "pause": "Приостановить распознавание",
        "resume": "Продолжить распознавание",
        "close": "Закрыть окно оверлея субтитров"
    },
    "es": {
        "font_dec": "Disminuir tamaño de fuente",
        "font_inc": "Aumentar tamaño de fuente",
        "alpha_dec": "Fondo más transparente",
        "alpha_inc": "Fondo más opaco",
        "background_window": "Fondo: ventana completa (clic para solo texto)",
        "background_text": "Fondo: solo detrás del texto (clic para ventana completa)",
        "display_both": "Actual: original + traducción (clic para solo original)",
        "display_original": "Actual: solo original (clic para solo traducción)",
        "display_translation": "Actual: solo traducción (clic para ambos)",
        "flow_up": "Los subtítulos fluyen hacia arriba (clic para invertir)",
        "flow_down": "Los subtítulos fluyen hacia abajo (clic para invertir)",
        "passthrough_off": "Modo de paso del ratón: desactivado (clic para activar)",
        "passthrough_on": "Modo de paso del ratón: activado (clic para desactivar)",
        "furigana_off": "Activar furigana para japonés",
        "furigana_on": "Desactivar furigana",
        "restart": "Reiniciar reconocimiento",
        "restarting": "Reiniciando reconocimiento",
        "restart_failed": "Error al reiniciar, clic para reintentar",
        "pause": "Pausar reconocimiento",
        "resume": "Reanudar reconocimiento",
        "close": "Cerrar ventana superpuesta de subtítulos"
    },
    "pt": {
        "font_dec": "Diminuir tamanho da fonte",
        "font_inc": "Aumentar tamanho da fonte",
        "alpha_dec": "Fundo mais transparente",
        "alpha_inc": "Fundo mais opaco",
        "background_window": "Fundo: janela inteira (clique para apenas o texto)",
        "background_text": "Fundo: apenas atrás do texto (clique para a janela inteira)",
        "display_both": "Atual: original + tradução (clique para apenas original)",
        "display_original": "Atual: apenas original (clique para apenas tradução)",
        "display_translation": "Atual: apenas tradução (clique para ambos)",
        "flow_up": "As legendas fluem para cima (clique para inverter)",
        "flow_down": "As legendas fluem para baixo (clique para inverter)",
        "passthrough_off": "Modo de passagem do mouse: desativado (clique para ativar)",
        "passthrough_on": "Modo de passagem do mouse: ativado (clique para desativar)",
        "furigana_off": "Ativar furigana para japonês",
        "furigana_on": "Desativar furigana",
        "restart": "Reiniciar reconhecimento",
        "restarting": "Reiniciando reconhecimento",
        "restart_failed": "Falha ao reiniciar, clique para tentar novamente",
        "pause": "Pausar reconhecimento",
        "resume": "Retomar reconhecimento",
        "close": "Fechar janela de sobreposição de legendas"
    }
}


def tr(key: str) -> str:
    lang = QLocale.system().name().split('_')[0]
    if lang not in _I18N_DATA:
        lang = "en"
    return _I18N_DATA[lang].get(key, _I18N_DATA["en"].get(key, key))


def _normalize_background_mode(value) -> str:
    return (BACKGROUND_MODE_TEXT
            if str(value) == BACKGROUND_MODE_TEXT
            else BACKGROUND_MODE_WINDOW)


def _paint_overlay_background(painter: QPainter, rect: QRect, bg_alpha: int,
                              background_mode: str,
                              show_outline: bool = False) -> None:
    mode = _normalize_background_mode(background_mode)
    alpha = max(0, min(255, int(bg_alpha)))
    painter.setRenderHint(QPainter.Antialiasing)

    # Layered windows do not receive mouse input on alpha=0 pixels under Windows.
    # A 1/255-alpha full-rect fill is visually transparent but keeps every point
    # draggable, including rounded corners and text-only background mode.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(0, 0, 0, HIT_TEST_ALPHA)))
    painter.drawRect(rect)

    if mode == BACKGROUND_MODE_WINDOW and alpha > 0:
        painter.setBrush(QBrush(QColor(0, 0, 0, alpha)))
        painter.drawRoundedRect(rect, BG_RADIUS, BG_RADIUS)
    elif mode == BACKGROUND_MODE_TEXT and show_outline:
        outline = QRectF(rect).adjusted(0.75, 0.75, -0.75, -0.75)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1.5))
        painter.drawRoundedRect(outline, BG_RADIUS, BG_RADIUS)


class OverlayToolTip(QLabel):
    """Custom premium floating ToolTip for overlay window buttons with white background and black text."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 0.98);
                color: #111827;
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 6px;
                padding: 6px 12px;
                font-family: "Segoe UI", "Microsoft YaHei", "Yu Gothic", "Meiryo", "Malgun Gothic", "DengXian", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 14px;
                font-weight: 500;
            }
        """)
        self.setVisible(False)


class ToolTipEventFilter(QObject):
    """Event filter to intercept button hovers and show custom tooltip positioned above the button, horizontally centered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.tooltip = None

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Enter:
            tip_text = obj.toolTip()
            if tip_text and self.parent_window:
                if not self.tooltip:
                    self.tooltip = OverlayToolTip(self.parent_window)
                self.tooltip.setText(tip_text)
                self.tooltip.adjustSize()

                rect = obj.rect()
                tooltip_w = self.tooltip.width()
                tooltip_h = self.tooltip.height()

                # Top center of the button in parent window coordinates
                parent_top_center = obj.mapTo(self.parent_window, QPoint(rect.width() // 2, 0))

                # Position the tooltip above the button, centered horizontally (6px gap)
                x = parent_top_center.x() - tooltip_w // 2
                y = parent_top_center.y() - tooltip_h - 6

                # Handle boundary clamping within parent window
                if x < 8:
                    x = 8
                elif x + tooltip_w > self.parent_window.width() - 8:
                    x = self.parent_window.width() - tooltip_w - 8

                if y < 8:
                    # If it goes off the top of the window, place it below the button
                    parent_bottom_center = obj.mapTo(self.parent_window, QPoint(rect.width() // 2, rect.height()))
                    y = parent_bottom_center.y() + 6

                self.tooltip.move(x, y)
                self.tooltip.raise_()
                self.tooltip.show()
        elif event.type() in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.Hide):
            if self.tooltip:
                self.tooltip.hide()
        elif event.type() == QEvent.ToolTip:
            # Block native tooltips from showing
            return True

        return super().eventFilter(obj, event)


# Cache of SVG inner contents for each symbol ID
_SVG_SYMBOLS = {}
_RENDERER_CACHE = {}

def _load_svg_symbols():
    try:
        import xml.etree.ElementTree as ET
        # Resolve path to static/icons/lucide-sprite.svg
        base_dir = os.path.dirname(os.path.abspath(__file__))
        svg_path = os.path.join(base_dir, "static", "icons", "lucide-sprite.svg")
        if not os.path.exists(svg_path):
            if hasattr(sys, '_MEIPASS'):
                svg_path = os.path.join(sys._MEIPASS, "static", "icons", "lucide-sprite.svg")
        
        if os.path.exists(svg_path):
            tree = ET.parse(svg_path)
            root = tree.getroot()
            for elem in root.findall(".//{http://www.w3.org/2000/svg}symbol"):
                sym_id = elem.get("id")
                if sym_id:
                    inner_xml = "".join(ET.tostring(child, encoding='utf-8').decode('utf-8') for child in elem)
                    _SVG_SYMBOLS[sym_id] = inner_xml
            if not _SVG_SYMBOLS:
                for elem in root.findall(".//symbol"):
                    sym_id = elem.get("id")
                    if sym_id:
                        inner_xml = "".join(ET.tostring(child, encoding='utf-8').decode('utf-8') for child in elem)
                        _SVG_SYMBOLS[sym_id] = inner_xml
    except Exception as e:
        print(f"Error loading SVG symbols: {e}")

_load_svg_symbols()

def get_svg_renderer(name: str, color: str) -> QSvgRenderer:
    key = (name, color)
    if key in _RENDERER_CACHE:
        return _RENDERER_CACHE[key]
    
    inner_xml = _SVG_SYMBOLS.get(name, "")
    svg_xml = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    {inner_xml}
    </svg>"""
    renderer = QSvgRenderer(svg_xml.encode('utf-8'))
    _RENDERER_CACHE[key] = renderer
    return renderer


class VectorButton(QPushButton):
    """A button that draws vector graphics directly in paintEvent from local SVG sprite definitions."""

    def __init__(self, text: str, tip: str, slot, icon_name: str = None, parent=None):
        super().__init__(text, parent)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(28, 24)
        self.setFocusPolicy(Qt.NoFocus)
        self.icon_name = icon_name
        self.clicked.connect(slot)

    def setIconName(self, name: str):
        if self.icon_name != name:
            self.icon_name = name
            self.update()

    def paintEvent(self, event):
        # 1. Let the stylesheet draw the button background, borders, hover/pressed states
        super().paintEvent(event)

        # 2. If there is no icon, we are done
        if not self.icon_name:
            return

        # 3. Draw the vector icon on top of the button background
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        color_str = "#bfdbfe" if "bfdbfe" in self.styleSheet() else "#f3f4f6"
        renderer = get_svg_renderer(self.icon_name, color_str)
        if renderer.isValid():
            renderer.render(p, QRectF(6, 4, 16, 16))
        
        p.end()


# ===========================================================================
# WebSocket 客户端（后台线程 + 独立 asyncio 事件循环）
# ===========================================================================
class WsBridge(QObject):
    """跨线程把原始消息字符串投递回 GUI 线程。"""
    message = Signal(str)
    status = Signal(bool)  # True=已连接


def is_parent_alive():
    import os
    try:
        ppid = os.getppid()
        if ppid <= 1:
            return False
        if os.name == "nt":
            import ctypes
            # Open process handle
            PROCESS_QUERY_INFORMATION = 0x0400
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE, False, ppid)
            if not handle:
                return False
            # Check if it has exited
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            STILL_ACTIVE = 259
            is_active = (exit_code.value == STILL_ACTIVE)
            ctypes.windll.kernel32.CloseHandle(handle)
            return is_active
        else:
            try:
                os.kill(ppid, 0)
                return True
            except OSError:
                return False
    except Exception:
        return True


class WsClient(threading.Thread):
    def __init__(self, ws_url: str, bridge: WsBridge):
        super().__init__(daemon=True)
        self.ws_url = ws_url
        self.bridge = bridge
        self._stop = False
        self._loop = None

    def run(self):
        if websockets is None:
            return
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            pass

    async def _main(self):
        while not self._stop:
            try:
                async with websockets.connect(self.ws_url, max_size=None,
                                              ping_interval=20) as ws:
                    self.bridge.status.emit(True)
                    async for msg in ws:
                        if self._stop:
                            break
                        if isinstance(msg, bytes):
                            try:
                                msg = msg.decode("utf-8")
                            except Exception:
                                continue
                        self.bridge.message.emit(msg)
            except Exception:
                pass
            self.bridge.status.emit(False)
            if self._stop:
                break
            if not is_parent_alive():
                QApplication.quit()
                break
            await asyncio.sleep(1.5)

    def stop(self):
        self._stop = True
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass


# ===========================================================================
# 字幕状态 + 渲染（移植自网页版 app.js 的核心分句逻辑，做了简化）
# ===========================================================================
SENTENCE_PUNCT = "。．.!！?？…"


def _normalize_lang_code(lang) -> str:
    return str(lang or "").strip().lower().replace("_", "-")


def _subtitle_font_lang(lang) -> str:
    code = _normalize_lang_code(lang)
    primary = code.split("-", 1)[0]
    if primary in {"zh", "cmn", "yue", "lzh"}:
        return "zh-Hans"
    if primary == "ja":
        return "ja"
    if primary == "ko":
        return "ko"
    return ""


def _lang_attr(lang) -> str:
    font_lang = _subtitle_font_lang(lang)
    return f' lang="{font_lang}"' if font_lang else ""


def _font_stack_for_lang(lang, use_bundled_cjk_fonts: bool = False) -> str:
    if not use_bundled_cjk_fonts or not custom_font_exists():
        return SYSTEM_FONT_STACK
    font_lang = _subtitle_font_lang(lang)
    if font_lang == "ja":
        return BUNDLED_JP_FONT_STACK
    if font_lang == "ko":
        return BUNDLED_KR_FONT_STACK
    if font_lang:
        return BUNDLED_SC_FONT_STACK
    return BUNDLED_CJK_FONT_STACK


def _font_families_for_lang(lang, use_bundled_cjk_fonts: bool = False) -> list[str]:
    """把 CSS 字体栈解析成 QFont.setFamilies 可用的家族列表。"""
    stack = _font_stack_for_lang(lang, use_bundled_cjk_fonts)
    families = []
    for part in stack.split(","):
        name = part.strip().strip("'\"")
        if name and name != "sans-serif":
            families.append(name)
    return families


# ===========================================================================
# 日语假名注音（ふりがな）：行为对齐网页版 static/js/furigana.js。
# 网页版用 kuromoji.js（mecab-ipadic 词典）分词后以 <ruby> 标注；QTextEdit 的
# 富文本不支持 <ruby>，这里改为把整行 token 逐个画成「注音在上、正文在下」的
# 内联图片（与语言标识 langtag 同一机制）。
#
# 分词不在本进程做：悬浮窗把整行文本 POST 到 server 的 /furigana，由主窗口里
# 已加载的 kuromoji 分词后返回 [[表面形, 注音|null], ...]。这样 exe 里只保留网页版
# 那一份 kuromoji 词典，不再打包 Python 词典，且分词/注音结果与网页版完全一致。
# ===========================================================================
class FuriganaService(QObject):
    """假名注音服务：整行文本经 server 交给主窗口 kuromoji 分词，结果按行缓存。

    语义对齐网页版 Furigana.createService：结果未就绪时 get_pairs 返回 None（调用方
    先按普通文本渲染），拿到结果后发 ready 信号触发整体重渲染。分词是异步网络请求，
    用单个后台工作线程串行处理，避免为每行文本各起一个线程。
    """

    ready = Signal()

    # 主窗口词典异步加载中（ready:false）时的重试节奏：最多等约 6 秒。
    _NOT_READY_RETRIES = 20
    _NOT_READY_INTERVAL = 0.3
    # 取词失败（无主窗口/请求失败）后的冷却：这段时间内该行直接按纯文本渲染，
    # 不再阻塞帧、也不狂发请求；冷却过后再试一次。
    _FAILED_COOLDOWN = 3.0

    def __init__(self, fetch_pairs, parent=None):
        super().__init__(parent)
        # fetch_pairs(text) -> dict|None：执行一次 /furigana 请求，返回解析后的 JSON。
        self._fetch_pairs = fetch_pairs
        self._enabled = False
        self._cache: dict[str, list] = {}
        self._failed: dict[str, float] = {}
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._queued: set[str] = set()
        self._worker = None

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        if self._enabled:
            self._ensure_worker()
        else:
            self.clear()

    def is_enabled(self) -> bool:
        return self._enabled

    def _ensure_worker(self):
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def get_pairs(self, text):
        """返回整行的 [(表面形, 注音|None), ...]；未就绪时返回 None 并排队获取。"""
        if not text or not self._enabled:
            return None
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        if self._in_failed_cooldown(text):
            return None                       # 近期取词失败：不再排队，交由调用方渲染纯文本
        if text not in self._queued:
            self._queued.add(text)
            self._queue.put(text)
        return None

    def is_pending(self, text) -> bool:
        """该行注音是否正在获取（值得阻塞本帧以避免"先纯文本后注音"的闪烁）。

        已缓存或处于失败冷却期时返回 False——此时应直接渲染纯文本，而非继续等待。
        """
        if not text or not self._enabled:
            return False
        if text in self._cache:
            return False
        return not self._in_failed_cooldown(text)

    def _in_failed_cooldown(self, text) -> bool:
        ts = self._failed.get(text)
        if ts is None:
            return False
        if time.time() - ts < self._FAILED_COOLDOWN:
            return True
        self._failed.pop(text, None)          # 冷却结束，允许再次尝试
        return False

    def _run(self):
        while True:
            text = self._queue.get()
            try:
                if not self._enabled:
                    continue
                pairs = self._resolve(text)
                if pairs is not None:
                    self._failed.pop(text, None)
                    if len(self._cache) >= 500:
                        self._cache.clear()
                    self._cache[text] = pairs
                else:
                    # 取词失败：记下冷却时间戳，让该行改走纯文本渲染。
                    if len(self._failed) >= 500:
                        self._failed.clear()
                    self._failed[text] = time.time()
                # 无论成功与否都发信号：成功→补注音重渲染；失败→解除阻塞、渲染纯文本。
                self.ready.emit()
            except Exception:
                pass
            finally:
                self._queued.discard(text)

    def _resolve(self, text):
        """向 server 请求分词；主窗口词典还在加载就重试，缺失主窗口则放弃。"""
        for _ in range(self._NOT_READY_RETRIES):
            if not self._enabled:
                return None
            resp = self._fetch_pairs(text)
            if not isinstance(resp, dict):
                return None                       # 无主窗口 / 请求失败：降级为纯文本
            if resp.get("ready"):
                pairs = []
                for pair in resp.get("pairs") or []:
                    if not pair:
                        continue
                    surface = pair[0] if len(pair) > 0 else ""
                    reading = pair[1] if len(pair) > 1 else None
                    if surface:
                        pairs.append((surface, reading or None))
                return pairs
            time.sleep(self._NOT_READY_INTERVAL)  # 词典加载中，稍后重试
        return None

    def clear(self):
        self._cache.clear()
        self._failed.clear()
        try:
            while True:
                self._queued.discard(self._queue.get_nowait())
        except queue.Empty:
            pass


# 注音图块的 img src 方案（同 langtag 机制，由 SubtitleTextEdit.loadResource 提供）。
_RUBY_SCHEME = "rubytk:"

# _furigana_line_html 的哨兵返回值：该日语行注音仍在获取中，本帧应阻塞
# （保持上一帧），等注音就绪后再整帧刷新，避免"先纯文本后注音"的闪烁。
_FURIGANA_PENDING = object()


# spec 只能用 URL 安全字符（QTextDocument 会把 img src 当 URL 规范化），
# 所以用 '.' 作分隔符、base64url 去掉 '=' 填充。
def _encode_ruby_spec(fs: int, non_final: bool, use_bundled: bool,
                      surface: str, reading) -> str:
    def b64(value):
        raw = base64.urlsafe_b64encode((value or "").encode("utf-8")).decode("ascii")
        return raw.rstrip("=")
    return (f"{int(fs)}.{1 if non_final else 0}.{1 if use_bundled else 0}"
            f".{b64(surface)}.{b64(reading or '')}")


def _decode_ruby_spec(spec: str):
    def unb64(value):
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    try:
        fs_str, nf_str, bundled_str, surface_b64, reading_b64 = spec.split(".", 4)
        return (int(fs_str), nf_str == "1", bundled_str == "1",
                unb64(surface_b64), unb64(reading_b64))
    except Exception:
        return None


# 用固定参考字串量各字体的「实际字形范围」（tight），而非 ascent/descent——后者含
# 大量内部行距，会在注音与正文之间留下空隙。参考串覆盖较高/带浊半浊/有降部的字形。
_KANA_REF = "あぁきさしせぬのゆよぐぱぽっ"
_KANJI_REF = "漢国鬱薔曜同"
_VISUAL_EXTENT_CACHE: dict = {}


def _visual_extent(fm: QFontMetricsF, ref: str, key) -> tuple[float, float]:
    """返回 (基线上方实际字形高度, 基线下方实际降部)，按 key 缓存。"""
    cached = _VISUAL_EXTENT_CACHE.get(key)
    if cached is None:
        r = fm.tightBoundingRect(ref)
        cached = (-r.top(), r.bottom())
        _VISUAL_EXTENT_CACHE[key] = cached
    return cached


def _make_ruby_pixmap(surface: str, reading: str, fs: int, non_final: bool,
                      use_bundled: bool, dpr: float = 1.0) -> QPixmap:
    """画一个「注音在上、正文在下」的 token 图块。

    同一行内所有 token（含无注音的）都用同一套竖直布局，保证注音基线、正文基线在
    整行内对齐。竖直排布按各字体的实际字形范围（tight）计算，让注音直接贴住正文，
    上下都不留字体内部行距造成的空隙。注音与正文水平居中（对应网页版 ruby-align:
    center，注音更宽时正文居中其下）。
    """
    families = _font_families_for_lang("ja", use_bundled)
    base_font = QFont()
    base_font.setFamilies(families)
    base_font.setPixelSize(int(fs))
    rt_font = QFont(base_font)
    # 主窗口宽 350px（server.py），命中网页版媒体查询 @media(max-width:768px)，
    # 此时 ruby rt 为 0.7em（非默认 0.5em）；悬浮窗对齐主窗口观感取 0.7。
    rt_px = max(6, round(fs * RUBY_RT_SCALE))
    rt_font.setPixelSize(rt_px)

    base_fm = QFontMetricsF(base_font)
    rt_fm = QFontMetricsF(rt_font)
    text = (surface or "").replace("\n", " ")
    rt_text = reading or ""
    base_w = base_fm.horizontalAdvance(text)
    rt_w = rt_fm.horizontalAdvance(rt_text) if rt_text else 0.0
    w = max(1.0, base_w, rt_w)

    fam_key = tuple(families)
    rt_asc, rt_desc = _visual_extent(rt_fm, _KANA_REF, ("rt", fam_key, rt_px))
    base_asc, _base_desc = _visual_extent(base_fm, _KANJI_REF, ("base", fam_key, int(fs)))
    base_desc = base_fm.descent()          # 正文降部沿用字体度量，保证与普通行竖直节奏一致

    top_pad = max(1.0, fs * 0.03)          # 注音顶部一点点留白，别贴到上一行
    gap = max(1.0, fs * 0.04)              # 注音底 → 正文顶 的小间隙（贴住即可）
    rt_baseline = top_pad + rt_asc
    base_top = rt_baseline + rt_desc + gap
    base_baseline = base_top + base_asc
    h = base_baseline + base_desc
    dpr = dpr or 1.0

    pm = QPixmap(max(1, math.ceil(w * dpr)), max(1, math.ceil(h * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    if rt_text:
        p.setFont(rt_font)
        p.setPen(QColor(RUBY_RT_COLOR))
        p.drawText(QPointF((w - rt_w) / 2, rt_baseline), rt_text)
    p.setFont(base_font)
    p.setPen(QColor(NONFINAL_COLOR if non_final else FINAL_COLOR))
    p.drawText(QPointF((w - base_w) / 2, base_baseline), text)
    p.end()
    return pm


def _ensure_speaker(spk):
    return "undefined" if spk is None else spk


class SubtitleModel:
    """维护 final / non-final token，并产出可渲染的「说话人块」结构。"""

    def __init__(self):
        self.final_tokens = []
        self.non_final_tokens = []

    def clear(self, preserve_existing=False):
        if preserve_existing:
            # 把进行中的 token 落定，避免重启时闪烁
            for tk in self.non_final_tokens:
                tk = dict(tk)
                tk["is_final"] = True
                self.final_tokens.append(tk)
        else:
            self.final_tokens = []
        self.non_final_tokens = []

    def apply_update(self, data: dict):
        for tk in (data.get("final_tokens") or []):
            if tk.get("text") == "<end>":
                continue
            self.final_tokens.append(tk)
        self.non_final_tokens = [
            tk for tk in (data.get("non_final_tokens") or [])
            if tk.get("text") != "<end>"
        ]

    # --- 构建渲染 token（final + non-final，必要时补 speculative 分隔） ---
    def _build_render_tokens(self):
        non_final = self.non_final_tokens or []
        has_nf_translation = any(
            (tk.get("translation_status") or "original") == "translation"
            for tk in non_final
        )
        if has_nf_translation:
            return [*self.final_tokens, *non_final]

        tokens = list(self.final_tokens)
        n = len(non_final)
        for i, tk in enumerate(non_final):
            tokens.append(tk)
            is_last = i == n - 1
            text = (tk.get("text") or "").rstrip()
            if (not is_last and not tk.get("is_separator")
                    and text and text[-1] in SENTENCE_PUNCT):
                tokens.append({"is_separator": True, "is_final": False})
        return tokens

    # --- 分句（移植 renderSubtitles 的归组算法，去掉 furigana/LLM 等） ---
    def build_blocks(self):
        tokens = self._build_render_tokens()
        sentences = []
        current = None

        def start_sentence(
            speaker,
            requires=None,
            translation_only=False,
        ):
            nonlocal current
            s = {
                "speaker": _ensure_speaker(speaker),
                "original": [],
                "translation": [],
                "original_lang": None,
                "translation_lang": None,
                "requires_translation": requires,
                "translation_only": translation_only,
                "fake_translation": False,
            }
            sentences.append(s)
            if not translation_only:
                current = s
            return s

        def find_last(speaker, predicate):
            spk = _ensure_speaker(speaker)
            for s in reversed(sentences):
                if s["speaker"] == spk and predicate(s):
                    return s
            return None

        for token in tokens:
            if token.get("is_separator"):
                if (current and current["requires_translation"] is not False
                        and not current["translation"]):
                    current["fake_translation"] = True
                current = None
                continue

            speaker = _ensure_speaker(token.get("speaker"))
            status = token.get("translation_status") or "original"

            if status == "translation":
                target = find_last(speaker, lambda s: not s["translation_only"])
                if target is None:
                    target = start_sentence(speaker, translation_only=True)
                if target["translation_lang"] is None and token.get("language"):
                    target["translation_lang"] = token.get("language")
                if not target["original_lang"] and token.get("source_language"):
                    target["original_lang"] = token.get("source_language")
                target["translation"].append(token)
            else:
                requires = status != "none"
                start_new = False
                if not current:
                    start_new = True
                elif current["speaker"] != speaker:
                    start_new = True
                elif current["translation_only"]:
                    start_new = True
                elif (current["requires_translation"] is not None
                      and current["requires_translation"] != requires):
                    start_new = True
                if start_new:
                    current = start_sentence(speaker, requires=requires)
                if current["requires_translation"] is None:
                    current["requires_translation"] = requires
                lang = token.get("language")
                if current["original_lang"] is None and lang:
                    current["original_lang"] = lang
                elif current["original_lang"] and lang and current["original_lang"] != lang:
                    current = start_sentence(speaker, requires=requires)
                    current["original_lang"] = lang
                current["original"].append(token)

        # 归并为说话人块
        blocks = []
        block = None
        for s in sentences:
            if not s["original"] and not s["translation"]:
                continue
            if not block or block["speaker"] != s["speaker"]:
                if block:
                    blocks.append(block)
                block = {"speaker": s["speaker"], "sentences": []}
            block["sentences"].append(s)
        if block:
            blocks.append(block)
        return blocks

    def trim_final_tokens_to_recent_sentences(self, max_sentences: int) -> None:
        """Trim history on the same sentence boundaries used for display."""
        if max_sentences <= 0 or not self.final_tokens:
            return

        saved_non_final = self.non_final_tokens
        self.non_final_tokens = []
        try:
            blocks = self.build_blocks()
        finally:
            self.non_final_tokens = saved_non_final

        sentences = [
            sentence
            for block in blocks
            for sentence in block["sentences"]
            if sentence["original"] or sentence["translation"]
        ]
        if len(sentences) <= max_sentences:
            return

        retained = sentences[-max_sentences:]
        retained_token_ids = {
            id(token)
            for sentence in retained
            for token in (sentence["original"] + sentence["translation"])
        }
        if not retained_token_ids:
            return

        first_index = next(
            (
                index
                for index, token in enumerate(self.final_tokens)
                if id(token) in retained_token_ids
            ),
            None,
        )
        if first_index is not None and first_index > 0:
            self.final_tokens = self.final_tokens[first_index:]


# ===========================================================================
# 语言标识：渲染成真正的圆角矩形（QTextEdit 的富文本不支持 span 的 border-radius，
# 所以画成内联图片）。颜色沿用网页版 dark 主题（TAG_BG / TAG_FG）。
# ===========================================================================
_TAG_SCHEME = "langtag:"


def _make_tag_pixmap(text: str, fs: int, dpr: float = 1.0) -> QPixmap:
    text = (text or "").upper()
    # 等宽字体（与网页版 .language-tag 的 monospace 一致）。
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setPixelSize(fs)
    font.setBold(True)
    fm = QFontMetrics(font)
    pad_x = max(5, int(fs * 0.6))
    pad_y = max(2, int(fs * 0.3))
    pill_w = fm.horizontalAdvance(text) + pad_x * 2
    pill_h = fm.height() + pad_y * 2
    gap = max(4, int(fs * 0.45))           # 标识右侧与正文的间距
    w = pill_w + gap
    dpr = dpr or 1.0

    pm = QPixmap(max(1, int(w * dpr)), max(1, int(pill_h * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 41))  # TAG_BG = rgba(255,255,255,0.16)
    radius = pill_h * 0.4                   # 明显的圆角矩形
    p.drawRoundedRect(QRectF(0, 0, pill_w, pill_h), radius, radius)
    p.setPen(QColor(TAG_FG))
    p.setFont(font)
    p.drawText(QRectF(0, 0, pill_w, pill_h), int(Qt.AlignCenter), text)
    p.end()
    return pm


class SubtitleTextEdit(QTextEdit):
    """QTextEdit with inline language badges and rounded per-line backdrops."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tag_cache = {}
        self._ruby_cache = {}
        self._backdrop_enabled = False
        self._backdrop_alpha = 0
        self._backdrop_padding = 0
        self.viewport().setAutoFillBackground(False)

    def setTextBackdrop(self, enabled: bool, alpha: int, padding: int) -> None:
        state = (bool(enabled), max(0, min(255, int(alpha))), max(0, int(padding)))
        current = (self._backdrop_enabled, self._backdrop_alpha, self._backdrop_padding)
        if state == current:
            return
        self._backdrop_enabled, self._backdrop_alpha, self._backdrop_padding = state
        # Keep enough document inset for the backdrop's left/right padding so
        # rounded corners are never clipped by the viewport.
        self.document().setDocumentMargin(
            max(4, self._backdrop_padding) if self._backdrop_enabled else 4)
        self.viewport().update()

    def _backdrop_rects(self) -> list[QRectF]:
        """Return viewport-space rects for every laid-out visual subtitle line.

        QTextLine's height already contains the font's equal top/bottom leading.
        Extending its natural width by the same apparent leading adds the missing
        left/right inset. Inline image width is part of naturalTextRect, so the
        language badge is included automatically.
        """
        rects = []
        document = self.document()
        layout = document.documentLayout()
        offset = QPointF(
            -self.horizontalScrollBar().value(),
            -self.verticalScrollBar().value(),
        )
        block = document.begin()
        while block.isValid():
            block_rect = layout.blockBoundingRect(block)
            text_layout = block.layout()
            for index in range(text_layout.lineCount()):
                line = text_layout.lineAt(index)
                if line.naturalTextWidth() <= 0:
                    continue
                rect = line.naturalTextRect().translated(block_rect.topLeft() + offset)
                content_bounds = self._line_content_vertical_bounds(block, line)
                if content_bounds is not None:
                    content_top, content_bottom = content_bounds
                    content_center = (content_top + content_bottom) / 2
                    line_center = line.naturalTextRect().center().y()
                    rect.translate(0, content_center - line_center)
                rect.adjust(-self._backdrop_padding, 0,
                            self._backdrop_padding, 0)
                rects.append(rect)
            block = block.next()
        return rects

    def _line_content_vertical_bounds(self, block, line):
        """Measure actual glyph/image bounds in block-local coordinates."""
        line_start = line.textStart()
        line_end = line_start + line.textLength()
        top = None
        bottom = None

        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fragment_start = fragment.position() - block.position()
            fragment_end = fragment_start + fragment.length()
            start = max(line_start, fragment_start)
            end = min(line_end, fragment_end)
            if start >= end:
                iterator += 1
                continue

            char_format = fragment.charFormat()
            if char_format.isImageFormat():
                name = char_format.toImageFormat().name()
                if name.startswith(_TAG_SCHEME):
                    pixmap = self._tag_pixmap(name[len(_TAG_SCHEME):])
                elif name.startswith(_RUBY_SCHEME):
                    pixmap = self._ruby_pixmap(name[len(_RUBY_SCHEME):])
                else:
                    iterator += 1
                    continue
                image_height = pixmap.height() / max(1.0, pixmap.devicePixelRatio())
                if char_format.verticalAlignment() == QTextCharFormat.AlignMiddle:
                    item_top = line.y() + (line.height() - image_height) / 2
                else:
                    # 无 vertical-align 的内联图片：底边落在文本基线上
                    item_top = line.y() + line.ascent() - image_height
                item_bottom = item_top + image_height
            else:
                text_start = start - fragment_start
                text_end = end - fragment_start
                # \u200b\uff1a\u5047\u540d\u884c token \u56fe\u5757\u95f4\u7684\u6362\u884c\u70b9\uff0c\u96f6\u5bbd\u4e0d\u53ef\u89c1\uff1b
                # tightBoundingRect \u5bf9\u5b83\u8fd4\u56de (100000,100000) \u54e8\u5175\u503c\uff0c\u5fc5\u987b\u5254\u9664\u3002
                visible_text = (fragment.text()[text_start:text_end]
                                .replace("\u2028", "").replace("\u200b", ""))
                if not visible_text:
                    iterator += 1
                    continue
                bounds = QFontMetricsF(char_format.font()).tightBoundingRect(visible_text)
                baseline = line.y() + line.ascent()
                item_top = baseline + bounds.top()
                item_bottom = baseline + bounds.bottom()

            top = item_top if top is None else min(top, item_top)
            bottom = item_bottom if bottom is None else max(bottom, item_bottom)
            iterator += 1

        return None if top is None else (top, bottom)

    def paintEvent(self, event):
        if self._backdrop_enabled and self._backdrop_alpha > 0:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setClipRect(event.rect())
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, self._backdrop_alpha))
            for rect in self._backdrop_rects():
                # Keep this for easy restoration if rounded backdrops are wanted again:
                # painter.drawRoundedRect(rect, TEXT_BACKDROP_RADIUS, TEXT_BACKDROP_RADIUS)
                painter.drawRect(rect)
            painter.end()
        super().paintEvent(event)

    def loadResource(self, rtype, name):
        try:
            s = name.toString()
        except Exception:
            s = str(name)
        if rtype == QTextDocument.ImageResource and s.startswith(_TAG_SCHEME):
            return self._tag_pixmap(s[len(_TAG_SCHEME):])
        if rtype == QTextDocument.ImageResource and s.startswith(_RUBY_SCHEME):
            return self._ruby_pixmap(s[len(_RUBY_SCHEME):])
        return super().loadResource(rtype, name)

    def _tag_pixmap(self, spec: str) -> QPixmap:
        cached = self._tag_cache.get(spec)
        if cached is not None:
            return cached
        fs_str, _, text = spec.partition("-")  # fs 是数字，第一个 '-' 即分隔符
        try:
            fs = int(fs_str)
        except ValueError:
            fs = 12
        pm = _make_tag_pixmap(text, fs, self.devicePixelRatioF())
        self._tag_cache[spec] = pm
        return pm

    def _ruby_pixmap(self, spec: str) -> QPixmap:
        cached = self._ruby_cache.get(spec)
        if cached is not None:
            return cached
        decoded = _decode_ruby_spec(spec)
        if decoded is None:
            pm = QPixmap(1, 1)
            pm.fill(Qt.transparent)
        else:
            fs, non_final, use_bundled, surface, reading = decoded
            pm = _make_ruby_pixmap(surface, reading, fs, non_final, use_bundled,
                                   self.devicePixelRatioF())
        if len(self._ruby_cache) >= 800:
            self._ruby_cache.clear()
        self._ruby_cache[spec] = pm
        return pm


# ===========================================================================
# Windows 专用：让窗口不抢焦点（点击不激活），并置顶。
# ===========================================================================
def _apply_no_activate_style(hwnd: int, app_window: bool = False):
    """让窗口点击不激活并置顶。

    app_window=True 时把窗口注册为任务栏窗口（WS_EX_APPWINDOW，去掉 WS_EX_TOOLWINDOW），
    这样它会出现在 Windows 任务栏，并能被 OBS 等软件作为独立窗口捕捉；
    False（默认）则保持工具窗口，不进任务栏。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        is_64bit = ctypes.sizeof(ctypes.c_void_p) == 8

        get_window_long = (
            user32.GetWindowLongPtrW if is_64bit else user32.GetWindowLongW
        )
        set_window_long = (
            user32.SetWindowLongPtrW if is_64bit else user32.SetWindowLongW
        )
        long_type = ctypes.c_longlong if is_64bit else ctypes.c_long
        get_window_long.restype = long_type
        get_window_long.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
        set_window_long.restype = long_type
        set_window_long.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.c_int,
            long_type,
        )

        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_NOACTIVATE = 0x08000000
        style = int(get_window_long(hwnd, GWL_EXSTYLE))
        style |= WS_EX_NOACTIVATE
        if app_window:
            style |= WS_EX_APPWINDOW
            style &= ~WS_EX_TOOLWINDOW
        else:
            style |= WS_EX_TOOLWINDOW
        set_window_long(hwnd, GWL_EXSTYLE, long_type(style))

        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            ctypes.wintypes.HWND(hwnd),
            ctypes.wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    except Exception:
        pass


def _set_click_through(hwnd: int, enabled: bool):
    """切换 Windows 鼠标穿透（WS_EX_TRANSPARENT）。

    开启后该窗口不再接收任何鼠标事件，点击会落到下方窗口；关闭则恢复正常。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        is_64bit = ctypes.sizeof(ctypes.c_void_p) == 8

        get_window_long = (
            user32.GetWindowLongPtrW if is_64bit else user32.GetWindowLongW
        )
        set_window_long = (
            user32.SetWindowLongPtrW if is_64bit else user32.SetWindowLongW
        )
        long_type = ctypes.c_longlong if is_64bit else ctypes.c_long
        get_window_long.restype = long_type
        get_window_long.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
        set_window_long.restype = long_type
        set_window_long.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.c_int,
            long_type,
        )

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        style = int(get_window_long(hwnd, GWL_EXSTYLE))
        if enabled:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
        set_window_long(hwnd, GWL_EXSTYLE, long_type(style))
    except Exception:
        pass


# ===========================================================================
# 悬浮窗
# ===========================================================================
class OverlayWindow(QWidget):
    _restart_finished = Signal(bool)

    def __init__(self, server_url: str):
        super().__init__()
        self.server_url = server_url.rstrip("/")
        self.model = SubtitleModel()
        # LLM 译文更新（改进 / 混合 / 准确）：按 sentence_id 覆盖 STT 译文。
        # 悬浮窗只展示最终译文，不做网页版的绿色（已改进）/灰色（临时译文）标注。
        self._refined_by_sid: dict[str, str] = {}
        self._refined_lang_by_sid: dict[str, str] = {}
        self.settings = QSettings("RealtimeSubtitle", "Overlay")

        self.font_size = int(self.settings.value("font_size", 20))
        self.bg_alpha = int(self.settings.value("bg_alpha", DEFAULT_ALPHA))
        self.background_mode = _normalize_background_mode(
            self.settings.value("background_mode", BACKGROUND_MODE_WINDOW))
        # 显示模式：both（原文+译文）/ original（仅原文）/ translation（仅译文）
        self.display_mode = str(self.settings.value("display_mode", "both"))
        self.flow_direction = str(self.settings.value("flow_direction", "up"))
        if self.flow_direction not in ("up", "down"):
            self.flow_direction = "up"
        self.furigana_enabled = str(
            self.settings.value("furigana_enabled", "false")).lower() in ("true", "1")
        self.furigana = FuriganaService(self._fetch_furigana, self)
        self.furigana.ready.connect(self._on_furigana_ready)
        if self.furigana_enabled:
            self.furigana.set_enabled(True)
        self.is_paused = False
        self.use_bundled_cjk_fonts = False
        self._restart_in_flight = False
        self._passthrough = False
        self._click_through_on = False
        self._mouse_inside = False

        self._drag_offset = None
        self._resize_edges = None
        self._resize_start_geo = None
        self._resize_start_mouse = None

        self.tooltip_filter = ToolTipEventFilter(self)

        # 缩放窗口时按 ~33fps 节流重渲染，让可见行数实时随高度变化（不必等下一帧字幕）。
        self._resize_render_timer = QTimer(self)
        self._resize_render_timer.setSingleShot(True)
        self._resize_render_timer.setInterval(30)
        self._resize_render_timer.timeout.connect(self._render)

        self._init_ui()
        self._restore_geometry()
        self._init_ws()
        self._restart_finished.connect(self._on_restart_finished)

        # 轮询光标位置以决定按钮显隐 + 穿透模式下的点击区域（比 enter/leave 更稳）。
        # 穿透模式要靠它实时切换「鼠标在按钮上→可点 / 其他地方→穿透」，故频率高一些。
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(50)
        self._hover_timer.timeout.connect(self._update_button_visibility)
        self._hover_timer.start()

    # --------------------------------------------------------------- UI ----
    def _init_ui(self):
        self.setWindowTitle("Realtime Subtitle Overlay")
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_W, MIN_H)
        self._apply_windows_no_activate_style()

        # 字幕文本区：不接收鼠标事件，让拖动/缩放在任意位置可用
        self.text = SubtitleTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFrameStyle(0)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.text.setStyleSheet("background: transparent; border: none;")
        self.text.setMouseTracking(True)

        # 右下角按钮条
        self.button_bar = QWidget(self)
        self.button_bar.setMouseTracking(True)
        bar_layout = QHBoxLayout(self.button_bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(4)

        self.btn_font_dec = self._make_button("A-", tr("font_dec"), self._dec_font)
        self.btn_font_inc = self._make_button("A+", tr("font_inc"), self._inc_font)
        self.btn_alpha_dec = self._make_button("", tr("alpha_dec"), self._dec_alpha, icon="layers-minus")
        self.btn_alpha_inc = self._make_button("", tr("alpha_inc"), self._inc_alpha, icon="layers-plus")
        self.btn_background = self._make_button(
            "", "", self._toggle_background_mode, icon="subtitles")
        self.btn_display = self._make_button("O/T", "", self._cycle_display)
        self.btn_furigana = self._make_button("あ", "", self._toggle_furigana)
        self.btn_flow = self._make_button("", "", self._toggle_flow_direction,
                                          icon="arrow-up-from-line")
        self.btn_passthrough = self._make_button("", "", self._toggle_passthrough, icon="mouse-pointer-2")
        self.btn_restart = self._make_button("", tr("restart"), self._restart_recognition, icon="rotate-cw")
        self.btn_pause = self._make_button("", tr("pause"), self._toggle_pause, icon="pause")
        self.btn_close = self._make_button("", tr("close"), self.close, icon="x")
        self._all_buttons = (
            self.btn_font_dec, self.btn_font_inc,
            self.btn_alpha_dec, self.btn_alpha_inc,
            self.btn_background,
            self.btn_display, self.btn_furigana, self.btn_flow,
            self.btn_passthrough,
            self.btn_restart, self.btn_pause, self.btn_close,
        )
        for b in self._all_buttons:
            bar_layout.addWidget(b)
        # Create opacity effects for all other buttons
        self._other_buttons = (
            self.btn_font_dec, self.btn_font_inc,
            self.btn_alpha_dec, self.btn_alpha_inc,
            self.btn_background,
            self.btn_display, self.btn_furigana, self.btn_flow,
            self.btn_restart, self.btn_pause, self.btn_close
        )
        self._btn_opacity_effects = {}
        for btn in self._other_buttons:
            effect = QGraphicsOpacityEffect(btn)
            btn.setGraphicsEffect(effect)
            self._btn_opacity_effects[btn] = effect

        self._update_background_button()
        self._update_display_button()
        self._update_furigana_button()
        self._update_flow_button()
        self._update_passthrough_button()

        self.button_bar.adjustSize()
        self.button_bar.setVisible(False)

        self._render()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_windows_no_activate_style()
        self._render()

    def _apply_windows_no_activate_style(self):
        """Keep clicks on the overlay from activating it on Windows fullscreen apps."""
        _apply_no_activate_style(int(self.winId()), app_window=True)

    _BTN_QSS = (
        "QPushButton {"
        "  color: #f3f4f6;"
        "  background: rgba(128,128,128,0.30);"
        "  border: none; border-radius: 6px;"
        "  font-size: 13px; font-weight: 600;"
        "}"
        "QPushButton:hover { background: rgba(128,128,128,0.34); }"
        "QPushButton:pressed { background: rgba(128,128,128,0.48); }"
    )
    _BTN_QSS_ACTIVE = (
        "QPushButton {"
        "  color: #bfdbfe;"
        "  background: rgba(128,128,128,0.50);"
        "  border: none; border-radius: 6px;"
        "  font-size: 13px; font-weight: 600;"
        "}"
        "QPushButton:hover { background: rgba(128,128,128,0.62); }"
        "QPushButton:pressed { background: rgba(128,128,128,0.74); }"
    )
    _BTN_QSS_TEXT = (
        "QPushButton {"
        "  color: #f3f4f6;"
        "  background: rgba(96,96,96,0.40);"
        "  border: none; border-radius: 6px;"
        "  font-size: 13px; font-weight: 600;"
        "}"
        "QPushButton:hover { background: rgba(96,96,96,0.52); }"
        "QPushButton:pressed { background: rgba(96,96,96,0.64); }"
    )
    _BTN_QSS_TEXT_ACTIVE = (
        "QPushButton {"
        "  color: #bfdbfe;"
        "  background: rgba(96,96,96,0.58);"
        "  border: none; border-radius: 6px;"
        "  font-size: 13px; font-weight: 600;"
        "}"
        "QPushButton:hover { background: rgba(96,96,96,0.70); }"
        "QPushButton:pressed { background: rgba(96,96,96,0.80); }"
    )

    def _button_style(self, active: bool = False) -> str:
        if self.background_mode == BACKGROUND_MODE_TEXT:
            return self._BTN_QSS_TEXT_ACTIVE if active else self._BTN_QSS_TEXT
        return self._BTN_QSS_ACTIVE if active else self._BTN_QSS

    def _refresh_button_styles(self) -> None:
        for button in self._all_buttons:
            active = (
                (button is self.btn_background
                 and self.background_mode == BACKGROUND_MODE_TEXT)
                or (button is self.btn_passthrough and self._passthrough)
                or (button is self.btn_furigana and self.furigana_enabled)
            )
            button.setStyleSheet(self._button_style(active))

    def _make_button(self, text, tip, slot, icon=None):
        b = VectorButton(text, tip, slot, icon, self.button_bar)
        b.setStyleSheet(self._button_style())
        b.installEventFilter(self.tooltip_filter)
        return b

    # ------------------------------------------------------------ geometry --
    def _restore_geometry(self):
        geo = self.settings.value("geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
                return
            except Exception:
                pass
        # 默认：屏幕底部居中
        screen = QApplication.primaryScreen().availableGeometry()
        w, h = 640, 200
        self.setGeometry(
            screen.left() + (screen.width() - w) // 2,
            screen.bottom() - h - 60,
            w, h,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        margin = 12
        self.text.setGeometry(
            margin, margin,
            max(0, self.width() - 2 * margin),
            max(0, self.height() - 2 * margin),
        )
        self._reposition_buttons()
        # 高度变了 -> 可见行数变了，实时重渲染（节流，避免连续缩放时狂刷）。
        # 用 getattr 兜底：构造期 _restore_geometry 的 setGeometry 也会触发本事件，
        # 此时节流定时器可能还没建好。
        timer = getattr(self, "_resize_render_timer", None)
        if timer is not None and not timer.isActive():
            timer.start()

    def _reposition_buttons(self):
        self.button_bar.adjustSize()
        bw = self.button_bar.width()
        bh = self.button_bar.height()
        pad = 8
        self.button_bar.move(self.width() - bw - pad, self.height() - bh - pad)

    # --------------------------------------------------------------- paint --
    def paintEvent(self, event):
        painter = QPainter(self)
        _paint_overlay_background(
            painter, self.rect(), self.bg_alpha, self.background_mode,
            show_outline=self._mouse_inside)

    # -------------------------------------------------- 鼠标拖动 / 缩放 ----
    def _edges_at(self, pos):
        r = self.rect()
        left = pos.x() <= RESIZE_MARGIN
        right = pos.x() >= r.width() - RESIZE_MARGIN
        top = pos.y() <= RESIZE_MARGIN
        bottom = pos.y() >= r.height() - RESIZE_MARGIN
        return left, top, right, bottom

    @staticmethod
    def _cursor_for_edges(edges):
        left, top, right, bottom = edges
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        if top or bottom:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        edges = self._edges_at(pos)
        if any(edges):
            self._resize_edges = edges
            self._resize_start_geo = self.geometry()
            self._resize_start_mouse = event.globalPosition().toPoint()
        else:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._resize_edges is not None:
            self._do_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        # 未按下：根据边缘更新光标
        self.setCursor(self._cursor_for_edges(self._edges_at(event.position().toPoint())))

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        if self._resize_edges is not None:
            self._resize_edges = None
            self._save_geometry()
        else:
            self._save_geometry()
        self.setCursor(Qt.ArrowCursor)

    def _do_resize(self, global_pos):
        left, top, right, bottom = self._resize_edges
        geo = QRect(self._resize_start_geo)
        delta = global_pos - self._resize_start_mouse
        if left:
            new_left = geo.left() + delta.x()
            geo.setLeft(min(new_left, geo.right() - MIN_W))
        if right:
            geo.setRight(max(geo.right() + delta.x(), geo.left() + MIN_W))
        if top:
            new_top = geo.top() + delta.y()
            geo.setTop(min(new_top, geo.bottom() - MIN_H))
        if bottom:
            geo.setBottom(max(geo.bottom() + delta.y(), geo.top() + MIN_H))
        self.setGeometry(geo)

    # ------------------------------------------------ 按钮显隐（悬停） ----
    def _update_button_visibility(self):
        pos = QCursor.pos()
        inside = self.geometry().contains(pos)
        if inside != self._mouse_inside:
            self._mouse_inside = inside
            self.update()
        # 按钮条：鼠标在窗口内才显示（穿透模式下也照常显示）。
        if inside != self.button_bar.isVisible():
            self.button_bar.setVisible(inside)
            if inside:
                self._reposition_buttons()
                self.button_bar.raise_()
        # 穿透模式：仅当鼠标停留在“鼠标穿透”按钮上时才关闭穿透（使其可点）；其他看不见的位置均可穿透
        if self._passthrough:
            over_buttons = (self.button_bar.isVisible()
                            and self._button_global_rect(self.btn_passthrough).contains(pos))
            self._set_click_through_state(not over_buttons)

    # ----------------------------------------------------------- 按钮动作 --
    def _inc_font(self):
        self.font_size = min(self.font_size + 2, 72)
        self.settings.setValue("font_size", self.font_size)
        self._render()

    def _dec_font(self):
        self.font_size = max(self.font_size - 2, 10)
        self.settings.setValue("font_size", self.font_size)
        self._render()

    def _step_alpha(self, direction: int):
        """在感知等距的不透明度挡位间移动（+1 更不透明 / -1 更透明）。"""
        # 先把当前值吸附到最接近的挡位，再朝目标方向走一挡。
        idx = min(range(len(ALPHA_LEVELS)),
                  key=lambda k: abs(ALPHA_LEVELS[k] - self.bg_alpha))
        idx = max(0, min(len(ALPHA_LEVELS) - 1, idx + direction))
        self.bg_alpha = ALPHA_LEVELS[idx]
        self.settings.setValue("bg_alpha", self.bg_alpha)
        self.update()
        self._render()

    def _inc_alpha(self):
        self._step_alpha(+1)

    def _dec_alpha(self):
        self._step_alpha(-1)

    def _update_background_button(self):
        text_only = self.background_mode == BACKGROUND_MODE_TEXT
        self.btn_background.setStyleSheet(self._button_style(text_only))
        self.btn_background.setToolTip(tr(
            "background_text" if text_only else "background_window"))

    def _toggle_background_mode(self):
        self.background_mode = (
            BACKGROUND_MODE_WINDOW
            if self.background_mode == BACKGROUND_MODE_TEXT
            else BACKGROUND_MODE_TEXT)
        self.settings.setValue("background_mode", self.background_mode)
        self._update_background_button()
        self._refresh_button_styles()
        self._last_html = None
        self.update()
        self._render()

    _DISPLAY_LABELS = {"both": "O/T", "original": "O", "translation": "T"}
    def _update_display_button(self):
        mode = self.display_mode if self.display_mode in self._DISPLAY_LABELS else "both"
        self.btn_display.setText(self._DISPLAY_LABELS[mode])
        self.btn_display.setToolTip(tr(f"display_{mode}"))

    def _cycle_display(self):
        order = ["both", "original", "translation"]
        idx = order.index(self.display_mode) if self.display_mode in order else 0
        self.display_mode = order[(idx + 1) % len(order)]
        self.settings.setValue("display_mode", self.display_mode)
        self._update_display_button()
        self._last_html = None   # 模式变了，强制重渲染
        self._render()

    def _update_furigana_button(self):
        self.btn_furigana.setStyleSheet(self._button_style(self.furigana_enabled))
        self.btn_furigana.setToolTip(tr(
            "furigana_on" if self.furigana_enabled else "furigana_off"))

    def _toggle_furigana(self):
        self.furigana_enabled = not self.furigana_enabled
        self.settings.setValue("furigana_enabled", self.furigana_enabled)
        # 与网页版 setEnabled 一致：关闭时清缓存并停止排队。
        self.furigana.set_enabled(self.furigana_enabled)
        self._update_furigana_button()
        # 不强制清 _last_html：开启时保留当前纯文本帧，等注音就绪后整帧切换，
        # 避免出现"无原文行→原文+注音"的中间态；关闭时 HTML 自然不同会重建。
        self._render()

    def _fetch_furigana(self, text):
        """FuriganaService 的取词回调：POST /furigana，返回解析后的 JSON（或 None）。"""
        return self._post_json("/furigana", {"text": text})

    def _on_furigana_ready(self):
        """收到一批注音结果（或取词失败）：重渲染一次。

        不清 _last_html：让 _render 的 HTML 差异与阻塞判断自行决定——注音就绪则
        整帧切换，仍有其他行未就绪则继续保持上一帧。
        """
        self._render()

    def _update_flow_button(self):
        flowing_down = self.flow_direction == "down"
        self.btn_flow.setIconName(
            "arrow-down-to-line" if flowing_down else "arrow-up-from-line")
        self.btn_flow.setToolTip(tr("flow_down" if flowing_down else "flow_up"))

    def _toggle_flow_direction(self):
        self.flow_direction = "down" if self.flow_direction == "up" else "up"
        self.settings.setValue("flow_direction", self.flow_direction)
        self._update_flow_button()
        self._last_html = None
        self._render()

    # ----------------------------------------------------- 鼠标穿透模式 ----
    def _button_bar_global_rect(self) -> QRect:
        tl = self.button_bar.mapToGlobal(QPoint(0, 0))
        return QRect(tl, self.button_bar.size())

    def _button_global_rect(self, btn) -> QRect:
        """获取单个按钮的全局屏幕物理区域"""
        tl = btn.mapToGlobal(QPoint(0, 0))
        return QRect(tl, btn.size())

    def _set_click_through_state(self, enabled: bool):
        """按需切换鼠标穿透，仅在状态变化时调用系统 API。"""
        if enabled == self._click_through_on:
            return
        self._click_through_on = enabled
        _set_click_through(int(self.winId()), enabled)

    def _toggle_passthrough(self):
        self._passthrough = not self._passthrough
        self._update_passthrough_button()
        if self._passthrough:
            # 进入：按当前鼠标位置立刻决定穿透/可点（通常鼠标在按钮上，先保持可点）。
            self._update_button_visibility()
        else:
            # 退出：确保恢复正常可点。
            self._set_click_through_state(False)

    def _update_passthrough_button(self):
        self.btn_passthrough.setStyleSheet(self._button_style(self._passthrough))
        self.btn_passthrough.setToolTip(tr("passthrough_on") if self._passthrough else tr("passthrough_off"))

        # In passthrough mode, hide (opacity 0) and disable other buttons, keeping their layout position
        if hasattr(self, "_btn_opacity_effects"):
            for btn in self._other_buttons:
                effect = self._btn_opacity_effects[btn]
                if self._passthrough:
                    effect.setOpacity(0.0)
                    btn.setEnabled(False)
                    btn.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                else:
                    effect.setOpacity(1.0)
                    btn.setEnabled(True)
                    btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    def _toggle_pause(self):
        target = "/resume" if self.is_paused else "/pause"
        # 乐观更新；网络请求放后台线程避免卡 UI
        self.is_paused = not self.is_paused
        self.btn_pause.setIconName("play" if self.is_paused else "pause")
        self.btn_pause.setToolTip(tr("resume") if self.is_paused else tr("pause"))
        threading.Thread(
            target=self._post, args=(target,), daemon=True
        ).start()

    def _restart_recognition(self):
        if self._restart_in_flight:
            return
        self._restart_in_flight = True
        self.btn_restart.setEnabled(False)
        self.btn_restart.setIconName(None)
        self.btn_restart.setText("...")
        self.btn_restart.setToolTip(tr("restarting"))
        threading.Thread(target=self._restart_worker, daemon=True).start()

    def _restart_worker(self):
        ok = self._post("/restart")
        self._restart_finished.emit(ok)

    def _on_restart_finished(self, ok: bool):
        self._restart_in_flight = False
        self.btn_restart.setEnabled(True)
        self.btn_restart.setText("")
        self.btn_restart.setIconName("rotate-cw")
        self.btn_restart.setToolTip(tr("restart") if ok else tr("restart_failed"))

    def _post(self, path, payload=None):
        try:
            data = b""
            headers = {}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers = {"Content-Type": "application/json"}
            req = urllib.request.Request(
                self.server_url + path, data=data, headers=headers, method="POST"
            )
            urllib.request.urlopen(req, timeout=8).read()
            return True
        except Exception:
            return False

    def _post_json(self, path, payload=None):
        """POST 并返回解析后的 JSON 响应体；失败返回 None。"""
        try:
            data = b""
            headers = {}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers = {"Content-Type": "application/json"}
            req = urllib.request.Request(
                self.server_url + path, data=data, headers=headers, method="POST"
            )
            body = urllib.request.urlopen(req, timeout=8).read()
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    # --------------------------------------------------------------- WS ----
    def _init_ws(self):
        scheme = "wss" if self.server_url.startswith("https") else "ws"
        host = self.server_url.split("://", 1)[-1]
        ws_url = f"{scheme}://{host}/ws?client=overlay"
        self.bridge = WsBridge()
        self.bridge.message.connect(self._on_message)
        self.ws_client = WsClient(ws_url, self.bridge)
        self.ws_client.start()

    def _on_message(self, raw: str):
        try:
            data = json.loads(raw)
        except Exception:
            return
        mtype = data.get("type")
        if mtype == "update":
            self.model.apply_update(data)
            self._render()
        elif mtype == "refine_result":
            self._apply_refine_result(data)
        elif mtype == "clear":
            preserve = bool(data.get("preserve_existing"))
            self.model.clear(preserve_existing=preserve)
            # 完整清空（重启且不保留）时，旧句子的译文覆盖已失效——丢弃以免占用内存；
            # 保留模式下句子及其 sentence_id 仍在，覆盖需一并保留。
            if not preserve:
                self._refined_by_sid.clear()
                self._refined_lang_by_sid.clear()
            self._last_html = None
            self._render()
        elif mtype == "overlay_visibility":
            visible = bool(data.get("visible"))
            if visible:
                self.show()
            else:
                self.hide()
        elif mtype == "recognition_paused":
            self.is_paused = bool(data.get("paused"))
            self.btn_pause.setIconName("play" if self.is_paused else "pause")
            self.btn_pause.setToolTip(tr("resume") if self.is_paused else tr("pause"))
        elif mtype == "subtitle_font_preference":
            enabled = bool(data.get("use_bundled_cjk_fonts"))
            if self.use_bundled_cjk_fonts != enabled:
                self.use_bundled_cjk_fonts = enabled
                self._last_html = None
                self._render()

    def _apply_refine_result(self, data: dict):
        """应用一条 LLM 译文更新，按 sentence_id 覆盖对应句子的 STT 译文。

        no_change=True 表示 LLM 认为原译文已够好，保持不变；否则用 refined_translation
        覆盖。准确模式下 STT 无内置译文，这里的覆盖即该句唯一的译文来源。
        """
        sid = data.get("sentence_id")
        if not sid:
            return
        sid = str(sid)
        if data.get("no_change"):
            return
        refined = (data.get("refined_translation") or "").strip()
        if not refined:
            return
        self._refined_by_sid[sid] = refined
        target_lang = (data.get("target_lang") or "").strip()
        if target_lang:
            self._refined_lang_by_sid[sid] = target_lang
        self._trim_refine_maps()
        # 覆盖变化必然改变 HTML；清掉缓存强制重渲染。
        self._last_html = None
        self._render()

    def _trim_refine_maps(self, cap: int = 200):
        for m in (self._refined_by_sid, self._refined_lang_by_sid):
            while len(m) > cap:
                m.pop(next(iter(m)))  # dict 保序：淘汰最旧的一条

    @staticmethod
    def _sentence_sid(sentence: dict):
        for key in ("translation", "original"):
            for tk in sentence.get(key) or []:
                sid = tk.get("llm_sentence_id")
                if sid:
                    return str(sid)
        return None

    def _sentence_translation_override(self, sentence: dict):
        """若该句有 LLM 译文更新，返回 (text, lang)，否则 None。"""
        sid = self._sentence_sid(sentence)
        if not sid:
            return None
        text = self._refined_by_sid.get(sid)
        if not text:
            return None
        # 改进 / 混合：沿用 STT 译文行的语言标签；准确模式无 STT 译文，退回 LLM 目标语言。
        lang = sentence.get("translation_lang") or self._refined_lang_by_sid.get(sid)
        return text, lang

    # ----------------------------------------------------------- 渲染 ------
    def _max_visible_lines(self) -> int:
        """根据文本区高度与字号估算大约能放下多少行字幕。"""
        line_px = self.font_size * 1.2 + 2          # line-height 120% + 2px 间距
        usable_h = max(1, self.text.height())
        return max(1, int(usable_h / line_px) + 1)

    def _trim_tokens(self, max_lines: int) -> None:
        """限制历史句子数量，避免 build_blocks / setHtml 随时间越跑越慢卡 CPU。"""
        cap = max(120, max_lines * 12)
        ft = self.model.final_tokens
        if len(ft) > cap:
            self.model.trim_final_tokens_to_recent_sentences(max(20, max_lines * 4))

    def _render(self):
        max_lines = self._max_visible_lines()
        self._trim_tokens(max_lines)
        blocks = self.model.build_blocks()
        record_groups, blocked = self._build_line_groups(blocks)

        sb = self.text.verticalScrollBar()
        # 有日语行的注音仍在获取：保持上一帧，等注音就绪后（ready 信号）再刷新，
        # 避免"先纯文本后注音"的闪烁。首帧尚无内容时不阻塞，先把已就绪的内容画出来。
        if blocked and getattr(self, "_last_html", None) is not None:
            target = sb.minimum() if self.flow_direction == "down" else sb.maximum()
            if sb.value() != target:
                sb.setValue(target)
            return

        has_content = any(record_groups)
        self.text.setTextBackdrop(
            has_content and self.background_mode == BACKGROUND_MODE_TEXT,
            self.bg_alpha,
            max(5, round(self.font_size * 0.26)),
        )

        if not has_content:
            fs = self.font_size
            # 占位文案垂直居中：QTextEdit 默认顶对齐，按可用高度补一段上边距。
            # 为了让 first block margin 正常生效，我们 prepend 一个 1px 的 spacer block。
            line_h = fs * 1.2
            usable_h = max(0, self.text.height())
            top = max(0, int((usable_h - line_h) / 2) - 6)
            html = (
                f'<div style="font-size:1px; line-height:1px;">&nbsp;</div>'
                f'<div style="color:{PLACEHOLDER_COLOR}; font-size:{fs}px; '
                f'font-family:{_font_stack_for_lang("", self.use_bundled_cjk_fonts)}; '
                f'text-align:center; margin-top:{top}px;">等待字幕…</div>'
            )
        else:
            # 只渲染最近的完整句子组，避免把一句切成残缺 token；排好流向后再敲定行距。
            ordered_groups = self._select_recent_groups(
                record_groups, max_lines, newest_first=self.flow_direction == "down")
            records = [rec for group in ordered_groups for rec in group]
            html = self._render_records(records)

        # 内容没变就不重建文档（避免无谓重排导致的闪烁），但仍跟随流向端点。
        if html == getattr(self, "_last_html", None):
            target = sb.minimum() if self.flow_direction == "down" else sb.maximum()
            if sb.value() != target:
                sb.setValue(target)
            return
        self._last_html = html

        # 关掉重绘再替换内容，确保只在最终端点状态画一次，避免闪烁。
        self.text.setUpdatesEnabled(False)
        try:
            self.text.setHtml(html)
            # 根据独立的悬浮窗流向，把最新字幕固定在顶部或底部。
            if self.flow_direction == "down":
                self.text.moveCursor(QTextCursor.Start)
                sb.setValue(sb.minimum())
            else:
                self.text.moveCursor(QTextCursor.End)
                sb.setValue(sb.maximum())
        finally:
            self.text.setUpdatesEnabled(True)

    def _select_recent_groups(self, groups, max_lines: int, newest_first=False):
        """挑出最近若干个完整句组（不超过 max_lines 行），并按流向排好序返回。"""
        selected = []
        line_count = 0
        for group in reversed(groups):
            group_len = len(group)
            if selected and line_count + group_len > max_lines:
                break
            selected.insert(0, group)
            line_count += group_len
        return list(reversed(selected)) if newest_first else selected

    def _build_line_groups(self, blocks):
        """产出按显示句子分组的「行记录」列表（不含 HTML）。

        每条记录 = {kind, tokens/pairs, lang, base_mb}；同一句的原文/译文贴紧
        （pair_mb），句与句之间留白（sent_mb）。display_mode 控制只显示原文 /
        只显示译文 / 两者。真正的 HTML 与行距在 _render 里按渲染顺序敲定
        （见 _render_records），因为行距是渲染相邻行之间的关系，需在排好流向之后算。
        """
        pair_mb = 0                          # 同句原文↔译文：贴紧
        sent_mb = max(5, int(self.font_size * 0.45))  # 句与句之间：留白

        show_orig = self.display_mode in ("both", "original")
        show_trans = self.display_mode in ("both", "translation")

        groups = []          # list[list[record]]
        blocked = False
        for block in blocks:
            for sentence in block["sentences"]:
                recs = []
                orig = sentence["original"] if show_orig else []
                trans = sentence["translation"] if show_trans else []
                # LLM 译文更新优先于 STT 内置译文；准确模式下 trans 为空，靠它补出译文行。
                override = self._sentence_translation_override(sentence) if show_trans else None
                has_trans = bool(trans) or override is not None
                if orig:
                    base_mb = pair_mb if has_trans else sent_mb
                    lang = sentence["original_lang"]
                    pairs = self._furigana_pairs_for(orig, lang) if self.furigana_enabled else None
                    if pairs is _FURIGANA_PENDING:
                        # 注音未就绪：不渲染纯文本，标记阻塞，本帧交由 _render 丢弃。
                        blocked = True
                    elif pairs:
                        recs.append({"kind": "furigana", "pairs": pairs, "tokens": orig,
                                     "lang": lang, "base_mb": base_mb})
                    else:
                        recs.append({"kind": "plain", "tokens": orig,
                                     "lang": lang, "base_mb": base_mb})
                if override is not None:
                    text, lang = override
                    recs.append({"kind": "plain",
                                 "tokens": [{"text": text, "is_final": True}],
                                 "lang": lang, "base_mb": sent_mb})
                elif trans:
                    recs.append({"kind": "plain", "tokens": trans,
                                 "lang": sentence["translation_lang"], "base_mb": sent_mb})
                if recs:
                    groups.append(recs)
        return groups, blocked

    def _render_records(self, records):
        """把渲染顺序排好的行记录生成 HTML，并按相邻关系敲定行距。

        开注音时假名行比普通文字行高出一个「假名带」。为不额外顶宽行距，凡「下方
        相邻行是假名行」，就把本行下边距收窄为 furigana_gap，让假名带落进原本的行间
        空白里；非假名处的间距保持不变，与不开注音时视觉一致。（Qt 不认负 margin，
        只能缩小上一行的下边距来实现。）
        """
        fs = self.font_size
        tag_fs = max(9, int(fs * 0.55))
        # 假名行上方目标间距（上一行底 → 本行假名顶），刻意小于 sent_mb。
        furigana_gap = max(2, int(fs * 0.12))

        parts = []
        for i, rec in enumerate(records):
            nxt = records[i + 1] if i + 1 < len(records) else None
            mb = rec["base_mb"]
            if self.furigana_enabled and nxt is not None and nxt["kind"] == "furigana":
                mb = min(mb, furigana_gap)
            if rec["kind"] == "furigana":
                html = self._furigana_line_html(
                    rec["pairs"], rec["tokens"], rec["lang"], fs, mb, tag_fs)
            else:
                html = self._line_html(rec["tokens"], rec["lang"], fs, mb, tag_fs)
            if html:
                parts.append(html)
        return "".join(parts)

    def _furigana_pairs_for(self, tokens, lang):
        """判断日语原文行是否走假名渲染，返回三态：

          * 注音对列表 [(表面形, 注音|None), ...] —— 已就绪且整行至少一处需注音；
          * None                —— 不适用（非日语/空行/已就绪但整行无需注音），走普通渲染；
          * _FURIGANA_PENDING   —— 注音仍在获取，本帧应阻塞（避免先纯文本后注音的闪烁）。
        """
        if _subtitle_font_lang(lang) != "ja":
            return None
        plain = "".join(tk.get("text") or "" for tk in tokens)
        if not plain.strip():
            return None
        pairs = self.furigana.get_pairs(plain)
        if pairs is None:
            # 未就绪：仍在取词就阻塞本帧；已放弃（无主窗口等）则回退纯文本。
            return _FURIGANA_PENDING if self.furigana.is_pending(plain) else None
        if not any(reading for _, reading in pairs):
            return None
        return pairs

    def _furigana_line_html(self, pairs, tokens, lang, fs, margin_bottom, tag_fs):
        """把已就绪的注音对列表渲染成一行假名 HTML。

        与网页版一致：整行 token 逐个画成「注音在上、正文在下」的内联图块；行内有
        非 final token 时整行按进行中着色。
        """
        non_final = any(not tk.get("is_final", True) for tk in tokens)
        imgs = []
        for surface, reading in pairs:
            spec = _encode_ruby_spec(
                fs, non_final, self.use_bundled_cjk_fonts, surface, reading)
            imgs.append(f'<img src="{_RUBY_SCHEME}{spec}">')
        tag_html = ""
        if lang:
            # 与网页版假名行一致：语言标识行内基线对齐（不再垂直居中）。
            spec = f"{tag_fs}-{_html_escape(str(lang)).upper()}"
            tag_html = f'<img src="{_TAG_SCHEME}{spec}">'
        font_stack = _font_stack_for_lang(lang, self.use_bundled_cjk_fonts)
        style = (f"margin:0 0 {margin_bottom}px 0; line-height:110%; "
                 f"font-size:{fs}px; color:{FINAL_COLOR}; font-family:{font_stack};")
        # 图块之间插入零宽空格，保证 QTextEdit 能在 token 边界换行。
        content = f'{tag_html}{"&#8203;".join(imgs)}'
        return f'<div{_lang_attr(lang)} style="{style}">{content}</div>'

    def _line_html(self, tokens, lang, fs, margin_bottom, tag_fs):
        spans = []
        for tk in tokens:
            text = tk.get("text") or ""
            if not text:
                continue
            safe = _html_escape(text).replace("\n", "<br>")
            if tk.get("is_final", True):
                spans.append(safe)
            else:
                spans.append(f'<span style="color:{NONFINAL_COLOR};">{safe}</span>')
        if not spans:
            return ""
        tag_html = ""
        if lang:
            # 圆角矩形语言标识：交给 SubtitleTextEdit.loadResource 画成内联图片。
            spec = f"{tag_fs}-{_html_escape(str(lang)).upper()}"
            tag_html = f'<img src="{_TAG_SCHEME}{spec}" style="vertical-align:middle;">'
        font_stack = _font_stack_for_lang(lang, self.use_bundled_cjk_fonts)
        style = (f"margin:0 0 {margin_bottom}px 0; line-height:110%; "
                 f"font-size:{fs}px; color:{FINAL_COLOR}; font-family:{font_stack};")
        content = f'{tag_html}{"".join(spans)}'
        return f'<div{_lang_attr(lang)} style="{style}">{content}</div>'

    # ------------------------------------------------------------- 关闭 ----
    def _save_geometry(self):
        self.settings.setValue("geometry", self.saveGeometry())

    def _notify_closed_to_server(self):
        self._post("/overlay", {"action": "close"})

    def closeEvent(self, event):
        self._save_geometry()
        event.ignore()
        self.hide()
        threading.Thread(target=self._notify_closed_to_server, daemon=True).start()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Realtime subtitle native overlay")
    parser.add_argument("--url", required=True, help="服务器地址，如 http://127.0.0.1:8000")
    parser.add_argument("--hidden", action="store_true", help="Start the window hidden")
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    app = QApplication.instance() or QApplication(sys.argv)
    load_bundled_fonts()
    app.setStyle(InstantToolTipStyle(app.style()))
    app.setQuitOnLastWindowClosed(False)
    win = OverlayWindow(args.url)
    if not args.hidden:
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
