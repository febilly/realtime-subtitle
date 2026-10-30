import asyncio

import pytest

from local_inference.repetition_guard import (
    RETRY_REPEAT_PENALTY,
    has_repeated_block,
    is_runaway,
    source_is_repetitive,
    tail_repeats,
    text_units,
)
from remote_inference_service import server
from streaming_translation.api.hymt2 import HyMT2API


def test_units_drop_punctuation_and_split_cjk_from_words():
    assert text_units("Run, RUN! 跑吧ラン") == ["run", "run", "跑", "吧", "ラ", "ン"]


@pytest.mark.parametrize("text", [
    "Run, " * 60,
    "Look, I really, really, " + "I really, really, " * 10,
    "我真的" * 12,
    "Okay, okay, I'm here. " + "Hurry up! " * 10,
])
def test_long_tail_loops_are_runaways(text):
    assert is_runaway(text, "run了run了run了run了快跑")


@pytest.mark.parametrize("text", [
    "Hahahahahahahahahahahahaha!",          # one word unit
    "哈哈哈哈哈哈哈哈哈",                     # nine repeats only
    "Very, very, very good.",
    "Run, run, run, run, run, run, run, run, run!",  # nine
    "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12",
    "apples, pears, apples, pears, apples, pears, plums",
    "Run, " * 30 + "and then I stopped.",  # the loop is not at the tail
    "",
])
def test_ordinary_text_is_not_a_runaway(text):
    assert not is_runaway(text, "普通的一句话")


def test_repetitive_source_exempts_any_translation():
    source = "哈哈哈哈哈哈哈哈哈哈哈"
    assert source_is_repetitive(source)
    assert not is_runaway("Ha, " * 40, source)
    assert source_is_repetitive("好的好的好的好的好的，走吧")
    assert not source_is_repetitive("run了run了run了run了快跑")  # four only


def test_block_counts():
    assert tail_repeats(list("x" + "ab" * 10)) == 10
    assert tail_repeats(list("x" + "ab" * 12)) == 12
    assert tail_repeats(list("ab" * 9)) == 0
    assert has_repeated_block(list("xx" + "abc" * 5 + "yy"), 5)
    assert not has_repeated_block(list("abc" * 4 + "ab"), 5)


class _LoopingEngine:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate(self, prompt, *, max_tokens=160, draft=None, repeat_penalty=1.0, stop_on_runaway=False):
        self.calls.append({"draft": draft, "repeat_penalty": repeat_penalty, "stop": stop_on_runaway})
        return self.outputs.pop(0), [len(self.calls)]


def _local_api(engine):
    api = HyMT2API(backend="api")
    api.backend = "local"
    api._local_engine = engine
    return api


def test_local_runaway_retries_once_with_penalty():
    engine = _LoopingEngine(["Run, " * 30, "Run, run, run, run, hurry!"])
    api = _local_api(engine)
    assert api.translate("run了run了run了run了快跑", "zh", "en", is_partial=False) == "Run, run, run, run, hurry!"
    assert [c["repeat_penalty"] for c in engine.calls] == [1.0, RETRY_REPEAT_PENALTY]
    assert all(c["stop"] for c in engine.calls)


def test_local_second_runaway_is_used_as_is():
    engine = _LoopingEngine(["Run, " * 30, "Go, " * 30])
    api = _local_api(engine)
    assert api.translate("run了run了run了run了快跑", "zh", "en", is_partial=False) == "Go, " * 30
    assert len(engine.calls) == 2


def test_local_repetitive_source_neither_stops_nor_retries():
    engine = _LoopingEngine(["Ha, " * 30])
    api = _local_api(engine)
    api.translate("哈哈哈哈哈哈哈哈哈哈", "zh", "en", is_partial=False)
    assert engine.calls == [{"draft": None, "repeat_penalty": 1.0, "stop": False}]


def test_local_clean_output_runs_once():
    engine = _LoopingEngine(["Hello."])
    api = _local_api(engine)
    api.translate("你好", "zh", "en", is_partial=False)
    assert len(engine.calls) == 1


class _RemoteBackend:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.penalties = []

    async def complete(self, prompt, repeat_penalty=1.0, max_new_tokens=0):
        self.penalties.append(repeat_penalty)
        return self.outputs.pop(0)


def test_remote_runaway_retries_once_with_penalty():
    backend = _RemoteBackend(["Run, " * 30, "Run, hurry!"])
    assert asyncio.run(server._translate(backend, "prompt", "run了快跑")) == "Run, hurry!"
    assert backend.penalties == [1.0, RETRY_REPEAT_PENALTY]

    backend = _RemoteBackend(["Ha, " * 30])
    asyncio.run(server._translate(backend, "prompt", "哈哈哈哈哈哈哈哈哈哈"))
    assert backend.penalties == [1.0]
