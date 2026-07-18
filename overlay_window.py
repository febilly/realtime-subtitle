"""原生（PySide6）半透明字幕悬浮窗。

作为独立进程运行（与 pywebview 主窗口分离，避免两个 GUI 事件循环冲突）：

    python overlay_window.py --url http://127.0.0.1:PORT

冻结（PyInstaller）后由主程序通过 `--run-overlay` 重新拉起自身进入这里。

窗口特性：
  * 无边框、半透明黑底、白字、圆角
  * 任意位置鼠标拖动；贴近边缘鼠标缩放
  * 鼠标移入才在右下角显示一排常用按钮（字号 +/-、暂停/继续、关闭）
  * 通过 WebSocket(`/ws`) 接收字幕，样式与网页版基本一致
"""

import os
import sys
import json
import argparse
import asyncio
import threading
import urllib.request
from html import escape as _html_escape

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPoint, QRect, QRectF, QSettings, QSize, QEvent, QLocale
from PySide6.QtGui import (
    QCursor,
    QPainter,
    QColor,
    QBrush,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QPainterPath,
    QPen,
    QPixmap,
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
        "display_both": "当前：显示原文与译文 (点击切换为仅原文)",
        "display_original": "当前：仅显示原文 (点击切换为仅译文)",
        "display_translation": "当前：仅显示译文 (点击切换为显示全部)",
        "flow_up": "字幕向上流动 (点击改为向下流动)",
        "flow_down": "字幕向下流动 (点击改为向上流动)",
        "passthrough_off": "穿透模式：关闭 (开启后除按钮外鼠标均可穿透)",
        "passthrough_on": "穿透模式：开启 (关闭后鼠标无法穿透悬浮窗)",
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
        "display_both": "Current: original + translation (click for original only)",
        "display_original": "Current: original only (click for translation only)",
        "display_translation": "Current: translation only (click for both)",
        "flow_up": "Subtitles flow upward (click to flow downward)",
        "flow_down": "Subtitles flow downward (click to flow upward)",
        "passthrough_off": "Click-through mode: disabled (click to enable)",
        "passthrough_on": "Click-through mode: enabled (click to disable)",
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
        "display_both": "現在：原文＋訳文 (クリックで原文のみ表示)",
        "display_original": "現在：原文のみ (クリックで訳文のみ表示)",
        "display_translation": "現在：訳文のみ (クリックで両方表示)",
        "flow_up": "字幕は上方向に流れます (クリックで下方向に変更)",
        "flow_down": "字幕は下方向に流れます (クリックで上方向に変更)",
        "passthrough_off": "マウスクリック透過：無効 (クリックで有効化)",
        "passthrough_on": "マウスクリック透過：有効 (クリックで無効化)",
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
        "display_both": "현재: 원문+번역문 (클릭 시 원문만 표시)",
        "display_original": "현재: 원문만 표시 (클릭 시 번역문만 표시)",
        "display_translation": "현재: 번역문만 표시 (클릭 시 둘 다 표시)",
        "flow_up": "자막이 위로 흐릅니다 (클릭하여 아래로 변경)",
        "flow_down": "자막이 아래로 흐릅니다 (클릭하여 위로 변경)",
        "passthrough_off": "마우스 클릭 관통: 비활성화 (클릭 시 활성화)",
        "passthrough_on": "마우스 클릭 관통: 활성화 (클릭 시 비활성화)",
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
        "display_both": "Сейчас: оригинал + перевод (нажмите для оригинала)",
        "display_original": "Сейчас: только оригинал (нажмите для перевода)",
        "display_translation": "Сейчас: только перевод (нажмите для всего)",
        "flow_up": "Субтитры движутся вверх (нажмите, чтобы направить вниз)",
        "flow_down": "Субтитры движутся вниз (нажмите, чтобы направить вверх)",
        "passthrough_off": "Режим сквозного клика: отключен (нажмите для включения)",
        "passthrough_on": "Режим сквозного клика: включен (нажмите для отключения)",
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
        "display_both": "Actual: original + traducción (clic para solo original)",
        "display_original": "Actual: solo original (clic para solo traducción)",
        "display_translation": "Actual: solo traducción (clic para ambos)",
        "flow_up": "Los subtítulos fluyen hacia arriba (clic para invertir)",
        "flow_down": "Los subtítulos fluyen hacia abajo (clic para invertir)",
        "passthrough_off": "Modo de paso del ratón: desactivado (clic para activar)",
        "passthrough_on": "Modo de paso del ratón: activado (clic para desactivar)",
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
        "display_both": "Atual: original + tradução (clique para apenas original)",
        "display_original": "Atual: apenas original (clique para apenas tradução)",
        "display_translation": "Atual: apenas tradução (clique para ambos)",
        "flow_up": "As legendas fluem para cima (clique para inverter)",
        "flow_down": "As legendas fluem para baixo (clique para inverter)",
        "passthrough_off": "Modo de passagem do mouse: desativado (clique para ativar)",
        "passthrough_on": "Modo de passagem do mouse: ativado (clique para desativar)",
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
    """QTextEdit，但把 ``langtag:<fs>|<TEXT>`` 的图片资源动态画成圆角语言标识。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tag_cache = {}

    def loadResource(self, rtype, name):
        try:
            s = name.toString()
        except Exception:
            s = str(name)
        if rtype == QTextDocument.ImageResource and s.startswith(_TAG_SCHEME):
            return self._tag_pixmap(s[len(_TAG_SCHEME):])
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
        # 显示模式：both（原文+译文）/ original（仅原文）/ translation（仅译文）
        self.display_mode = str(self.settings.value("display_mode", "both"))
        self.flow_direction = str(self.settings.value("flow_direction", "up"))
        if self.flow_direction not in ("up", "down"):
            self.flow_direction = "up"
        self.is_paused = False
        self.use_bundled_cjk_fonts = False
        self._restart_in_flight = False
        self._passthrough = False
        self._click_through_on = False

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
        self.btn_display = self._make_button("O/T", "", self._cycle_display)
        self.btn_flow = self._make_button("", "", self._toggle_flow_direction,
                                          icon="arrow-up-from-line")
        self.btn_passthrough = self._make_button("", "", self._toggle_passthrough, icon="mouse-pointer-2")
        self.btn_restart = self._make_button("", tr("restart"), self._restart_recognition, icon="rotate-cw")
        self.btn_pause = self._make_button("", tr("pause"), self._toggle_pause, icon="pause")
        self.btn_close = self._make_button("", tr("close"), self.close, icon="x")
        for b in (self.btn_font_dec, self.btn_font_inc,
                  self.btn_alpha_dec, self.btn_alpha_inc,
                  self.btn_display, self.btn_flow, self.btn_passthrough,
                  self.btn_restart, self.btn_pause, self.btn_close):
            bar_layout.addWidget(b)
        # Create opacity effects for all other buttons
        self._other_buttons = (
            self.btn_font_dec, self.btn_font_inc,
            self.btn_alpha_dec, self.btn_alpha_inc,
            self.btn_display, self.btn_flow, self.btn_restart,
            self.btn_pause, self.btn_close
        )
        self._btn_opacity_effects = {}
        for btn in self._other_buttons:
            effect = QGraphicsOpacityEffect(btn)
            btn.setGraphicsEffect(effect)
            self._btn_opacity_effects[btn] = effect

        self._update_display_button()
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
        "  background: rgba(255,255,255,0.14);"
        "  border: none; border-radius: 6px;"
        "  font-size: 13px; font-weight: 600;"
        "}"
        "QPushButton:hover { background: rgba(255,255,255,0.30); }"
        "QPushButton:pressed { background: rgba(255,255,255,0.45); }"
    )
    # 激活态（如穿透模式开启）：蓝色高亮，和网页版 active 按钮一致。
    _BTN_QSS_ACTIVE = (
        "QPushButton {"
        "  color: #bfdbfe;"
        "  background: rgba(96,165,250,0.45);"
        "  border: none; border-radius: 6px;"
        "  font-size: 13px; font-weight: 600;"
        "}"
        "QPushButton:hover { background: rgba(96,165,250,0.60); }"
        "QPushButton:pressed { background: rgba(96,165,250,0.70); }"
    )

    def _make_button(self, text, tip, slot, icon=None):
        b = VectorButton(text, tip, slot, icon, self.button_bar)
        b.setStyleSheet(self._BTN_QSS)
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
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(0, 0, 0, self.bg_alpha)))  # 黑色 + 可调半透明
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), BG_RADIUS, BG_RADIUS)

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

    def _inc_alpha(self):
        self._step_alpha(+1)

    def _dec_alpha(self):
        self._step_alpha(-1)

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
        self.btn_passthrough.setStyleSheet(
            self._BTN_QSS_ACTIVE if self._passthrough else self._BTN_QSS)
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
        line_groups = self._build_line_groups(blocks)
        lines = [line for group in line_groups for line in group]

        if not lines:
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
            # 只渲染最近的完整句子组，避免把一句切成残缺 token。
            html = "".join(self._select_recent_lines(
                line_groups,
                max_lines,
                newest_first=self.flow_direction == "down",
            ))

        sb = self.text.verticalScrollBar()

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

    def _select_recent_lines(self, line_groups, max_lines: int, newest_first=False):
        selected = []
        line_count = 0
        for group in reversed(line_groups):
            group_len = len(group)
            if selected and line_count + group_len > max_lines:
                break
            selected.insert(0, group)
            line_count += group_len
        ordered = reversed(selected) if newest_first else selected
        return [line for group in ordered for line in group]

    def _build_line_groups(self, blocks):
        """产出按显示句子分组的 HTML <div> 列表。

        同一句的原文/译文贴紧（pair_mb），句与句之间留白（sent_mb），从而让一句的
        原文+译文在视觉上成组。display_mode 控制只显示原文 / 只显示译文 / 两者都显示。
        """
        fs = self.font_size
        tag_fs = max(9, int(fs * 0.55))
        pair_mb = 0                          # 同句原文↔译文：贴紧
        sent_mb = max(5, int(fs * 0.45))     # 句与句之间：留白

        show_orig = self.display_mode in ("both", "original")
        show_trans = self.display_mode in ("both", "translation")

        groups = []
        for block in blocks:
            for sentence in block["sentences"]:
                group = []
                orig = sentence["original"] if show_orig else []
                trans = sentence["translation"] if show_trans else []
                # LLM 译文更新优先于 STT 内置译文；准确模式下 trans 为空，靠它补出译文行。
                override = self._sentence_translation_override(sentence) if show_trans else None
                has_trans = bool(trans) or override is not None
                if orig:
                    mb = pair_mb if has_trans else sent_mb
                    group.append(self._line_html(
                        orig, sentence["original_lang"], fs, mb, tag_fs))
                if override is not None:
                    text, lang = override
                    group.append(self._line_html(
                        [{"text": text, "is_final": True}], lang, fs, sent_mb, tag_fs))
                elif trans:
                    group.append(self._line_html(
                        trans, sentence["translation_lang"], fs, sent_mb, tag_fs))
                group = [line for line in group if line]
                if group:
                    groups.append(group)
        return groups

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
        return f'<div{_lang_attr(lang)} style="{style}">{tag_html}{"".join(spans)}</div>'

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
