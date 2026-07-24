import os
import re
import time
import types
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from overlay_window import (
    _RUBY_SCHEME,
    FuriganaService,
    OverlayWindow,
    SubtitleTextEdit,
    _decode_ruby_spec,
    _encode_ruby_spec,
    _make_ruby_pixmap,
)

APP = QApplication.instance() or QApplication([])


def _wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_ruby_spec_roundtrip_is_url_safe():
    spec = _encode_ruby_spec(24, True, False, "一生懸命", "いっしょうけんめい")
    # QTextDocument 会把 img src 当 URL 规范化，spec 不能包含需转义的字符
    assert all(c.isalnum() or c in "._-" for c in spec)
    assert _decode_ruby_spec(spec) == (24, True, False, "一生懸命", "いっしょうけんめい")
    empty = _encode_ruby_spec(20, False, True, "の", None)
    assert _decode_ruby_spec(empty) == (20, False, True, "の", "")


def test_ruby_pixmaps_share_uniform_height_within_a_line():
    with_ruby = _make_ruby_pixmap("思春", "ししゅん", 24, False, False)
    without_ruby = _make_ruby_pixmap("の", "", 24, False, False)
    # 同行内有无注音的 token 图块必须同高，保证共用基线
    assert with_ruby.height() == without_ruby.height()
    assert with_ruby.width() > without_ruby.width()


def _ink_segments(pixmap):
    """扫描每行是否有不透明像素，返回 [(top, bottom), ...] 的墨迹竖直区段。"""
    from PySide6.QtGui import qAlpha
    img = pixmap.toImage()
    w, h = img.width(), img.height()
    segs = []
    run = None
    for y in range(h):
        ink = any(qAlpha(img.pixel(x, y)) > 40 for x in range(w))
        if ink and run is None:
            run = y
        elif not ink and run is not None:
            segs.append((run, y - 1))
            run = None
    if run is not None:
        segs.append((run, h - 1))
    return segs


def test_furigana_sits_tight_against_the_base_text():
    # 注音块底与正文块顶之间只应留极小间隙（贴住），不再有字体内部行距的大空隙。
    fs = 24
    segs = _ink_segments(_make_ruby_pixmap("漢字", "かんじ", fs, False, False))
    assert len(segs) == 2                    # 上=注音，下=正文
    gap = segs[1][0] - segs[0][1] - 1
    # 贴住即可：间隙远小于旧布局按 ascent 定位时的 ~8–11px（阈值给字体后端差异留余量）
    assert 0 <= gap <= fs * 0.3, f"furigana-to-base gap too large: {gap}px"


def test_subtitle_text_edit_serves_ruby_pixmaps():
    edit = SubtitleTextEdit()
    spec = _encode_ruby_spec(24, False, False, "思春", "ししゅん")
    pixmap = edit._ruby_pixmap(spec)
    assert not pixmap.isNull()
    assert pixmap.width() > 1 and pixmap.height() > 1
    assert edit._ruby_pixmap(spec) is pixmap  # 缓存命中

    fallback = edit._ruby_pixmap("not-a-valid-spec")
    assert fallback.width() == 1 and fallback.height() == 1


def test_zero_width_space_does_not_poison_backdrop_bounds():
    edit = SubtitleTextEdit()
    edit.resize(400, 160)
    spec1 = _encode_ruby_spec(24, False, False, "思春", "ししゅん")
    spec2 = _encode_ruby_spec(24, False, False, "期", "き")
    edit.setHtml(
        '<div style="margin:0; line-height:110%; font-size:24px;">'
        f'<img src="{_RUBY_SCHEME}{spec1}">&#8203;<img src="{_RUBY_SCHEME}{spec2}">'
        "</div>")
    edit.setTextBackdrop(True, 128, 6)
    edit.show()
    APP.processEvents()

    rects = edit._backdrop_rects()
    assert len(rects) == 1
    # ​ 的 tightBoundingRect 是 (100000,100000) 哨兵值，若未剔除，
    # 背板会被平移到视口外
    assert 0 <= rects[0].top() < 160
    pixmap_h = edit._ruby_pixmap(spec1).height()
    assert rects[0].height() >= pixmap_h


def test_service_returns_none_when_disabled():
    service = FuriganaService(lambda text: {"ready": True, "pairs": [["猫", "ねこ"]]})
    assert service.get_pairs("猫") is None          # 未启用
    assert not service.is_enabled()


def test_service_fetches_pairs_and_caches_them():
    calls = []

    def fetch(text):
        calls.append(text)
        return {"ready": True, "pairs": [["思春", "ししゅん"], ["期", "き"], ["な", None]]}

    service = FuriganaService(fetch)
    service.set_enabled(True)

    assert service.get_pairs("思春期な") is None       # 首次：排队，返回 None
    assert _wait_for(lambda: service.get_pairs("思春期な") is not None)

    pairs = service.get_pairs("思春期な")
    assert pairs == [("思春", "ししゅん"), ("期", "き"), ("な", None)]
    # 缓存命中后不再重复请求
    before = len(calls)
    service.get_pairs("思春期な")
    assert len(calls) == before


def test_service_retries_while_main_window_dictionary_loads():
    state = {"ready_after": 2, "n": 0}

    def fetch(text):
        state["n"] += 1
        if state["n"] >= state["ready_after"]:
            return {"ready": True, "pairs": [["犬", "いぬ"]]}
        return {"ready": False, "pairs": []}

    service = FuriganaService(fetch)
    service._NOT_READY_INTERVAL = 0    # 测试里不真的 sleep
    service.set_enabled(True)

    service.get_pairs("犬")
    assert _wait_for(lambda: service.get_pairs("犬") is not None)
    assert service.get_pairs("犬") == [("犬", "いぬ")]
    assert state["n"] >= state["ready_after"]


def test_service_gives_up_without_a_main_window():
    def fetch(text):
        return None                       # 没有主窗口 / 请求失败

    service = FuriganaService(fetch)
    service.set_enabled(True)
    service.get_pairs("猫")
    # 短暂等待后仍拿不到（降级为纯文本），且不会崩
    time.sleep(0.1)
    APP.processEvents()
    assert service.get_pairs("猫") is None


def _render_records_helper(font_size, furigana_enabled, records):
    """在不构造整个悬浮窗控件的前提下驱动 _render_records（只用到几个属性 + 纯方法）。"""
    fake = types.SimpleNamespace(
        font_size=font_size,
        furigana_enabled=furigana_enabled,
        use_bundled_cjk_fonts=False,
    )
    fake._line_html = OverlayWindow._line_html.__get__(fake)
    fake._furigana_line_html = OverlayWindow._furigana_line_html.__get__(fake)
    return OverlayWindow._render_records.__get__(fake)(records)


def _margins(html):
    return [int(m) for m in re.findall(r"margin:0 0 (\d+)px", html)]


def test_line_gap_narrows_when_the_next_line_is_furigana():
    fs = 24
    sent_mb = max(5, int(fs * 0.45))       # 10
    furigana_gap = max(2, int(fs * 0.12))  # 2
    records = [
        {"kind": "plain", "tokens": [{"text": "汉字", "is_final": True}],
         "lang": "zh", "base_mb": sent_mb},
        {"kind": "furigana", "pairs": [("漢", "かん")],
         "tokens": [{"text": "漢", "is_final": True}], "lang": "ja", "base_mb": 0},
    ]
    html = _render_records_helper(fs, True, records)
    margins = _margins(html)
    # 上一行（其下方是假名行）的下边距被收窄，且明显小于句间距 sent_mb
    assert margins[0] == furigana_gap
    assert margins[0] < sent_mb


def test_line_gap_unchanged_when_furigana_disabled():
    fs = 24
    sent_mb = max(5, int(fs * 0.45))
    records = [
        {"kind": "plain", "tokens": [{"text": "汉字", "is_final": True}],
         "lang": "zh", "base_mb": sent_mb},
        {"kind": "plain", "tokens": [{"text": "テスト", "is_final": True}],
         "lang": "ja", "base_mb": 0},
    ]
    html = _render_records_helper(fs, False, records)
    assert _margins(html)[0] == sent_mb       # 不开注音：句间距保持原样


def test_is_pending_blocks_frame_until_resolved():
    gate = threading.Event()

    def fetch(text):
        gate.wait(2)
        return {"ready": True, "pairs": [["猫", "ねこ"]]}

    service = FuriganaService(fetch)
    service.set_enabled(True)

    assert service.get_pairs("猫") is None
    # 取词进行中：应阻塞本帧（不先渲染纯文本），避免闪烁
    assert service.is_pending("猫") is True

    gate.set()
    assert _wait_for(lambda: service.get_pairs("猫") is not None)
    assert service.is_pending("猫") is False       # 已就绪，不再阻塞


def test_failed_fetch_stops_blocking_so_line_renders_plain():
    service = FuriganaService(lambda text: None)   # 无主窗口 / 请求失败
    service.set_enabled(True)
    service.get_pairs("猫")
    # 取词失败后进入冷却：停止阻塞，调用方改渲染纯文本（不再无限期等待）
    assert _wait_for(lambda: not service.is_pending("猫"))
    assert service.get_pairs("猫") is None


def test_disabled_service_never_blocks():
    service = FuriganaService(lambda text: {"ready": True, "pairs": [["猫", "ねこ"]]})
    assert service.is_pending("猫") is False        # 未启用时从不阻塞


def test_disabling_clears_cache():
    service = FuriganaService(lambda text: {"ready": True, "pairs": [["猫", "ねこ"]]})
    service.set_enabled(True)
    service.get_pairs("猫")
    assert _wait_for(lambda: service.get_pairs("猫") is not None)

    service.set_enabled(False)
    assert service.get_pairs("猫") is None            # 关闭后清缓存 + 不再取词
