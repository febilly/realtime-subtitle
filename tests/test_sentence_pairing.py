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


def test_quiet_close_keeps_post_source_grace_for_late_tail():
    """Latest live regression (2026-07-11): translation pauses while the
    source keeps streaming. Closing the source must start a short grace
    window; otherwise the partial draft closes immediately and its tail is
    routed into the next sentence."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    source = {"text": "What is stopping most people from switching to Linux?"}
    pairer.route_source_token(source, "1")
    partial = {"text": "是什么在阻止大多数人", "translation_status": "translation"}
    pairer.route_translation_token(partial, "1")

    # The source remains active long enough that the old translation token is
    # already past QUIET_CLOSE_SECONDS when the source finally closes.
    clock["t"] += 2.0
    pairer.close_source("1")
    assert pairer.collect_completed() == []
    assert 0.29 <= pairer.seconds_until_next_deadline() <= 0.31

    # The final translated words arrive just after source close and must stay
    # on this sentence rather than opening/claiming the next entry.
    clock["t"] += 0.05
    tail = {"text": "切换到 Linux？", "translation_status": "translation"}
    pairer.route_translation_token(tail, "1")
    completed = pairer.collect_completed()
    assert [e.sentence_id for e in completed] == [source["llm_sentence_id"]]
    assert completed[0].translation_text() == "是什么在阻止大多数人切换到 Linux？"
    assert tail["llm_sentence_id"] == source["llm_sentence_id"]


def test_pairing_deadline_tracks_quiet_and_empty_waits():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": "quiet"}, "1")
    pairer.route_translation_token({"text": "静默"}, "1")
    pairer.close_source("1")
    assert 0.89 <= pairer.seconds_until_next_deadline() <= 0.91
    clock["t"] += 0.9
    assert pairer.seconds_until_next_deadline() == 0.0
    assert [e.translation_close_reason for e in pairer.collect_completed()] == ["quiet"]

    pairer.route_source_token({"text": "empty"}, "1")
    pairer.close_source("1")
    assert 2.99 <= pairer.seconds_until_next_deadline() <= 3.01


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


def test_punct_handoff_splits_zero_gap_burst_when_next_closed_source_waits():
    """Live 2026-07-11 regression (llm_20260711_210847 ids 19-21): two source
    sentences are already closed and waiting when their translations arrive as
    ONE zero-gap burst. The 300 ms resync gap can never fire inside the burst,
    so without a handoff the head swallows both translations and every later
    pair shifts by one. A later CLOSED source waiting is proof the burst spans
    sentences: hand off at the punctuation immediately."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    src1 = {"text": "すご！"}
    src2 = {"text": "リペアキットいっぱい使うな、これ。"}
    pairer.route_source_token(src1, "1")
    pairer.close_source("1")
    clock["t"] += 0.5  # closed in different batches: the session didn't merge
    pairer.route_source_token(src2, "1")
    pairer.close_source("1")

    trans1 = {"text": "太厉害了！", "translation_status": "translation"}
    pairer.route_translation_token(trans1, "1")
    clock["t"] += 0.05  # zero-gap burst: well below RESYNC_GAP_SECONDS
    trans2 = {"text": "这个要用一堆修复套件。", "translation_status": "translation"}
    pairer.route_translation_token(trans2, "1")

    completed = pairer.collect_completed()
    assert [e.source_text() for e in completed] == ["すご！", "リペアキットいっぱい使うな、これ。"]
    assert [e.translation_text() for e in completed] == ["太厉害了！", "这个要用一堆修复套件。"]
    assert completed[0].translation_close_reason == "punct_handoff"
    assert trans1["llm_sentence_id"] == src1["llm_sentence_id"]
    assert trans2["llm_sentence_id"] == src2["llm_sentence_id"]


def test_punct_handoff_sees_ender_behind_closing_quote():
    """Live 2026-07-12 regression (llm_20260712_081125 ids 16-20): the head's
    translation ends 「…吗？"」 — the sentence ender hides behind a closing
    quote. A bare last-char check missed it, so the next sentence's "不。"
    glued on and every later pair shifted (one entry even timed out empty and
    needed revival)."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    src1 = {"text": 'In English, you can say, "Can I buy some ice cream?"'}
    src2 = {"text": "No."}
    pairer.route_source_token(src1, "1")
    pairer.close_source("1")
    clock["t"] += 0.4
    pairer.route_source_token(src2, "1")
    pairer.close_source("1")

    trans1 = {"text": '在英语里，你可以说："我可以买点冰淇淋吗？"', "translation_status": "translation"}
    pairer.route_translation_token(trans1, "1")
    clock["t"] += 0.05  # zero-gap burst
    trans2 = {"text": "不。", "translation_status": "translation"}
    pairer.route_translation_token(trans2, "1")

    completed = pairer.collect_completed()
    assert [e.translation_text() for e in completed] == [
        '在英语里，你可以说："我可以买点冰淇淋吗？"',
        "不。",
    ]
    assert completed[0].translation_close_reason == "punct_handoff"
    assert trans2["llm_sentence_id"] == src2["llm_sentence_id"]


def test_resync_gap_sees_ender_behind_closing_quote():
    """Same root cause, resync flavor (llm_20260712_081125 ids 4-5): the next
    sentence's translation starts >= RESYNC_GAP after a quote-ended head while
    that next source is still open — it must not glue onto the head."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": '"We all know how to say no, right?"'}, "1")
    pairer.close_source("1")
    pairer.route_translation_token(
        {"text": '"我们都知道怎么说不，对吧？"', "translation_status": "translation"}, "1"
    )

    pairer.route_source_token({"text": "Well, yeah, you can use いいえ."}, "1")

    clock["t"] += 0.5  # a real gap: the next sentence's translation begins
    tail = {"text": "嗯，是的，你可以用いいえ。", "translation_status": "translation"}
    pairer.route_translation_token(tail, "1")
    pairer.close_source("1")

    completed = pairer.collect_completed()
    assert [e.translation_text() for e in completed] == [
        '"我们都知道怎么说不，对吧？"',
        "嗯，是的，你可以用いいえ。",
    ]
    assert completed[0].translation_close_reason == "resync_gap"


def test_punct_handoff_needs_a_closed_later_source():
    """The anti-split guard stays in force while the next sentence's source is
    still OPEN: a multi-sentence translation of one source ("因为A。所以B。")
    must keep gluing when nothing later is provably waiting."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    pairer.route_source_token({"text": "AだからBだ。"}, "1")
    pairer.close_source("1")
    pairer.route_source_token({"text": "次の"}, "1")  # next source still open

    pairer.route_translation_token({"text": "因为A。"}, "1")
    clock["t"] += 0.05
    trans_tail = {"text": "所以B。"}
    pairer.route_translation_token(trans_tail, "1")

    completed = pairer.collect_completed()
    assert [e.translation_text() for e in completed] == ["因为A。所以B。"]


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


def test_late_translation_revives_timed_out_sentence():
    """User-reported bug (2026-07-10): an incomplete fragment gets <end> but
    Soniox only emits its translation seconds later, after someone else's
    complete sentence forces a commit. The fragment times out empty; the late
    translation must revive it under the ORIGINAL sentence id — not open a
    source-less orphan entry whose fresh id makes the frontend's gray line
    vanish once the speaker's next source tokens claim it."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    frag = {"text": "鉄の供給も"}
    pairer.route_source_token(frag, "1")
    pairer.close_source("1", reason="endpoint")

    clock["t"] += 3.5  # MAX_WAIT elapses while another speaker talks
    timed_out = pairer.collect_completed()
    assert [e.translation_close_reason for e in timed_out] == ["timeout_empty"]

    clock["t"] += 2.0  # late translation finally streams in
    late = {"text": "铁的供应也"}
    pairer.route_translation_token(late, "1")
    assert late["llm_sentence_id"] == frag["llm_sentence_id"]

    clock["t"] += 1.0  # quiet close (no ending punctuation)
    completed = pairer.collect_completed()
    assert len(completed) == 1
    assert completed[0].sentence_id == frag["llm_sentence_id"]
    assert completed[0].translation_text() == "铁的供应也"
    assert completed[0].translation_was_revived

    # The speaker's next sentence is unaffected.
    pairer.route_source_token({"text": "次の話。"}, "1")
    pairer.close_source("1")
    own = {"text": "下一个话题。"}
    pairer.route_translation_token(own, "1")
    completed = pairer.collect_completed()
    assert [e.translation_text() for e in completed] == ["下一个话题。"]


def test_revival_window_expires():
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)
    pairer.route_source_token({"text": "こんにちは"}, "1")
    pairer.close_source("1")
    clock["t"] += 3.5
    assert pairer.collect_completed()[0].translation_close_reason == "timeout_empty"

    clock["t"] += 10.0  # beyond REVIVE_WINDOW_SECONDS
    orphan = {"text": "你好"}
    entry = pairer.route_translation_token(orphan, "1")
    assert not entry.translation_was_revived
    assert not entry.source_tokens


def test_revival_yields_to_awaiting_sentence():
    """A newer sentence already awaiting its translation keeps FIFO priority:
    the arriving chunk goes to it, not to the timed-out ghost."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)
    pairer.route_source_token({"text": "古い文"}, "1")
    pairer.close_source("1")
    clock["t"] += 3.5
    assert pairer.collect_completed()[0].translation_close_reason == "timeout_empty"

    newer = {"text": "新しい文。"}
    pairer.route_source_token(newer, "1")
    pairer.close_source("1")
    trans = {"text": "新句子。"}
    pairer.route_translation_token(trans, "1")
    assert trans["llm_sentence_id"] == newer["llm_sentence_id"]


def test_revival_precedes_newer_open_source_without_translation():
    """Live llm_20260711_214104 ids 104-105: the old short sentence times
    out, a newer long source starts but remains open, then the old short
    translation arrives. FIFO must revive the old sentence instead of closing
    the open new sentence with the wrong translation."""
    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    old = {"text": "おー。"}
    pairer.route_source_token(old, "1")
    pairer.close_source("1")
    clock["t"] += 3.1
    assert pairer.collect_completed()[0].translation_close_reason == "timeout_empty"

    newer = {"text": "これさ、ここがごちゃってなるのめっちゃ嫌だから、"}
    pairer.route_source_token(newer, "1")  # still open

    clock["t"] += 1.4
    late_old = {"text": "哦。", "translation_status": "translation"}
    pairer.route_translation_token(late_old, "1")
    assert late_old["llm_sentence_id"] == old["llm_sentence_id"]
    assert pairer.collect_completed()[0].source_text() == "おー。"

    own = {
        "text": "这个啊，这里会变得乱糟糟的我特别讨厌，",
        "translation_status": "translation",
    }
    pairer.route_translation_token(own, "1")
    assert own["llm_sentence_id"] == newer["llm_sentence_id"]


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


def test_seeded_translation_survives_until_fresh_chunk_arrives():
    """Interrupt-merge regression (llm_20260710_193303 run #34-#39): the
    restored entry is seeded with the retracted fragment's punct-ended
    translation. It must not punct-close (nor resync-close) on the seeded
    text alone — the continuation's translation, arriving later, belongs to
    the merged sentence, not the next one."""
    from sentence_pairing import PairedSentence

    clock = {"t": 100.0}
    pairer = _make_pairer(clock)

    # Merged entry seeded with the fragment's translation "真的。"
    pairer.restore_entry(
        PairedSentence(
            sentence_id="merged",
            speaker="1",
            source_tokens=[{"text": "マジで"}],
            translation_tokens=[{"text": "真的。"}],
        )
    )
    pairer.route_source_token({"text": " いや、ほんとに。"}, "1")
    pairer.close_source("1")

    # Batch end right after the close: seeded punct must NOT complete it.
    assert pairer.collect_completed() == []

    # Next sentence's source opens while the merged translation is late.
    nxt = {"text": "ほんとに、大人になってからいくらでもできるから。"}
    pairer.route_source_token(nxt, "1")
    pairer.close_source("1")

    # The continuation's translation arrives 0.5s later (beyond RESYNC_GAP):
    # it must land on the merged entry, not shift to the next sentence.
    clock["t"] += 0.5
    late = {"text": "不是，真的。"}
    pairer.route_translation_token(late, "1")
    assert late["llm_sentence_id"] == "merged"

    completed = pairer.collect_completed()
    assert [e.sentence_id for e in completed] == ["merged"]
    assert completed[0].translation_text() == "真的。不是，真的。"
    assert completed[0].translation_was_seeded

    # And the next sentence still pairs with its own translation.
    own = {"text": "真的，长大以后随时都能做。"}
    pairer.route_translation_token(own, "1")
    clock["t"] += 1.0
    completed = pairer.collect_completed()
    assert [e.translation_text() for e in completed] == ["真的，长大以后随时都能做。"]
    assert own["llm_sentence_id"] == completed[0].sentence_id


def test_seeded_translation_quiet_closes_when_no_fresh_chunk_comes():
    from sentence_pairing import PairedSentence

    clock = {"t": 100.0}
    pairer = _make_pairer(clock)
    pairer.restore_entry(
        PairedSentence(
            sentence_id="merged",
            speaker="1",
            source_tokens=[{"text": "そうね"}],
            translation_tokens=[{"text": "是啊。"}],
        )
    )
    pairer.route_source_token({"text": " それは思ってる。"}, "1")
    pairer.close_source("1")

    assert pairer.collect_completed() == []
    clock["t"] += 1.0  # QUIET_CLOSE_SECONDS elapsed with no fresh translation
    completed = pairer.collect_completed()
    assert [e.translation_close_reason for e in completed] == ["quiet"]
    assert completed[0].translation_text() == "是啊。"


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


def test_session_translation_tail_after_source_close_stays_with_sentence(monkeypatch):
    """Replays the 2026-07-11 failure shape: an old partial translation is
    already quiet when the source closes, then its tail arrives 50 ms later.
    The refine call must wait for and retain that tail."""
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
                "translation_status": "original", "language": "en",
                "source_language": "en"}

    def trans(text):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": "translation", "language": "zh",
                "source_language": "en"}

    all_final = []
    sent = 0

    def feed(tokens, advance):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    feed([src("What is stopping most people"), trans("是什么在阻止大多数人")], 0.1)
    feed([src(" from switching to Linux?")], 2.0)
    assert calls == []
    feed([trans("切换到 Linux？")], 0.05)

    assert calls == [
        ("What is stopping most people from switching to Linux?",
         "是什么在阻止大多数人切换到 Linux？")
    ]


def test_session_translation_punctuation_closes_matching_open_source(monkeypatch):
    """2026-07-11 follow-up regression: translation punctuation split the
    frontend display while the semantic source stayed open. The next English
    sentence inherited the same id, so refine triggered late and its Chinese
    result appeared under both display blocks."""
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    updates = []

    async def broadcast(data):
        updates.append(data)

    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", _run_immediately)
    monkeypatch.setattr(module, "is_llm_refine_available", lambda: True)

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

    def tok(text, status, language):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": status, "language": language,
                "source_language": "en"}

    all_final = []
    sent = 0

    def feed(tokens):
        nonlocal sent
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    # Soniox has not finalized source punctuation yet, but its translation
    # already provides an authoritative display/semantic sentence boundary.
    feed([tok("Every day people argue online", "original", "en")])
    feed([tok("每天都有人在网上争论。", "translation", "zh")])
    assert calls == [
        ("Every day people argue online", "每天都有人在网上争论。")
    ]

    feed([tok("But what do we think?", "original", "en")])
    feed([tok("但我们怎么看？", "translation", "zh")])
    assert calls == [
        ("Every day people argue online", "每天都有人在网上争论。"),
        ("But what do we think?", "但我们怎么看？"),
    ]

    original_ids = {}
    for update in updates:
        for token in update.get("final_tokens", []):
            if token.get("translation_status") == "original":
                original_ids[token.get("text")] = token.get("llm_sentence_id")
    assert original_ids["Every day people argue online"]
    assert original_ids["But what do we think?"]
    assert (
        original_ids["Every day people argue online"]
        != original_ids["But what do we think?"]
    )


def test_session_ellipsis_does_not_split_sentence(monkeypatch):
    """Replay of llm_20260711_194734 ids 40-42. An ellipsis is a trail-off,
    not a sentence end: the fragment before it must stay in the SAME paired
    sentence as its continuation, so the pairing never needs to guess where
    the translation splits (the guessing variants each misfired live)."""
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

    def tok(text, status, language):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": status, "language": language,
                "source_language": "ja"}

    all_final = []
    sent = 0

    def feed(tokens, advance=0.0):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    # The ellipsis fragment and its continuation arrive in one response; the
    # "..." must not open a sentence boundary between them.
    feed([
        tok("で、青いやつもっといっぱい作って、ここに...", "original", "ja"),
        tok("こいつぶち込んで。", "original", "ja"),
    ])
    # Both translation parts arrive in one fast burst (< 300 ms apart) and
    # belong to that single merged sentence.
    feed([
        tok("然后把蓝色的那个多做一些，放到这里......", "translation", "zh"),
        tok("把这个塞进去。", "translation", "zh"),
    ], advance=0.05)
    # The next pair proves the FIFO remains aligned after the ellipsis.
    feed([tok("どこ働かせると。", "original", "ja")], advance=0.5)
    feed([tok("要让它去哪里工作呢。", "translation", "zh")], advance=0.05)
    feed([], advance=2.0)  # settle

    assert calls == [
        ("で、青いやつもっといっぱい作って、ここに...こいつぶち込んで。",
         "然后把蓝色的那个多做一些，放到这里......把这个塞进去。"),
        ("どこ働かせると。", "要让它去哪里工作呢。"),
    ]


def test_session_same_batch_sentences_merge_into_one_pair(monkeypatch):
    """End-to-end replay of llm_20260711_210847 ids 19-21: Soniox finalizes
    TWO complete sentences in one response, then streams both translations as
    one glued zero-gap burst. Splitting that burst correctly is guesswork, so
    the session must instead keep the two sentences in ONE paired entry — the
    refine LLM then receives both sources with both translations, and the
    following sentence still pairs with its own translation."""
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

    def tok(text, status, language):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": status, "language": language,
                "source_language": "ja"}

    all_final = []
    sent = 0

    def feed(tokens, advance=0.0):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    # Two complete sentences finalized in one response batch.
    feed([
        tok("すご！", "original", "ja"),
        tok("リペアキットいっぱい使うな、これ。", "original", "ja"),
    ])
    # Their translations arrive as one glued zero-gap burst.
    feed([
        tok("太厉害了！", "translation", "zh"),
        tok("这个要用一堆修复套件。", "translation", "zh"),
    ], advance=0.06)
    # The next sentence must still pair with its OWN translation (live it got
    # the previous sentence's leftover and then timed out empty).
    feed([tok("で、急いで今度上の退治に行かないといけない。", "original", "ja")], advance=0.5)
    feed([tok("然后得赶紧去把上面的清掉。", "translation", "zh")], advance=0.06)
    feed([], advance=2.0)  # settle

    assert calls == [
        ("すご！リペアキットいっぱい使うな、これ。", "太厉害了！这个要用一堆修复套件。"),
        ("で、急いで今度上の退治に行かないといけない。", "然后得赶紧去把上面的清掉。"),
    ], calls


def test_session_mid_token_period_still_splits_sentences(monkeypatch):
    """Live 2026-07-12 (llm_20260712_083058, 准确 mode): soniox finalized
    '...in Japanese. It's' as ONE token, and the boundary scan — which only
    looks at token ends — never split, so two sentences dispatched as one.
    Final tokens are now pre-split at internal sentence boundaries."""
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
    session._llm_refine_mode = "translate"
    session._suppress_soniox_translation = True

    calls = []

    async def fake_translate(source, context_items, target_lang=None):
        calls.append(source)
        return {"status": "ok", "translation": "x"}

    session._perform_translate = fake_translate

    def src(text):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": "original", "language": "en",
                "source_language": "en"}

    all_final = []
    sent = 0

    def feed(tokens, advance=0.4):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    feed([src("Some of you might be thinking, well, we all know how to say"
              ' "no" in Japanese. It\'s')])
    feed([src(' "いいえ," right?')])
    feed([], advance=2.0)  # settle

    assert calls == [
        'Some of you might be thinking, well, we all know how to say "no" in Japanese.',
        'It\'s "いいえ," right?',
    ], calls


def test_session_quoted_passage_stays_one_pair(monkeypatch):
    """Live 2026-07-12 (llm_20260712_083348 ids 30-39, 混合 mode): the source
    split at the 。 inside 「だめ、だめ、だめ。それ、スズメバチ。」 while
    soniox translated the whole quotation as one block that arrived before the
    second source half closed — the head swallowed it and the pairing shifted
    for ten sentences. An ender inside an unclosed quote pair is no longer a
    boundary, so the quotation is ONE pair."""
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

    def tok(text, status, language):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": status, "language": language,
                "source_language": "ja"}

    all_final = []
    sent = 0

    def feed(tokens, advance=0.4):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    # The quotation streams as two tokens; the inner 。 must not split it.
    feed([tok("「だめ、だめ、だめ。", "original", "ja")])
    # The WHOLE quoted translation arrives before the second source half.
    feed([tok('"だめ、だめ、だめ。那是鹳。"', "translation", "zh")], advance=0.05)
    feed([tok("それ、スズメバチ。」", "original", "ja")], advance=0.1)
    # The next pair must stay aligned.
    feed([tok("「危ないから触っちゃだめ。」", "original", "ja")], advance=0.5)
    feed([tok('"很危险，别碰。"', "translation", "zh")], advance=0.05)
    feed([], advance=2.0)  # settle

    assert calls == [
        ("「だめ、だめ、だめ。それ、スズメバチ。」", '"だめ、だめ、だめ。那是鹳。"'),
        ("「危ないから触っちゃだめ。」", '"很危险，别碰。"'),
    ], calls


def test_session_late_translation_after_timeout_revives_fragment(monkeypatch):
    """User-reported bug: speaker A's incomplete fragment gets <end> with no
    translation; speaker B then says a full sentence; A's translation only
    arrives after that. The fragment times out empty, so the late translation
    must revive it — same sentence id on the tokens, one refine call pairing
    (fragment, late translation) — instead of opening a fresh id that makes
    the frontend's gray line vanish."""
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

    def tok(text, speaker, status, lang):
        return {"text": text, "is_final": True, "speaker": speaker,
                "translation_status": status, "language": lang,
                "source_language": "ja"}

    all_final = []
    sent = 0

    def feed(tokens, advance=0.4):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    # Speaker A: incomplete fragment, endpoint, no translation.
    feed([tok("鉄の供給も", "1", "original", "ja"),
          {"text": "<end>", "is_final": True, "speaker": "1",
           "translation_status": "original"}])
    # A's entry times out empty while B speaks.
    feed([], advance=3.5)
    # Speaker B: complete sentence with its translation.
    feed([tok("これで完成です。", "2", "original", "ja"),
          tok("这样就完成了。", "2", "translation", "zh")])
    # A's translation finally arrives, seconds late.
    feed([tok("铁的供应也。", "1", "translation", "zh")], advance=1.0)
    feed([], advance=2.0)  # settle

    assert calls == [
        ("これで完成です。", "这样就完成了。"),
        ("鉄の供給も", "铁的供应也。"),
    ], calls

    # The late translation tokens carry the FRAGMENT's sentence id, and the
    # revived refine_result targets that same id.
    frag_id = late_id = None
    for update in updates:
        for token in update.get("final_tokens", []):
            if token.get("text") == "鉄の供給も":
                frag_id = token.get("llm_sentence_id")
            if token.get("text") == "铁的供应也。":
                late_id = token.get("llm_sentence_id")
    assert frag_id and frag_id == late_id, (frag_id, late_id)
    refined_ids = [u["sentence_id"] for u in updates if u.get("type") == "refine_result"]
    assert frag_id in refined_ids


def test_session_late_short_translation_does_not_swap_with_open_next_sentence(monkeypatch):
    """End-to-end replay of llm_20260711_214104 ids 104-105."""
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

    def tok(text, status, language="ja"):
        return {"text": text, "is_final": True, "speaker": "1",
                "translation_status": status, "language": language,
                "source_language": "ja"}

    all_final = []
    sent = 0

    def feed(tokens, advance=0.0):
        nonlocal sent
        now["t"] += advance
        sent = session._process_soniox_response(
            {"tokens": tokens}, all_final, sent, object()
        )[0]

    feed([tok("おー。", "original")])
    feed([], advance=3.1)  # timeout_empty, still revivable
    feed([tok("これさ、ここがごちゃってなるのめっちゃ嫌だから、", "original")], advance=1.0)
    feed([tok("哦。", "translation", "zh")], advance=0.4)
    feed([tok("这个啊，这里会变得乱糟糟的我特别讨厌，", "translation", "zh")], advance=0.2)
    feed([tok("<end>", "original")], advance=0.1)
    feed([], advance=1.0)

    assert calls == [
        ("おー。", "哦。"),
        ("これさ、ここがごちゃってなるのめっちゃ嫌だから、",
         "这个啊，这里会变得乱糟糟的我特别讨厌，"),
    ]

    token_ids = {}
    for update in updates:
        for token in update.get("final_tokens", []):
            text = token.get("text")
            if text in {"おー。", "哦。", "これさ、ここがごちゃってなるのめっちゃ嫌だから、",
                        "这个啊，这里会变得乱糟糟的我特别讨厌，"}:
                token_ids[text] = token.get("llm_sentence_id")
    assert token_ids["おー。"] == token_ids["哦。"]
    assert (
        token_ids["これさ、ここがごちゃってなるのめっちゃ嫌だから、"]
        == token_ids["这个啊，这里会变得乱糟糟的我特别讨厌，"]
    )
    assert (
        token_ids["おー。"]
        != token_ids["これさ、ここがごちゃってなるのめっちゃ嫌だから、"]
    )
