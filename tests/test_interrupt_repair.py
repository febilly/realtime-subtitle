from types import SimpleNamespace

import interrupt_repair
from interrupt_repair import InterruptRepairHistory, clone_tokens_for_merge

CONFIG = SimpleNamespace(
    SONIOX_INTERRUPT_REPAIR_ENABLED=True,
    SONIOX_INTERRUPT_MAX_DURATION_MS=800,
    SONIOX_INTERRUPT_RESUME_GAP_MS=1500,
    SONIOX_INTERRUPT_FILLER_WHITELIST_ENABLED=True,
    SONIOX_INTERRUPT_FILLER_WHITELIST="uh,um,嗯,うん",
)


def _is_internal(token):
    text = token.get("text") if isinstance(token, dict) else token
    return text in ("<end>", "<fin>")


def _join(tokens):
    return "".join(t.get("text", "") for t in tokens).strip()


def _src(text, start, end, speaker="1"):
    return {"text": text, "speaker": speaker, "start_ms": start, "end_ms": end}


def _history_a1_then_filler(filler_text="うん。", filler_start=1100, filler_end=1400):
    history = InterruptRepairHistory()
    history.record("1", "s-1", [_src("今日は", 0, 500), _src("。", 500, 1000)], [],
                   is_internal_token=_is_internal)
    history.record("2", "s-2", [_src(filler_text, filler_start, filler_end, speaker="2")], [],
                   is_internal_token=_is_internal)
    return history


def _find(history, speaker="1", start_ms=1600, config=CONFIG):
    return history.find_merge_pair(speaker, start_ms, config=config, join_text=_join)


def test_resuming_after_short_filler_matches_previous_sentence():
    match = _find(_history_a1_then_filler())
    assert match is not None
    previous, interrupt = match
    assert (previous.sentence_id, interrupt.sentence_id) == ("s-1", "s-2")


def test_no_match_for_other_speaker_long_interrupt_non_filler_or_late_resume():
    assert _find(_history_a1_then_filler(), speaker="3") is None
    assert _find(_history_a1_then_filler(filler_end=3000)) is None  # interrupt too long
    assert _find(_history_a1_then_filler(filler_text="違うよ。")) is None
    assert _find(_history_a1_then_filler(), start_ms=1400 + 1501) is None  # resume gap too large


def test_filler_whitelist_can_be_disabled_and_repair_switched_off():
    lenient = SimpleNamespace(**{**vars(CONFIG), "SONIOX_INTERRUPT_FILLER_WHITELIST_ENABLED": False})
    assert _find(_history_a1_then_filler(filler_text="違うよ。"), config=lenient) is not None
    off = SimpleNamespace(**{**vars(CONFIG), "SONIOX_INTERRUPT_REPAIR_ENABLED": False})
    assert _find(_history_a1_then_filler(), config=off) is None


def test_retracted_sentence_is_skipped_and_reported():
    history = _history_a1_then_filler()
    previous, _ = _find(history)
    history.retract(previous)
    assert history.is_retracted("s-1")
    assert not history.is_retracted("s-2")
    assert _find(history) is None


def test_record_replaces_same_id_and_keeps_bounded_history():
    history = InterruptRepairHistory()
    for index in range(interrupt_repair.HISTORY_MAX + 3):
        history.record("1", f"s-{index}", [_src("x", index, index + 1)], [], is_internal_token=_is_internal)
    history.record("1", f"s-{interrupt_repair.HISTORY_MAX + 2}", [_src("y", 99, 100)], [],
                   is_internal_token=_is_internal)
    ids = [s.sentence_id for s in history.snapshots]
    assert len(ids) == interrupt_repair.HISTORY_MAX
    assert len(set(ids)) == len(ids)
    assert history.snapshots[-1].original_tokens[0]["text"] == "y"


def test_record_ignores_sentences_without_timestamps():
    history = InterruptRepairHistory()
    history.record("1", "s-1", [{"text": "no time", "speaker": "1"}], [], is_internal_token=_is_internal)
    assert history.snapshots == []


def test_clone_strips_final_source_punctuation_only():
    tokens = [
        {"text": " 今日は", "translation_status": "original"},
        {"text": "。", "translation_status": "original"},
        {"text": "Today.", "translation_status": "translation"},
    ]
    cloned = clone_tokens_for_merge(tokens, "s-9")
    assert [t["text"] for t in cloned] == [" 今日は", "", "Today."]
    assert {t["llm_sentence_id"] for t in cloned} == {"s-9"}
    assert tokens[1]["text"] == "。"  # originals untouched


def test_clone_strips_punctuation_before_trailing_whitespace():
    tokens = [{"text": "今日は。 ", "translation_status": "original"}]
    assert clone_tokens_for_merge(tokens, "s-9")[0]["text"] == "今日は "
