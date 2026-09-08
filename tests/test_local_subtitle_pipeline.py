"""Concurrency-facing contracts for the local subtitle presentation pipeline."""

from __future__ import annotations

import threading
import time

from local_inference.subtitle_pipeline import LocalSubtitlePipeline


def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "timed out waiting for asynchronous translation work"


class Recorder:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.finals: list[dict] = []
        self.errors: list[object] = []
        self.lock = threading.Lock()

    def publish(self, payload: dict) -> None:
        with self.lock:
            self.updates.append(payload)

    def on_final(self, payload: dict) -> None:
        with self.lock:
            self.finals.append(payload)

    def on_error(self, error: object) -> None:
        with self.lock:
            self.errors.append(error)

    def rows(self) -> list[dict]:
        with self.lock:
            return [
                row
                for update in self.updates
                for row in update.get("local_segments", [])
            ]


class FakeTranslatorFactory:
    """Thread-safe fake that can hold one selected translate call in flight."""

    def __init__(self, *, block_first: bool = False, blocked_sources: set[str] | None = None) -> None:
        self.block_first = block_first
        self.blocked_sources = blocked_sources or set()
        self.calls: list[dict] = []
        self.instances: list[FakeTranslator] = []
        self.lock = threading.Lock()
        self.blocked_started = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> "FakeTranslator":
        translator = FakeTranslator(self, len(self.instances))
        with self.lock:
            self.instances.append(translator)
        return translator

    def should_block(self, source: str) -> bool:
        with self.lock:
            return source in self.blocked_sources or (self.block_first and not self.calls)


class FakeTranslator:
    def __init__(self, factory: FakeTranslatorFactory, number: int) -> None:
        self.factory = factory
        self.number = number
        self.closed = False

    def translate(self, source: str, **kwargs) -> str:
        block = self.factory.should_block(source)
        with self.factory.lock:
            self.factory.calls.append({"source": source, "translator": self.number, **kwargs})
        if block:
            self.factory.blocked_started.set()
            # Tests always release this event; the timeout ensures a failed
            # assertion cannot strand the executor indefinitely.
            self.factory.release.wait(3.0)
        return f"MT:{source}"

    def close(self) -> None:
        self.closed = True


def make_pipeline(factory: FakeTranslatorFactory, recorder: Recorder) -> LocalSubtitlePipeline:
    return LocalSubtitlePipeline(factory, recorder.publish, recorder.on_final, recorder.on_error)


def test_source_rows_publish_before_a_blocked_translation_starts():
    factory = FakeTranslatorFactory(block_first=True)
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("First sentence. Second sentence", False, "en", "ja")
        assert factory.blocked_started.wait(1.0)
        assert len(recorder.updates) == 1
        first = recorder.updates[0]
        assert [row["source"] for row in first["local_segments"]] == [
            "First sentence.", " Second sentence",
        ]
        assert all(not row["translation"] for row in first["local_segments"])
    finally:
        factory.release.set()
        pipeline.close()


def test_multi_sentence_rows_upsert_immediately_and_period_to_comma_keeps_ids():
    factory = FakeTranslatorFactory()
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("Alpha. Beta. Gamma", False, "en", "en")
        first = recorder.updates[-1]["local_segments"]
        assert [row["source"] for row in first] == ["Alpha.", " Beta.", " Gamma"]
        ids = [row["id"] for row in first]

        pipeline.update("Alpha, Beta. Gamma", False, "en", "en")
        changed = recorder.updates[-1]["local_segments"]
        assert [row["source"] for row in changed] == ["Alpha,"]
        assert [row["id"] for row in changed] == ids[:1]
    finally:
        pipeline.close()


def test_pending_revisions_coalesce_while_older_useful_translation_is_published():
    factory = FakeTranslatorFactory(block_first=True)
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("alpha", False, "en", "ja")
        assert factory.blocked_started.wait(1.0)
        pipeline.update("alpha one", False, "en", "ja")
        pipeline.update("alpha two", True, "en", "ja")

        factory.release.set()
        wait_until(lambda: len(recorder.finals) == 1)
        with factory.lock:
            assert [call["source"] for call in factory.calls] == ["alpha", "alpha two"]
        translations = [row["translation"] for row in recorder.rows() if row["translation"]]
        assert translations[:2] == ["MT:alpha", "MT:alpha two"]
        assert recorder.finals[0]["translation"] == "MT:alpha two"
    finally:
        factory.release.set()
        pipeline.close()


def test_semantic_cut_finalizes_prefix_and_retains_suffix_identity_and_text():
    factory = FakeTranslatorFactory()
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("First sentence. Retained suffix", False, "en", "en")
        initial = recorder.updates[-1]["local_segments"]
        prefix_id, suffix_id = [row["id"] for row in initial]

        # The recognizer supplies its safe prefix as text and the exact known
        # replay suffix separately.  The prefix is finalized without clearing
        # the already displayed suffix row.
        pipeline.update(
            "First sentence.", True, "en", "en", semantic_suffix=" Retained suffix"
        )
        wait_until(lambda: len(recorder.finals) == 1)
        assert recorder.finals[0]["id"] == prefix_id
        assert recorder.finals[0]["source"] == "First sentence."

        pipeline.update(" Retained suffix revised", False, "en", "en")
        tail_update = recorder.updates[-1]["local_segments"]
        assert tail_update[0]["id"] == suffix_id
        assert tail_update[0]["source"] == " Retained suffix revised"
    finally:
        pipeline.close()


def test_multi_sentence_audio_cut_preserves_separate_rows_and_tail_identity():
    factory = FakeTranslatorFactory(block_first=True)
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("First sentence. Second sentence. Retained tail", False, "en", "ja")
        initial = recorder.updates[0]["local_segments"]
        ids = [row["id"] for row in initial]
        assert factory.blocked_started.wait(1.0)
        pipeline.update(
            "First sentence. Second sentence.", True, "en", "ja",
            semantic_suffix=" Retained tail",
        )
        pipeline.update(" Retained tail revised", True, "en", "ja")
        factory.release.set()
        wait_until(lambda: len(recorder.finals) == 3)
        assert [row["id"] for row in recorder.finals] == ids
        assert [row["source"] for row in recorder.finals] == [
            "First sentence.", " Second sentence.", " Retained tail revised",
        ]
        assert not recorder.errors
        assert not any(update.get("local_removed_segment_ids") for update in recorder.updates)
    finally:
        factory.release.set()
        pipeline.close()


def test_multiple_final_utterances_survive_a_slow_translation_chain():
    factory = FakeTranslatorFactory(block_first=True)
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("One.", True, "en", "ja")
        assert factory.blocked_started.wait(1.0)
        pipeline.update("Two.", True, "en", "ja")
        factory.release.set()
        wait_until(lambda: len(recorder.finals) == 2)
        assert [row["source"] for row in recorder.finals] == ["One.", "Two."]
        assert [row["translation"] for row in recorder.finals] == ["MT:One.", "MT:Two."]
    finally:
        factory.release.set()
        pipeline.close()


def test_each_row_gets_an_independent_translator_state():
    factory = FakeTranslatorFactory()
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("One. Two", False, "en", "ja")
        wait_until(lambda: len([row for row in recorder.rows() if row["translation"]]) == 2)
        with factory.lock:
            assert len(factory.instances) == 2
            assert [call["translator"] for call in factory.calls[:2]] == [0, 1]
    finally:
        pipeline.close()


def test_close_marks_tail_final_and_drains_then_closes_translator():
    factory = FakeTranslatorFactory(block_first=True)
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("Unfinished tail", False, "en", "ja")
        assert factory.blocked_started.wait(1.0)
        closing = threading.Thread(target=pipeline.close)
        closing.start()
        wait_until(lambda: any(row["is_final"] for row in recorder.rows()))
        factory.release.set()
        closing.join(3.0)
        assert not closing.is_alive()
        assert [row["source"] for row in recorder.finals] == ["Unfinished tail"]
        assert factory.instances[0].closed
    finally:
        factory.release.set()
        pipeline.close()


def test_removed_row_cannot_resurrect_stale_translation_output():
    factory = FakeTranslatorFactory(blocked_sources={" Keep"})
    recorder = Recorder()
    pipeline = make_pipeline(factory, recorder)
    try:
        pipeline.update("Old row. Keep", False, "en", "ja")
        first_rows = recorder.updates[-1]["local_segments"]
        removed_id = first_rows[1]["id"]
        assert factory.blocked_started.wait(1.0)

        pipeline.update("Old row.", False, "en", "ja")
        removal = recorder.updates[-1]
        assert removal["local_removed_segment_ids"] == [removed_id]
        factory.release.set()
        time.sleep(0.08)
        assert not any(
            row["id"] == removed_id and row["translation"]
            for row in recorder.rows()
        )
    finally:
        factory.release.set()
        pipeline.close()


def test_translation_can_be_disabled_or_skipped_for_the_same_language():
    for enabled, target_language in ((False, "ja"), (True, "en")):
        factory = FakeTranslatorFactory()
        recorder = Recorder()
        pipeline = make_pipeline(factory, recorder)
        try:
            pipeline.update("Visible source", False, "en", target_language, translation_enabled=enabled)
            source = recorder.updates[-1]["local_segments"][0]
            assert source["source"] == "Visible source"
            assert source["translation"] == ""
            assert not source["requires_translation"]
            wait_until(lambda: not pipeline._working)
            assert not factory.instances
        finally:
            pipeline.close()
