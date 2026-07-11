from unittest.mock import MagicMock

import pytest

import text_processor


@pytest.fixture(autouse=True)
def reset_text_processor(monkeypatch):
    monkeypatch.setattr(text_processor.config, "ENABLE_ARABIC_RESHAPER", True, raising=False)
    text_processor._TEXT_POST_PROCESSING_DEGRADATION_WARNINGS.clear()


@pytest.mark.parametrize(
    ("text", "arabic", "hebrew"),
    [
        ("hello", False, False),
        ("مرحبا", True, False),
        ("שלום", False, True),
        ("\ufefb", False, False),
        ("\ufb2a", False, True),
    ],
)
def test_rtl_script_detection(text, arabic, hebrew):
    assert text_processor._contains_arabic_reshapable_text(text) is arabic
    assert text_processor._contains_hebrew_text(text) is hebrew
    assert text_processor._contains_rtl_reorderable_text(text) is (arabic or hebrew)


def test_rtl_isolate_wrapping_is_idempotent():
    wrapped = text_processor.wrap_rtl_isolate("שלום")
    assert wrapped == f"{text_processor.RTL_RLI}שלום{text_processor.RTL_PDI}"
    assert text_processor.is_rtl_isolate_wrapped(wrapped) is True
    assert text_processor.wrap_rtl_isolate(wrapped) == wrapped
    assert text_processor.wrap_rtl_isolate("") == ""


@pytest.mark.parametrize("text", ["", "   ", "\n\r\n"])
def test_wrap_text_at_word_boundaries_ignores_empty_content(text):
    assert text_processor._wrap_text_at_word_boundaries(text, 10) == []


def test_wrap_text_at_word_boundaries_handles_newlines_and_long_words():
    assert text_processor._wrap_text_at_word_boundaries(
        "one two\r\nabcdefghijk short", 5
    ) == ["one", "two", "abcde", "fghij", "k", "short"]


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("anything", 0, ""),
        ("short", 10, "short"),
        ("one two three", 7, "one two"),
        ("abcdefgh", 5, "abcde"),
    ],
)
def test_limit_line_at_word_boundary(text, limit, expected):
    assert text_processor._limit_line_at_word_boundary(text, limit) == expected


def test_process_rtl_line_uses_reshaper_then_bidi(monkeypatch):
    reshaper = MagicMock()
    reshaper.reshape.return_value = "reshaped"
    bidi = MagicMock(return_value="display")
    monkeypatch.setattr(text_processor, "_arabic_reshaper", reshaper)
    monkeypatch.setattr(text_processor, "_bidi_get_display", bidi)

    result = text_processor._process_rtl_line("مرحبا")

    assert result == f"{text_processor.RTL_RLI}display{text_processor.RTL_PDI}"
    reshaper.reshape.assert_called_once_with("مرحبا")
    bidi.assert_called_once_with("reshaped")


def test_process_hebrew_skips_arabic_reshaper(monkeypatch):
    reshaper = MagicMock()
    bidi = MagicMock(return_value="display")
    monkeypatch.setattr(text_processor, "_arabic_reshaper", reshaper)
    monkeypatch.setattr(text_processor, "_bidi_get_display", bidi)

    assert text_processor._process_rtl_line("שלום") == (
        f"{text_processor.RTL_RLI}display{text_processor.RTL_PDI}"
    )
    reshaper.reshape.assert_not_called()
    bidi.assert_called_once_with("שלום")


def test_missing_reshaper_warns_once_and_falls_back(monkeypatch, capsys):
    monkeypatch.setattr(text_processor, "_arabic_reshaper", None)
    monkeypatch.setattr(text_processor, "_bidi_get_display", MagicMock())

    first = text_processor._process_rtl_line("مرحبا")
    second = text_processor._process_rtl_line("مرحبا")

    assert first == second == text_processor.wrap_rtl_isolate("مرحبا")
    assert capsys.readouterr().out.count("arabic-reshaper") == 1


def test_reshaper_and_bidi_failures_fall_back(monkeypatch):
    reshaper = MagicMock()
    reshaper.reshape.side_effect = ValueError("bad shape")
    monkeypatch.setattr(text_processor, "_arabic_reshaper", reshaper)
    monkeypatch.setattr(text_processor, "_bidi_get_display", MagicMock())
    assert text_processor._process_rtl_line("مرحبا") == text_processor.wrap_rtl_isolate("مرحبا")

    reshaper.reshape.side_effect = None
    reshaper.reshape.return_value = "reshaped"
    bidi = MagicMock(side_effect=ValueError("bad bidi"))
    monkeypatch.setattr(text_processor, "_bidi_get_display", bidi)
    assert text_processor._process_rtl_line("مرحبا") == text_processor.wrap_rtl_isolate("reshaped")


def test_build_rtl_lines_caps_line_count(monkeypatch):
    monkeypatch.setattr(text_processor, "RTL_OSC_LINE_MAX_CHARS", 5)
    monkeypatch.setattr(text_processor, "RTL_OSC_MAX_LINES", 2)
    monkeypatch.setattr(text_processor, "_process_rtl_line", lambda line: line)
    assert text_processor._build_rtl_reordered_lines("one two three four") == "one\ntwo"


def test_build_rtl_lines_respects_total_character_budget(monkeypatch):
    monkeypatch.setattr(text_processor, "RTL_OSC_LINE_MAX_CHARS", 10)
    monkeypatch.setattr(text_processor, "RTL_OSC_MAX_LINES", 4)
    monkeypatch.setattr(text_processor, "_process_rtl_line", lambda line: line)
    assert text_processor._build_rtl_reordered_lines("hello world again", max_chars=12) == (
        "hello\nworld"
    )


def test_apply_reshaper_bypasses_disabled_non_rtl_and_wrapped_text(monkeypatch):
    monkeypatch.setattr(text_processor.config, "ENABLE_ARABIC_RESHAPER", False, raising=False)
    assert text_processor.apply_arabic_reshaper_if_needed("مرحبا") == "مرحبا"

    monkeypatch.setattr(text_processor.config, "ENABLE_ARABIC_RESHAPER", True, raising=False)
    assert text_processor.apply_arabic_reshaper_if_needed("plain") == "plain"
    wrapped = text_processor.wrap_rtl_isolate("שלום")
    assert text_processor.apply_arabic_reshaper_if_needed(wrapped) == wrapped


def test_apply_reshaper_passes_max_chars_to_builder(monkeypatch):
    builder = MagicMock(return_value="processed")
    monkeypatch.setattr(text_processor, "_build_rtl_reordered_lines", builder)
    assert text_processor.apply_arabic_reshaper_if_needed(
        "مرحبا", language="ar", max_chars=20
    ) == "processed"
    builder.assert_called_once_with("مرحبا", max_chars=20)
