from local_inference.subtitle_segments import StickySubtitleSegments


def texts(segments):
    return [segment.text for segment in segments]


def test_internal_boundary_is_immediate_and_sticks_through_punctuation_flip():
    state = StickySubtitleSegments()
    first = state.update("First sentence. Second sentence")
    assert texts(first) == ["First sentence.", " Second sentence"]
    first_ids = [segment.id for segment in first]

    changed = state.update("First sentence, Second sentence")
    assert texts(changed) == ["First sentence,", " Second sentence"]
    assert [segment.id for segment in changed] == first_ids


def test_insertions_and_content_corrections_keep_rows_aligned():
    state = StickySubtitleSegments()
    first = state.update("Alpha. Beta. Gamma")
    changed = state.update("An Alpha. Better Beta. Gamma")

    assert "".join(texts(changed)) == "An Alpha. Better Beta. Gamma"
    assert texts(changed) == ["An Alpha.", " Better Beta.", " Gamma"]
    assert [segment.id for segment in changed] == [segment.id for segment in first]


def test_many_sentences_and_terminal_punctuation_do_not_make_empty_row():
    state = StickySubtitleSegments()
    segments = state.update("One. Two. Three. Four.")
    assert texts(segments) == ["One.", " Two.", " Three.", " Four."]
    assert all(segment.text for segment in segments)


def test_terminal_whitespace_stays_with_sentence_instead_of_a_blank_row():
    state = StickySubtitleSegments()
    assert texts(state.update("Hello. ")) == ["Hello. "]


def test_decimal_and_abbreviation_rules_are_shared_with_sentence_splitter():
    state = StickySubtitleSegments(max_spaced_chars=200)
    segments = state.update("Value 3.14 is e.g. useful. Next.")
    assert texts(segments) == ["Value 3.14 is e.g. useful.", " Next."]


def test_unpunctuated_text_uses_comma_or_whitespace_without_breaking_words():
    state = StickySubtitleSegments(max_spaced_chars=18)
    segments = state.update("one two three four five six seven eight")
    assert "".join(texts(segments)) == "one two three four five six seven eight"
    assert len(segments) > 1
    assert all(not (left.text[-1:].isalnum() and right.text[:1].isalnum()) for left, right in zip(segments, segments[1:]))

    cjk = StickySubtitleSegments(max_cjk_chars=8)
    segments = cjk.update("这是一个很长的没有句号但是有，自然逗号可以使用的句子")
    assert "".join(texts(segments)) == "这是一个很长的没有句号但是有，自然逗号可以使用的句子"
    assert len(segments) > 1


def test_substantial_rewrite_has_no_empty_duplicate_or_lost_text():
    state = StickySubtitleSegments(max_spaced_chars=12)
    state.update("old text. another old row. tail")
    rewritten = "Completely different words arrive without matching the earlier hypothesis at all"
    segments = state.update(rewritten)
    assert "".join(texts(segments)) == rewritten
    assert all(segment.text for segment in segments)
    assert len({segment.id for segment in segments}) == len(segments)


def test_consume_prefix_keeps_prefix_id_inside_a_row_and_update_is_stable():
    state = StickySubtitleSegments()
    original = state.update("Alpha. Beta remains")
    prefix_id = original[0].id
    old_suffix_id = original[1].id
    consumed = state.consume_prefix("Alpha. Be")

    assert texts(consumed) == ["Alpha.", " Be"]
    assert texts(state.segments) == ["ta remains"]
    assert consumed[0].id == prefix_id
    assert consumed[-1].id == old_suffix_id
    assert state.segments[0].id != old_suffix_id
    suffix_id = state.segments[0].id
    updated = state.update("ta remains revised")
    assert updated[0].id == suffix_id


def test_invalid_consume_prefix_is_rejected_and_repeated_utterances_are_not_deduplicated():
    state = StickySubtitleSegments()
    first = state.update("Same utterance.")
    try:
        state.consume_prefix("wrong")
    except ValueError:
        pass
    else:
        raise AssertionError("non-prefix must fail")

    state.reset()
    second = state.update("Same utterance.")
    assert first[0].id != second[0].id
