"""Pairing regression tests.

The live off-by-one (llm_log evidence, 2026-07-10): translation N streams in
after source N+1 already started, the old sentence-buffer code paired
(source N+1, translation N) and never resynced — 80% of refine calls got a
draft belonging to the previous sentence. These tests pin the FIFO routing
that replaced it, at both the unit (SentencePairer) and session level.
"""
import asyncio
import concurrent.futures
import sys
from unittest.mock import MagicMock

from test_soniox_session_response import _install_soniox_session_import_mocks


def _run_immediately(coro, _loop):
    asyncio.run(coro)
    future = concurrent.futures.Future()
    future.set_result(None)
    return future


# --------------------------------------------------------------------- unit


def _make_pairer(clock):
    from sentence_pairing import SentencePairer

    counter = {"n": 0}

    def make_id():
        counter["n"] += 1
        return f"s{counter['n']}"

    return SentencePairer(make_id, now=lambda: clock["t"])


def test_late_translation_routes_to_closed_previous_sentence():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    src1 = {"text": "そう、ここ。"}
    pairer.route_source_token(src1, "1")
    pairer.close_source("1")

    # Next sentence's source starts BEFORE the previous translation arrives.
    src2 = {"text": "うーん"}
    pairer.route_source_token(src2, "1")

    # The late translation must land on sentence 1, not the open sentence 2.
    trans1 = {"text": "对，就在这儿。", "translation_status": "translation"}
    pairer.route_translation_token(trans1, "1")

    assert trans1["llm_sentence_id"] == src1["llm_sentence_id"] == "s1"
    assert src2["llm_sentence_id"] == "s2"

    completed = pairer.collect_completed()
    assert [e.sentence_id for e in completed] == ["s1"]
    assert completed[0].source_text() == "そう、ここ。"
    assert completed[0].translation_text() == "对，就在这儿。"


def test_translation_split_across_batches_stays_on_one_sentence():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    src1 = {"text": "そう、ここ。"}
    pairer.route_source_token(src1, "1")
    pairer.close_source("1")

    pairer.route_translation_token({"text": "对，"}, "1")
    clock["t"] += 0.05  # fast chunk stream within one sentence
    pairer.route_translation_token({"text": "就在这儿。"}, "1")

    completed = pairer.collect_completed()
    assert len(completed) == 1
    assert completed[0].translation_text() == "对，就在这儿。"


def test_resync_gap_moves_next_translation_to_next_sentence():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": "来た？"}, "1")
    pairer.close_source("1")
    pairer.route_translation_token({"text": "来了？"}, "1")

    pairer.route_source_token({"text": "来た。"}, "1")
    pairer.close_source("1")

    # After a real gap the next translation belongs to the next sentence,
    # even though sentence 1 was never explicitly closed on the translation
    # side (its text already ends a sentence).
    clock["t"] += 0.5
    trans2 = {"text": "来了。"}
    pairer.route_translation_token(trans2, "1")

    completed = pairer.collect_completed()
    assert [e.translation_text() for e in completed] == ["来了？", "来了。"]
    assert trans2["llm_sentence_id"] == completed[1].sentence_id


def test_internal_punctuation_within_burst_does_not_split():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": "AだからBだ。"}, "1")
    pairer.close_source("1")

    pairer.route_translation_token({"text": "因为A。"}, "1")
    clock["t"] += 0.05  # same burst: below RESYNC_GAP_SECONDS
    pairer.route_translation_token({"text": "所以B。"}, "1")

    completed = pairer.collect_completed()
    assert len(completed) == 1
    assert completed[0].translation_text() == "因为A。所以B。"


def test_missing_translation_times_out_and_completes_empty():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": "こんにちは。"}, "1")
    pairer.close_source("1")

    assert pairer.collect_completed() == []
    clock["t"] += 3.5  # MAX_WAIT_SECONDS elapsed
    completed = pairer.collect_completed()
    assert len(completed) == 1
    assert completed[0].translation_text() == ""


def test_no_translation_expected_completes_at_source_close():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": "同语言。"}, "1")
    pairer.close_source("1", expects_translation=False)
    completed = pairer.collect_completed()
    assert len(completed) == 1


def test_close_reasons_and_timing_metrics_are_recorded():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    # punct close, with a measurable chunk gap and translation lag
    pairer.route_source_token({"text": "こんにちは。"}, "1")
    pairer.close_source("1")
    clock["t"] = 100.4
    pairer.route_translation_token({"text": "你"}, "1")
    clock["t"] = 100.55
    pairer.route_translation_token({"text": "好。"}, "1")
    clock["t"] = 100.6
    done = pairer.collect_completed()
    assert len(done) == 1
    entry = done[0]
    assert entry.translation_close_reason == "punct"
    assert abs(entry.first_translation_at - 100.4) < 1e-9
    assert abs(entry.max_chunk_gap - 0.15) < 1e-9
    assert entry.translation_closed_at >= entry.source_closed_at

    # quiet close
    pairer.route_source_token({"text": "うん"}, "1")
    pairer.close_source("1")
    clock["t"] = 101.0
    pairer.route_translation_token({"text": "嗯"}, "1")
    clock["t"] = 102.0
    done = pairer.collect_completed()
    assert [e.translation_close_reason for e in done] == ["quiet"]

    # empty timeout
    pairer.route_source_token({"text": "え"}, "1")
    pairer.close_source("1")
    clock["t"] = 106.0
    done = pairer.collect_completed()
    assert [e.translation_close_reason for e in done] == ["timeout_empty"]

    # no translation expected
    pairer.route_source_token({"text": "同语言。"}, "1")
    pairer.close_source("1", expects_translation=False)
    done = pairer.collect_completed()
    assert [e.translation_close_reason for e in done] == ["no_translation_expected"]


def test_per_speaker_queues_are_independent():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": "はい。"}, "1")
    pairer.close_source("1")
    pairer.route_source_token({"text": "そう。"}, "2")
    pairer.close_source("2")

    t1 = {"text": "好。"}
    t2 = {"text": "对。"}
    pairer.route_translation_token(t2, "2")
    pairer.route_translation_token(t1, "1")

    completed = {e.speaker: e for e in pairer.collect_completed()}
    assert completed["1"].translation_text() == "好。"
    assert completed["2"].translation_text() == "对。"


# ------------------------------------------------------------------ session


def test_session_fast_banter_never_shifts_refine_pairs(monkeypatch):
    """End-to-end reproduction of the live off-by-one: three rapid sentences
    whose translations each arrive interleaved with the NEXT sentence's
    source. Every refine call must receive its own sentence's translation."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

    now = {"t": 100.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: now["t"])

    session = module.SonioxSession(MagicMock(), broadcast)
    session.translation = "one_way"
    session.translation_target_lang = "zh"
    session.loop = object()
    session._segment_mode = "punctuation"
    session._llm_refine_mode = "refine"
    session._suppress_soniox_translation = False

    calls = []

    async def fake_refine(source, translation, context_items):
        calls.append((source, translation))
        return {"status": "ok", "no_change": True}

    session._perform_refine = fake_refine

    def src(text):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": "original", "language": "ja",
                "source_language": "ja"}

    def trans(text):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": "translation", "language": "zh",
                "source_language": "ja"}

    all_final = []
    sent = 0

    def feed(tokens, advance=0.4):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    # The shifted-steady-state pattern from the live log: each batch carries
    # the PREVIOUS sentence's translation alongside the next source.
    feed([src("ちょ、待って。")])
    feed([trans("等一下。"), src("くぐりの、こうだ！")])
    feed([trans("钻过去的，这样！"), src("来た？")])
    feed([trans("来了？")])
    feed([], advance=2.0)  # settle: quiet/timeout close whatever remains

    assert calls == [
        ("ちょ、待って。", "等一下。"),
        ("くぐりの、こうだ！", "钻过去的，这样！"),
        ("来た？", "来了？"),
    ], calls

    # And the broadcast refine_result frames carry matching sentence ids for
    # the tokens the frontend received.
    refined = [u for u in updates if u.get("type") == "refine_result"]
    assert len(refined) == 3
    token_ids = {}
    for update in updates:
        for token in update.get("final_tokens", []):
            if token.get("is_separator"):
                continue
            token_ids.setdefault(token.get("llm_sentence_id"), []).append(token.get("text"))
    for frame in refined:
        texts = token_ids.get(frame["sentence_id"]) or []
        assert frame["source"] in "".join(texts), (frame, token_ids)
