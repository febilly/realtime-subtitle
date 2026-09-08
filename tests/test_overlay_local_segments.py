import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from overlay_window import SubtitleModel


def row(identifier, source, **changes):
    value = {
        "id": identifier,
        "source": source,
        "translation": "",
        "source_language": "en",
        "target_language": "zh",
        "order": 0,
        "revision": 1,
        "is_final": False,
        "requires_translation": True,
    }
    value.update(changes)
    return value


def sentences(model):
    return [
        sentence
        for block in model.build_blocks()
        for sentence in block["sentences"]
    ]


def source_texts(model):
    return ["".join(token["text"] for token in sentence["original"])
            for sentence in sentences(model)]


def test_local_source_is_visible_immediately_and_late_translation_revises_the_same_row():
    model = SubtitleModel()
    model.apply_update({"local_segments": [row("local-1", "Visible source")]})
    assert source_texts(model) == ["Visible source"]
    assert sentences(model)[0]["translation"] == []

    model.apply_update({"local_segments": [row(
        "local-1", "Visible source", revision=2, translation="可见译文",
    )]})
    rendered = sentences(model)
    assert source_texts(model) == ["Visible source"]
    assert [token["text"] for token in rendered[0]["translation"]] == ["可见译文"]

    # A stale source snapshot must not erase the newer translation revision.
    model.apply_update({"local_segments": [row("local-1", "stale", revision=1)]})
    assert source_texts(model) == ["Visible source"]
    assert [token["text"] for token in sentences(model)[0]["translation"]] == ["可见译文"]


def test_local_only_frame_preserves_ordinary_live_tail_and_mixes_in_arrival_order():
    model = SubtitleModel()
    model.apply_update({"non_final_tokens": [{
        "text": "Online draft", "is_final": False, "speaker": "1",
        "language": "en", "translation_status": "original",
    }]})
    model.apply_update({"local_segments": [row("local-1", "Local row", order=0)]})

    assert [token["text"] for token in model.non_final_tokens] == ["Online draft"]
    assert source_texts(model) == ["Online draft", "Local row"]

    model.apply_update({"final_tokens": [{
        "text": "Online later", "is_final": True, "speaker": "1",
        "language": "en", "translation_status": "original",
    }], "non_final_tokens": []})
    assert source_texts(model) == ["Local row", "Online later"]


def test_local_rows_keep_backend_split_order_and_tombstones_survive_late_updates():
    model = SubtitleModel()
    model.apply_update({"local_segments": [
        row("a", "First", order=0, is_final=True),
        row("c", "Third", order=2, is_final=True),
    ]})
    model.apply_update({"local_segments": [row("b", "Second", order=1, is_final=True)]})
    assert source_texts(model) == ["First", "Second", "Third"]

    model.apply_update({"local_removed_segment_ids": ["b"]})
    model.apply_update({"local_segments": [row("b", "must stay removed", order=1, revision=2)]})
    assert source_texts(model) == ["First", "Third"]

    model.apply_update({"local_reset": True})
    model.apply_update({"local_segments": [row("b", "new run", order=0)]})
    assert source_texts(model) == ["new run"]


def test_local_final_history_is_trimmed_without_allowing_late_translation_resurrection():
    model = SubtitleModel()
    model.apply_update({"local_segments": [
        row("old", "Old", order=0, is_final=True),
        row("keep-1", "Keep one", order=1, is_final=True),
        row("keep-2", "Keep two", order=2, is_final=True),
    ]})
    model.trim_final_tokens_to_recent_sentences(2)
    assert source_texts(model) == ["Keep one", "Keep two"]

    model.apply_update({"local_segments": [row(
        "old", "Old", order=0, revision=2, is_final=True, translation="late",
    )]})
    assert source_texts(model) == ["Keep one", "Keep two"]
