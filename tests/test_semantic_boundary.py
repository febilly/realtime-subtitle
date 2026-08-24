import pytest

from local_inference.semantic_boundary import (
    LocalAgreementBoundaryDetector,
    deduplicate_normalized_replay,
    deduplicate_replayed_overlap,
    has_safe_replay_evidence,
)


def test_local_agreement_two_requires_two_hypotheses_and_keeps_exact_suffix():
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe("Hello world. This") is None
    decision = detector.observe("Hello world. This is the next sentence")

    assert decision is not None
    assert decision.prefix == "Hello world."
    assert decision.suffix == " This is the next sentence"
    assert decision.boundary_index == len(decision.prefix)
    assert decision.prefix + decision.suffix == "Hello world. This is the next sentence"
    assert decision.agreement_count == 2


def test_terminal_punctuation_needs_right_context_in_both_agreement_updates():
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe("This still looks final.") is None
    assert detector.observe("This still looks final. But it was provisional") is None

    decision = detector.observe("This still looks final. But now it is stable")
    assert decision is not None
    assert decision.prefix == "This still looks final."


def test_detector_exposes_provisional_internal_boundary_for_fast_follow():
    detector = LocalAgreementBoundaryDetector(min_right_context_nonspace_chars=2)

    assert detector.observe("Still provisional.") is None
    assert not detector.has_provisional_boundary
    assert detector.observe("Still provisional. next") is None
    assert detector.has_provisional_boundary
    assert detector.observe("Still provisional. next grows") is not None
    assert not detector.has_provisional_boundary


def test_whitespace_is_not_right_context():
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe("Done.   ") is None
    assert detector.observe("Done.\t") is None
    assert detector.observe("Done. Next") is None
    assert detector.observe("Done. Next revision") is not None


def test_suffix_can_revise_while_the_prefix_stays_stable():
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe("First sentence. an unstable tail") is None
    decision = detector.observe("First sentence. a completely revised tail")

    assert decision is not None
    assert decision.prefix == "First sentence."
    assert decision.suffix == " a completely revised tail"


def test_earliest_of_multiple_stable_boundaries_is_returned():
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe("Ready? Yes! We continue") is None
    decision = detector.observe("Ready? Yes! We continue talking")

    assert decision is not None
    assert decision.prefix == "Ready?"
    assert decision.suffix == " Yes! We continue talking"


@pytest.mark.parametrize(
    "first,second",
    [
        ("The value is 3.14 and rising", "The value is 3.14 and rising fast"),
        ("Use e.g. this example", "Use e.g. this example here"),
        ("The speaker trails off... still talking", "The speaker trails off... still talking now"),
        ("「まだ。続いている」途中", "「まだ。続いている」途中です"),
    ],
)
def test_shared_segmentation_rules_reject_unsafe_periods(first, second):
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe(first) is None
    assert detector.observe(second) is None


def test_boundary_must_be_safe_at_the_same_index_in_every_hypothesis():
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe("The value is 3. Next") is None
    # The exact text prefix still matches, but the period is now part of 3.14.
    assert detector.observe("The value is 3.14 today") is None


def test_agreement_count_is_configurable():
    detector = LocalAgreementBoundaryDetector(agreement_count=3)

    assert detector.observe("One. two") is None
    assert detector.observe("One. two grows") is None
    decision = detector.observe("One. two grows again")

    assert decision is not None
    assert decision.prefix == "One."
    assert decision.agreement_count == 3


def test_minimum_right_context_is_configurable():
    detector = LocalAgreementBoundaryDetector(min_right_context_nonspace_chars=4)

    assert detector.observe("One. ab") is None
    assert detector.observe("One. abc") is None
    assert detector.observe("One. abcd") is None
    assert detector.observe("One. abcde") is not None


def test_detector_latches_until_reset():
    detector = LocalAgreementBoundaryDetector()

    assert detector.observe("One. next") is None
    assert detector.observe("One. next grows") is not None
    assert detector.latched
    assert detector.observe("One. next grows again") is None

    detector.reset()
    assert not detector.latched
    assert detector.observe("Two! suffix") is None
    decision = detector.observe("Two! suffix grows")
    assert decision is not None
    assert decision.prefix == "Two!"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"agreement_count": 1},
        {"min_prefix_nonspace_chars": 0},
        {"min_right_context_nonspace_chars": 0},
    ],
)
def test_detector_rejects_unsafe_configuration(kwargs):
    with pytest.raises(ValueError):
        LocalAgreementBoundaryDetector(**kwargs)


@pytest.mark.parametrize("prefix", ["Hello.", "大家好。"])
def test_detector_does_not_commit_prefix_that_cannot_be_replay_deduplicated(prefix):
    detector = LocalAgreementBoundaryDetector(
        agreement_count=2,
        min_right_context_nonspace_chars=2,
        require_safe_replay_evidence=True,
    )
    hypothesis = prefix + " Next sentence"

    assert detector.observe(hypothesis) is None
    assert detector.observe(hypothesis) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hello.", False),
        ("Hello world.", True),
        ("Déjà.", False),
        ("De\u0301ja\u0300.", False),
        ("Déjà vu.", True),
        ("Привет.", False),
        ("Привет мир.", True),
        ("大家好。", False),
        ("欢迎大家。", True),
        ("안녕하세요.", True),
    ],
)
def test_safe_replay_evidence_matches_dedup_threshold(text, expected):
    assert has_safe_replay_evidence(text) is expected


def test_deduplicate_english_multiword_overlap():
    assert deduplicate_replayed_overlap(
        "We saw the blue car.",
        "the blue car. It left quickly.",
    ) == "It left quickly."


def test_deduplicate_unspaced_chinese_overlap():
    assert deduplicate_replayed_overlap(
        "这是第一句话。第二句话",
        "第二句话还没说完",
    ) == "还没说完"


def test_deduplicate_chooses_the_longest_safe_overlap():
    assert deduplicate_replayed_overlap(
        "alpha beta gamma",
        "beta gamma delta",
    ) == "delta"


@pytest.mark.parametrize(
    "committed,replayed",
    [
        ("I like cats", "cats are nice"),  # one common word is ambiguous
        ("我喜欢你", "你好吗"),  # one common Han character is ambiguous
        ("forecast", "cast away"),  # match starts in the middle of a word
        ("talk !!!", "!!! keep going"),  # punctuation carries no lexical evidence
        ("Case Sensitive", "sensitive text"),  # case revisions are not exact evidence
    ],
)
def test_deduplicate_is_conservative_for_ambiguous_overlap(committed, replayed):
    assert deduplicate_replayed_overlap(committed, replayed) == replayed


def test_deduplicate_preserves_original_text_when_there_is_no_overlap():
    replayed = "  entirely new text"
    assert deduplicate_replayed_overlap("old sentence", replayed) == replayed


def test_deduplicate_thresholds_can_be_relaxed_explicitly():
    assert deduplicate_replayed_overlap(
        "I like cats",
        "cats are nice",
        min_word_tokens=1,
    ) == "are nice"


def test_known_replay_dedup_allows_cjk_punctuation_revision():
    assert deduplicate_normalized_replay(
        "不要问你的国家能为你的国家做什么。",
        "国家做什么？不要问下一句",
    ) == "不要问下一句"


def test_known_replay_dedup_allows_english_case_and_punctuation_revision():
    assert deduplicate_normalized_replay(
        "We saw the blue car.",
        "The blue car? It left.",
    ) == "It left."


def test_known_replay_dedup_keeps_short_legitimate_repetition():
    assert deduplicate_normalized_replay("我喜欢你", "你好吗") == "你好吗"


def test_known_replay_dedup_never_deletes_an_entire_identical_utterance():
    repeated = "No preguntes qué puede hacer tu país por ti."
    assert deduplicate_normalized_replay(repeated, repeated) == repeated


@pytest.mark.parametrize(
    "committed,replayed",
    [
        ("We broadcast live now.", "cast live now begins here"),
        ("We finished alpha broad.", "alpha broadcaster arrived"),
        ("I don't know.", "t know. Next sentence"),
        ("I don’t know.", "t know. Next sentence"),
        ("I reviewed résumé noir.", "sumé noir. Next sentence"),
        (
            "I reviewed re\u0301sume\u0301 noir.",
            "sume\u0301 noir. Next sentence",
        ),
    ],
)
def test_known_replay_dedup_does_not_match_inside_spaced_words(committed, replayed):
    assert deduplicate_normalized_replay(committed, replayed) == replayed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_overlap_chars": 0},
        {"min_cjk_chars": 0},
        {"min_word_tokens": 0},
    ],
)
def test_deduplicate_rejects_unsafe_configuration(kwargs):
    with pytest.raises(ValueError):
        deduplicate_replayed_overlap("alpha beta", "alpha beta", **kwargs)
