"""Sentence-boundary unit tests.

The ellipsis rules pin the 2026-07-11 decision that "…" / "..." never END a
sentence: splitting at a trail-off created extra pairing handoffs whose
misfires shifted source/translation alignment, and the fragment before an
ellipsis translates worse on its own.
"""
import json
from pathlib import Path

import pytest

import sentence_segmentation as seg


SHARED_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "segmentation-cases.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", SHARED_CASES, ids=lambda case: case["id"])
def test_shared_sentence_segmentation_cases(case):
    functions = {
        "is_sentence_ender_at": seg.is_sentence_ender_at,
        "text_ends_with_ellipsis": seg.text_ends_with_ellipsis,
        "text_has_unclosed_quote": seg.text_has_unclosed_quote,
        "text_continues_abbreviation": seg.text_continues_abbreviation,
        "token_text_continues_decimal": seg.token_text_continues_decimal,
        "token_text_starts_with_closing_quote": seg.token_text_starts_with_closing_quote,
        "text_ends_with_closing_quote_after_sentence_punctuation": (
            seg.text_ends_with_closing_quote_after_sentence_punctuation
        ),
        "has_sentence_ending_punctuation": seg.has_sentence_ending_punctuation,
        "is_sentence_ending_punctuation": seg.is_sentence_ending_punctuation,
        "split_text_at_sentence_boundaries": seg.split_text_at_sentence_boundaries,
        "split_into_sentence_lines": seg.split_into_sentence_lines,
    }
    actual = functions[case["function"]](*case["args"])
    assert actual == case["expected"]
    if case["function"] == "split_text_at_sentence_boundaries":
        assert "".join(actual) == case["args"][0]


def test_ellipsis_is_not_a_sentence_ender():
    assert not seg.is_sentence_ending_punctuation("なんか変な方に...")
    assert not seg.is_sentence_ending_punctuation("等等…")
    assert not seg.is_sentence_ending_punctuation("等等……")
    assert not seg.is_sentence_ending_punctuation("...")
    assert not seg.is_sentence_ending_punctuation("…」")
    # Real sentence enders keep working, including after an earlier ellipsis.
    assert seg.is_sentence_ending_punctuation("そうだね。")
    assert seg.is_sentence_ending_punctuation("すご！")
    assert seg.is_sentence_ending_punctuation("ここに...こいつぶち込んで。")


def test_single_period_still_ends_sentence():
    assert seg.is_sentence_ending_punctuation("Done.")
    assert seg.is_sentence_ender_at("Done.", 4)
    # ...but not when it is part of an ASCII ellipsis or a decimal.
    assert not seg.is_sentence_ender_at("に...", 3)
    assert not seg.is_sentence_ender_at("に...", 2)
    assert not seg.is_sentence_ender_at("3.14", 1)


def test_split_into_sentence_lines_keeps_ellipsis_runs_together():
    split = seg.split_into_sentence_lines
    assert split("ここに...こいつぶち込んで。") == ["ここに...こいつぶち込んで。"]
    assert split("等等…好") == ["等等…好"]
    assert split("甲。乙丙！丁") == ["甲。", "乙丙！", "丁"]  # real enders still split


def test_text_ends_with_ellipsis():
    assert seg.text_ends_with_ellipsis("方に...")
    assert seg.text_ends_with_ellipsis("方に..")
    assert seg.text_ends_with_ellipsis("等等…")
    assert seg.text_ends_with_ellipsis("等等…」")
    assert not seg.text_ends_with_ellipsis("そうだね。")
    assert not seg.text_ends_with_ellipsis("Done.")
    assert not seg.text_ends_with_ellipsis("")


def test_split_text_at_sentence_boundaries_is_exact():
    """The exact splitter never trims: pieces concatenate back unchanged.
    soniox can finalize a token whose text spans a boundary ("Japanese. It's",
    live llm_20260712_083058) — the session pre-splits such tokens and the
    display must not lose the original spacing."""
    cases = [
        "we all know how to say \"no\" in Japanese. It's",
        "甲。乙丙！丁",
        " leading space. kept ",
        "no boundary here",
        "「だめ、だめ、だめ。それ、スズメバチ。」",
        "",
    ]
    for text in cases:
        pieces = seg.split_text_at_sentence_boundaries(text)
        assert "".join(pieces) == text, (text, pieces)

    assert seg.split_text_at_sentence_boundaries(
        "we all know how to say \"no\" in Japanese. It's"
    ) == ["we all know how to say \"no\" in Japanese.", " It's"]


def test_no_split_inside_unclosed_quote_pairs():
    """Live 2026-07-12 (llm_20260712_083348 ids 30-31): 「だめ、だめ、だめ。
    それ、スズメバチ。」 was split at the inner 。 while soniox translated the
    whole quotation as one block — the pairing then shifted for ten sentences.
    An ender inside an unclosed 「」-style pair is not a boundary."""
    assert seg.split_text_at_sentence_boundaries(
        "「だめ、だめ、だめ。それ、スズメバチ。」"
    ) == ["「だめ、だめ、だめ。それ、スズメバチ。」"]
    assert seg.split_into_sentence_lines("「だめ。それ。」だめだって。") == [
        "「だめ。それ。」だめだって。"
    ]
    # Balanced pairs still split normally after the quote closes.
    assert seg.split_into_sentence_lines("「だめ。」と言った。次の話。") == [
        "「だめ。」と言った。",
        "次の話。",
    ]

    assert seg.text_has_unclosed_quote("「だめ、だめ、だめ。")
    assert not seg.text_has_unclosed_quote("「だめ。それ。」")
    assert not seg.text_has_unclosed_quote('say, "No thanks,"')  # ASCII: ambiguous, ignored


def test_token_ender_inside_open_quote_context_is_not_a_boundary():
    state = seg.PendingBoundaryState()

    def check(tokens, index, context_text):
        return state.is_token_sentence_ending(
            tokens,
            index,
            context_text=context_text,
            is_internal_token=lambda _t: False,
            source_as_output=False,
        )

    def tok(text):
        return {"text": text, "speaker": "1", "translation_status": "translation"}

    # The quote opened earlier in the sentence and has not closed yet.
    assert not check([tok("だめ。")], 0, "「だめ、だめ、だめ。")
    # Once the quote closes, the ender behind it is a boundary again.
    assert check([tok("スズメバチ。」")], 0, "「だめ、だめ、だめ。それ、スズメバチ。」")


def test_streamed_ellipsis_dots_do_not_close_mid_run():
    """A lone "." token whose buffered context already ends with "." is part
    of a streamed ASCII ellipsis, not a boundary — in both directions."""
    state = seg.PendingBoundaryState()

    def check(tokens, index, context_text):
        return state.is_token_sentence_ending(
            tokens,
            index,
            context_text=context_text,
            is_internal_token=lambda _t: False,
            source_as_output=False,
        )

    def trans(text):
        return {"text": text, "speaker": "1", "translation_status": "translation"}

    # Context already ends with dots: the arriving dot continues the ellipsis.
    assert not check([trans(".")], 0, "方に..")
    # The next token starts with a dot: this dot is the ellipsis's first dot.
    assert not check([trans("."), trans("..")], 0, "方に.")
    # An ordinary sentence-ending period is unaffected.
    assert check([trans("好。"), trans("下一句")], 0, "很好。")


def test_fragmented_word_suffix_period_uses_full_context_for_abbreviations():
    """Live 2026-07-13: Soniox finalized here. as "her" + "e."."""
    state = seg.PendingBoundaryState()

    def trans(text):
        return {"text": text, "speaker": "1", "translation_status": "translation"}

    assert state.is_token_sentence_ending(
        [trans("e."), trans(" So")],
        0,
        context_text="And there's a couple of candidate ideas here.",
        is_internal_token=lambda _t: False,
        source_as_output=False,
    )
    assert not state.is_token_sentence_ending(
        [trans("e."), trans("g.")],
        0,
        context_text="For e.",
        is_internal_token=lambda _t: False,
        source_as_output=False,
    )
