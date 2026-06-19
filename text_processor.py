"""OSC text post-processing helpers.

The web UI can render RTL text with CSS, but VRChat's chatbox often needs the
text reshaped/reordered before it is sent over OSC.
"""
from typing import Optional

import config

RTL_RLI = "\u2067"
RTL_PDI = "\u2069"
RTL_OSC_LINE_MAX_CHARS = 30
RTL_OSC_MAX_LINE_BREAKS = 3
RTL_OSC_MAX_LINES = RTL_OSC_MAX_LINE_BREAKS + 1

try:
    import arabic_reshaper as _arabic_reshaper
except Exception:
    _arabic_reshaper = None

try:
    from bidi.algorithm import get_display as _bidi_get_display
except Exception:
    _bidi_get_display = None

_TEXT_POST_PROCESSING_DEGRADATION_WARNINGS = set()


def _warn_text_post_processing_degraded_once(key: str, message: str) -> None:
    if key in _TEXT_POST_PROCESSING_DEGRADATION_WARNINGS:
        return
    _TEXT_POST_PROCESSING_DEGRADATION_WARNINGS.add(key)
    print(f"[TextPost] {message}")


def _contains_arabic_reshapable_text(text: str) -> bool:
    """Detect Arabic-script source characters, excluding presentation forms."""
    return any(
        "\u0600" <= ch <= "\u06ff"
        or "\u0750" <= ch <= "\u077f"
        or "\u0870" <= ch <= "\u089f"
        or "\u08a0" <= ch <= "\u08ff"
        for ch in text
    )


def _contains_hebrew_text(text: str) -> bool:
    return any(
        "\u0590" <= ch <= "\u05ff"
        or "\ufb1d" <= ch <= "\ufb4f"
        for ch in text
    )


def _contains_rtl_reorderable_text(text: str) -> bool:
    return _contains_arabic_reshapable_text(text) or _contains_hebrew_text(text)


def is_rtl_isolate_wrapped(text: str) -> bool:
    return bool(text) and text.startswith(RTL_RLI) and text.endswith(RTL_PDI)


def wrap_rtl_isolate(text: str) -> str:
    if not text or is_rtl_isolate_wrapped(text):
        return text
    return f"{RTL_RLI}{text}{RTL_PDI}"


def _wrap_text_at_word_boundaries(text: str, max_chars: int) -> list[str]:
    if not text or max_chars <= 0:
        return []

    lines: list[str] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for source_line in normalized.split("\n"):
        words = source_line.split()
        current = ""

        for word in words:
            if len(word) > max_chars:
                if current:
                    lines.append(current)
                    current = ""
                for start in range(0, len(word), max_chars):
                    lines.append(word[start:start + max_chars])
                continue

            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chars:
                current = f"{current} {word}"
            else:
                lines.append(current)
                current = word

        if current:
            lines.append(current)

    return lines


def _limit_line_at_word_boundary(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    words = text.split()
    current = ""
    for word in words:
        if len(word) > max_chars:
            return current or word[:max_chars]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current = f"{current} {word}"
        else:
            break
    return current


def _line_needs_rtl_processing(line: str) -> bool:
    return _contains_rtl_reorderable_text(line)


def _process_rtl_line(line: str) -> str:
    if not _line_needs_rtl_processing(line):
        return line

    display_source = line
    if _contains_arabic_reshapable_text(line):
        if _arabic_reshaper is None:
            _warn_text_post_processing_degraded_once(
                "arabic_reshaper_missing",
                "阿拉伯文显示重排降级：arabic-reshaper 不可用，仅添加方向隔离控制符。",
            )
            return wrap_rtl_isolate(line)
        try:
            display_source = str(_arabic_reshaper.reshape(line))
        except Exception as exc:
            _warn_text_post_processing_degraded_once(
                "arabic_reshaper_failed",
                f"阿拉伯文显示重排降级：处理失败（{exc!r}），仅添加方向隔离控制符。",
            )
            return wrap_rtl_isolate(line)

    if _bidi_get_display is None:
        _warn_text_post_processing_degraded_once(
            "python_bidi_missing",
            "RTL 显示重排降级：python-bidi 不可用，混排英文/数字可能显示异常。",
        )
        return wrap_rtl_isolate(display_source)

    try:
        return wrap_rtl_isolate(str(_bidi_get_display(display_source)))
    except Exception as exc:
        _warn_text_post_processing_degraded_once(
            "rtl_bidi_failed",
            f"RTL 显示重排降级：bidi 处理失败（{exc!r}），仅添加方向隔离控制符。",
        )
        return wrap_rtl_isolate(display_source)


def _rtl_processed_line_for_budget(line: str, inner_budget: int) -> str:
    logical_line = _limit_line_at_word_boundary(line, inner_budget)
    while logical_line:
        processed_line = _process_rtl_line(logical_line)
        line_overhead = (
            len(RTL_RLI) + len(RTL_PDI)
            if is_rtl_isolate_wrapped(processed_line)
            else 0
        )
        if len(processed_line) <= inner_budget + line_overhead:
            return processed_line
        logical_line = logical_line[:-1].rstrip()
    return ""


def _build_rtl_reordered_lines(text: str, max_chars: Optional[int] = None) -> str:
    logical_lines = _wrap_text_at_word_boundaries(text, RTL_OSC_LINE_MAX_CHARS)
    if RTL_OSC_MAX_LINES > 0:
        logical_lines = logical_lines[:RTL_OSC_MAX_LINES]
    if not logical_lines:
        return ""

    processed_lines: list[str] = []
    current_length = 0

    for logical_line in logical_lines:
        processed_line = _process_rtl_line(logical_line)
        separator_length = 1 if processed_lines else 0

        if max_chars is None:
            processed_lines.append(processed_line)
            continue

        if current_length + separator_length + len(processed_line) <= max_chars:
            processed_lines.append(processed_line)
            current_length += separator_length + len(processed_line)
            continue

        remaining = max_chars - current_length - separator_length
        line_overhead = (
            len(RTL_RLI) + len(RTL_PDI)
            if _line_needs_rtl_processing(logical_line)
            else 0
        )
        inner_budget = min(RTL_OSC_LINE_MAX_CHARS, remaining - line_overhead)
        if inner_budget > 0:
            shortened_line = _rtl_processed_line_for_budget(logical_line, inner_budget)
            if shortened_line:
                processed_lines.append(shortened_line)
        break

    return "\n".join(processed_lines)


def apply_arabic_reshaper_if_needed(
    text: str,
    language: Optional[str] = None,
    max_chars: Optional[int] = None,
) -> str:
    del language
    if not text or not getattr(config, "ENABLE_ARABIC_RESHAPER", True):
        return text
    if is_rtl_isolate_wrapped(text):
        return text
    if not _contains_rtl_reorderable_text(text):
        return text
    return _build_rtl_reordered_lines(text, max_chars=max_chars)
