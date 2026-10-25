"""Replay anonymized real Soniox recordings through the backend segmentation.

Fixtures live in tests/fixtures/segmentation-replay/ and are cut from real
recordings by ``tools/segmentation_replay.py make-fixture`` (text randomized,
structure and timing kept). Each one is replayed with the recorded clock and
must reproduce its expected backend output exactly; the frontend half of the
check (tests/frontend/segmentation-replay.test.js) consumes the same expected
files.

After an INTENDED segmentation change, regenerate the expected outputs:

    python tools/segmentation_replay.py update-golden

and review the diff of tests/fixtures/segmentation-replay/*.expected.jsonl.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import segmentation_replay  # noqa: E402
from test_soniox_session_response import _install_soniox_session_import_mocks  # noqa: E402

FIXTURES = segmentation_replay.list_fixtures()


def test_fixtures_exist():
    assert FIXTURES, "no segmentation replay fixtures found"


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_replays_to_expected_output(monkeypatch, name):
    _install_soniox_session_import_mocks(monkeypatch)
    import soniox_session as module

    meta, records = segmentation_replay.load_fixture(name)
    frames, errors = segmentation_replay.replay(module, records, meta["config"])

    assert not errors, errors[:3]
    assert segmentation_replay.check_text_conservation(records, frames) is None
    actual = segmentation_replay.golden_frames(frames)
    expected = segmentation_replay.load_expected(name)
    if actual != expected:
        mismatch = next(
            (i for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
            min(len(actual), len(expected)),
        )
        pytest.fail(
            f"{name}: backend output differs from expected at frame {mismatch} "
            f"(actual {len(actual)} frames, expected {len(expected)}).\n"
            f"actual:   {actual[mismatch] if mismatch < len(actual) else '<end>'}\n"
            f"expected: {expected[mismatch] if mismatch < len(expected) else '<end>'}\n"
            "If the change is intended: python tools/segmentation_replay.py update-golden"
        )


def test_anonymize_text_keeps_segmentation_signal():
    rng = segmentation_replay.random.Random(1)
    out = segmentation_replay.anonymize_text(' Hello, 3.5 "world"... 你好。いいね！', rng)
    assert len(out) == len(' Hello, 3.5 "world"... 你好。いいね！')
    # Punctuation and whitespace survive in place; letters/digits do not.
    for original, replaced in zip(' Hello, 3.5 "world"... 你好。いいね！', out):
        if original.isalnum():
            assert replaced.isalnum()
        else:
            assert replaced == original
    assert "Hello" not in out and "world" not in out and "你好" not in out
    assert segmentation_replay.anonymize_text("<end>", rng) == "<end>"
    assert segmentation_replay.anonymize_text(" うん", rng) == " うん"
