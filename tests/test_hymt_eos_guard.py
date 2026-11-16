"""Hy-MT2: when the source grew, don't let the model stop right after copying the previous translation."""
import numpy as np

from realtime_subtitle.streaming_translation.api.hymt2 import HyMT2API
from tests.test_spec_decode_draft_bias import STOP, VOCAB, _decoder


def _greedy_sampler(decoder):
    def sample(_ctx, idx=-1):
        ptr = decoder._m.llama_get_logits_ith(None, idx)
        return int(np.ctypeslib.as_array(ptr, shape=(VOCAB,)).argmax())

    return type("Greedy", (), {"sample": staticmethod(sample)})()


def _run(preferred, rivals, *, margin, draft=None, penalty=4.0, hold=(1, 2)):
    decoder, state = _decoder(preferred, rivals, margin)
    state["pos0"] = 100
    return decoder.generate(
        _greedy_sampler(decoder), 100, stop_ids={STOP}, max_tokens=50, draft=draft,
        stop_penalty=penalty, avoid_stop=lambda emitted: emitted == list(hold),
    )


def test_stop_after_copying_the_previous_output_is_pushed_down():
    # After [1, 2] the model wants to stop; token 3 is a close second.
    result = _run([1, 2], {2: 3}, margin=1.0)
    assert result.tokens[:3] == [1, 2, 3]
    assert result.n_stop_vetoed == 1


def test_veto_also_applies_at_the_end_of_an_accepted_draft():
    result = _run([1, 2], {2: 3}, margin=1.0, draft=[1, 2])
    assert result.tokens[:3] == [1, 2, 3]
    assert result.n_stop_vetoed == 1


def test_a_confident_stop_still_wins():
    result = _run([1, 2], {2: 3}, margin=6.0)
    assert result.tokens == [1, 2]
    assert result.n_stop_vetoed == 0


def test_stop_elsewhere_is_untouched():
    # Output differs from the previous one, so its stop is normal.
    result = _run([1, 4], {2: 3}, margin=1.0)
    assert result.tokens == [1, 4]
    assert result.n_stop_vetoed == 0


def test_zero_penalty_disables_the_guard():
    result = _run([1, 2], {2: 3}, margin=1.0, penalty=0.0)
    assert result.tokens == [1, 2]


class _RecordingEngine:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append(kwargs)
        return "Same.", [11, 12]


def _api():
    api = HyMT2API(backend="api")
    api.backend = "local"
    api._local_engine = _RecordingEngine()
    return api


def test_grown_source_passes_the_previous_translation_as_hold(monkeypatch):
    import realtime_subtitle.core.config as config

    monkeypatch.setattr(config, "LOCAL_MT_EOS_PENALTY", 4.0, raising=False)
    monkeypatch.setattr(config, "LOCAL_MT_FRESH_RETRY", False, raising=False)
    api = _api()
    api.translate("I went", "en", "zh", is_partial=True)
    api.translate("I went to the store", "en", "zh", is_partial=True)
    first, second = api._local_engine.calls
    assert "eos_penalty" not in first
    assert second["eos_penalty"] == 4.0
    assert second["hold_text"] == "Same."
    assert second["hold_tokens"] == [11, 12]


def test_same_or_shorter_source_has_no_guard():
    api = _api()
    api.translate("I went to the store", "en", "zh", is_partial=True)
    api.translate("I went to the store", "en", "zh", is_partial=True)
    api.translate("I went to the stor", "en", "zh", is_partial=True)
    assert all("eos_penalty" not in call for call in api._local_engine.calls)


# ── engine-level: a pushed-down stop that makes the model rewrite the whole previous translation ──
import threading
from types import SimpleNamespace

from realtime_subtitle.streaming_translation.api.hymt2 import HyMT2LocalEngine

_STOP_ID = 0


class _CharModel:
    n_vocab = 0x10000

    def tokenize(self, text, add_special=False, parse_special=True):
        return [ord(c) for c in text]

    def detokenize(self, tokens):
        return "".join(chr(t) for t in tokens)


class _ScriptedSpec:
    """Emits ``script`` (``None`` = the model wants to stop), honouring avoid_stop and on_token."""

    def __init__(self, script):
        self.script = script

    def generate(self, sampler, pos, *, stop_ids, max_tokens, draft=None, on_token=None,
                 stop_penalty=0.0, avoid_stop=None):
        emitted, vetoed = [], 0
        for item in self.script:
            if item is None:
                if stop_penalty > 0 and avoid_stop is not None and avoid_stop(emitted):
                    vetoed += 1
                    continue
                break
            emitted.append(ord(item))
            if on_token is not None and on_token(emitted):
                break
        return SimpleNamespace(tokens=emitted, n_passes=1, n_accepted=0, n_drafted=0, n_stop_vetoed=vetoed)


def _engine(script):
    engine = HyMT2LocalEngine.__new__(HyMT2LocalEngine)
    engine.model = _CharModel()
    engine._spec = _ScriptedSpec(script)
    engine._engine_lock = threading.Lock()
    engine._cached_tokens = []
    engine._prefill = lambda tokens, cached: len(tokens)
    engine._LlamaSampler = lambda **_kwargs: object()
    engine.stop_ids = {_STOP_ID}
    engine._n_ctx = 2048
    return engine


def _script(text_with_stops):
    return [None if c == "|" else c for c in text_with_stops]


def test_pushed_stop_that_rewrites_the_previous_translation_falls_back_to_it():
    held = "明天好像会下雨。"
    engine = _engine(_script(held + "|" + held + held + "|"))
    out, toks = engine.generate("p", eos_penalty=1.0, hold_text=held, hold_tokens=[ord(c) for c in held])
    assert out == held
    assert toks == [ord(c) for c in held]


def test_pushed_stop_that_adds_new_content_is_kept():
    held = "明天好像会下雨。"
    engine = _engine(_script(held + "|我会通知大家。|"))
    out, _ = engine.generate("p", eos_penalty=1.0, hold_text=held, hold_tokens=[ord(c) for c in held])
    assert out == held + "我会通知大家。"


# ── API-level: retranslate without the previous version when the guarded decode still copied it ──
class _CopyThenFreshEngine:
    def __init__(self, copy, fresh):
        self.copy, self.fresh = copy, fresh
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs))
        if "Previous translation of the current source" in prompt:
            return self.copy, [1, 2]
        if len(self.prompts) == 1:
            return "我去了超市。", [1, 2]
        return self.fresh, [3, 4, 5]


def _fresh_api(monkeypatch, copy, fresh, enabled=True, min_add=2):
    import realtime_subtitle.core.config as config

    monkeypatch.setattr(config, "LOCAL_MT_EOS_PENALTY", 2.0, raising=False)
    monkeypatch.setattr(config, "LOCAL_MT_FRESH_RETRY", enabled, raising=False)
    monkeypatch.setattr(config, "LOCAL_MT_FRESH_MIN_ADD", min_add, raising=False)
    api = HyMT2API(backend="api")
    api.backend = "local"
    api._local_engine = _CopyThenFreshEngine(copy, fresh)
    return api


def test_copied_output_is_retranslated_without_the_previous_version(monkeypatch):
    api = _fresh_api(monkeypatch, "我去了超市。", "我去了超市，买了牛奶。")
    api.translate("I went to the store", "en", "zh", is_partial=True)
    out = api.translate("I went to the store and bought milk", "en", "zh", is_partial=True)
    assert out == "我去了超市，买了牛奶。"
    fresh_prompt, fresh_kwargs = api._local_engine.prompts[-1]
    assert "Previous translation" not in fresh_prompt
    assert "eos_penalty" not in fresh_kwargs


def test_fresh_retry_needs_enough_added_content(monkeypatch):
    api = _fresh_api(monkeypatch, "我去了超市。", "我去了超市，买了牛奶。")
    api.translate("I went to the store", "en", "zh", is_partial=True)
    out = api.translate("I went to the store um", "en", "zh", is_partial=True)
    assert out == "我去了超市。"
    assert len(api._local_engine.prompts) == 2


def test_fresh_retry_kept_only_when_it_says_more(monkeypatch):
    api = _fresh_api(monkeypatch, "我去了超市。", "我到了商店。")
    api.translate("I went to the store", "en", "zh", is_partial=True)
    out = api.translate("I went to the store and bought milk", "en", "zh", is_partial=True)
    assert out == "我去了超市。"


def test_fresh_retry_can_be_disabled(monkeypatch):
    api = _fresh_api(monkeypatch, "我去了超市。", "我去了超市，买了牛奶。", enabled=False)
    api.translate("I went to the store", "en", "zh", is_partial=True)
    out = api.translate("I went to the store and bought milk", "en", "zh", is_partial=True)
    assert out == "我去了超市。"
    assert len(api._local_engine.prompts) == 2
