"""Sentence-boundary unit tests.

The ellipsis rules pin the 2026-07-11 decision that "…" / "..." never END a
sentence: splitting at a trail-off created extra pairing handoffs whose
misfires shifted source/translation alignment, and the fragment before an
ellipsis translates worse on its own.
"""
import sentence_segmentation as seg


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
